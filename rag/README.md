# DW2 Russian RAG

RAG-хранилище переводов DW2 на PostgreSQL и `pgvector`. Оно используется
основным скриптом `batch_translate.py` для поиска похожих переводов перед
обращением к LLM.

## Что делает batch_translate.py

При обычном запуске с настроенным PostgreSQL скрипт автоматически:

1. применяет недостающие миграции базы данных;
2. собирает справочники переводов из версий, указанных в `--cache-from`;
3. добавляет или обновляет эти справочники в таблице `translations`;
4. собирает английские тексты целевой версии, отсутствующие в `--cache-from`;
5. регистрирует такие тексты с пустым `russian`;
6. находит все строки без `embedding` и создаёт embedding пакетами;
7. сохраняет результат после каждой пачки, поэтому обработку можно продолжить;
8. выгружает embedding- и LLM-модели из LM Studio;
9. автоматически запускает перевод с LLM.

Существующие embedding не удаляются и не пересчитываются. При повторном запуске
обрабатываются только строки, где `embedding IS NULL`.

## Требования

- PostgreSQL с расширением `vector` (pgvector);
- Python-пакеты `psycopg`, `numpy`, `openai`, `lmstudio`, `keyboard`;
- запущенный LM Studio;
- запущенный LM Studio с доступным embeddings API;
- embedding-модель и LLM, которые LM Studio может загрузить по имени.

Установка зависимостей:

```powershell
pip install "psycopg[binary]" numpy openai lmstudio keyboard
```

## Настройка PostgreSQL

Обязательная переменная:

```powershell
$env:DW2_PG_PASSWORD = "your_password"
```

Необязательные переменные:

```powershell
$env:DW2_PG_HOST = "localhost"
$env:DW2_PG_PORT = "5432"
$env:DW2_PG_DB = "dw2russian"
$env:DW2_PG_USER = "postgres"
```

Если `DW2_PG_PASSWORD` не задан, перевод продолжится без автоматической работы
с RAG и PostgreSQL.

## Настройка LM Studio

```powershell
$env:LM_STUDIO_API_BASE = "http://localhost:1234/v1"
$env:LM_STUDIO_API_KEY = "none"
$env:EMBEDDING_MODEL = "text-embedding-qwen3-embedding-8b"
```

`LM_STUDIO_API_BASE` используется для OpenAI-совместимого embeddings API.
Для выгрузки модели скрипт обращается к native endpoint LM Studio:

```text
POST http://localhost:1234/api/v1/models/unload
```

с телом:

```json
{"instance_id": "text-embedding-qwen3-embedding-8b"}
```

Перед использованием embedding или LLM скрипт сначала выгружает известные модели
из LM Studio. Затем OpenAI-compatible embeddings API или `lmstudio.llm(...)`
загружает нужную модель автоматически. Если выгрузка не удалась, перевод не
запускается.

## Основной запуск

```powershell
python batch_translate.py --all `
    --target-version 1.3.6.6 `
    --cache-from 1.3.6.3
```

`--cache-from` может содержать несколько версий. Они указываются от старой к
новой:

```powershell
python batch_translate.py --all `
    --target-version 1.3.6.6 `
    --cache-from 1.3.4.3 1.3.5.7 1.3.6.3
```

Перед началом embedding скрипт автоматически освобождает память LM Studio.
После окончания embedding-модель выгружается, затем память снова очищается перед
загрузкой LLM. Ручные подтверждения не требуются.

## Проверка RAG

Для проверки похожих переводов без запуска обычного перевода:

```powershell
python batch_translate.py `
    --target-version 1.3.6.6 `
    --cache-from 1.3.6.3 `
    --check-rag "Privateering Agreement"
```

Режим выводит найденные английские и русские пары, версию источника и
`similarity`. Он требует `DW2_PG_PASSWORD`.

Для произвольного текста, которого ещё нет в базе, диагностический режим
автоматически использует embedding API после очистки памяти LM Studio.

## Ручные команды RAG

Показать статус и применить миграции:

```powershell
python rag/rag.py migrate --status
python rag/rag.py migrate
```

Найти похожие переводы в уже подготовленной базе:

```powershell
python rag/rag.py search "Ancient Shakturi Advance Ship" --top-k 5
python rag/rag.py search "ship hull" --category ship
python rag/rag.py search "lore text" --no-hybrid
```

Сформировать RAG-промпт:

```powershell
python rag/rag.py prompt "Ancient Shakturi Advance Ship" --top-k 3
```

Команды `search` и `prompt` автоматически дозаполняют отсутствующие embedding.
Во время обычного перевода embedding для запроса не генерируется: используется
заранее сохранённый embedding target-фразы, поэтому embedding-модель не нужна
одновременно с LLM.

## Миграции

Версия схемы хранится в таблице `schema_migrations`. Миграции применяются по
порядку при запуске `batch_translate.py` и при вызове `migrate`.

| Версия | Файл | Назначение |
|---:|---|---|
| 1 | `001_initial.sql` | `translations`, pgvector, базовые индексы и представления |
| 2 | `002_en_search_and_indexes.sql` | английский полнотекстовый поиск и дополнительные индексы |
| 3 | `003_unique_translation_pairs.sql` | уникальность `(source_version, english)` для безопасного upsert |

Например, если база имеет версию 1, а код ожидает версию 3, будут применены
миграции 2 и 3.

## Таблица translations

Основные поля:

```text
english        — исходный английский текст
russian        — русский перевод; пустое значение означает pending-фразу
source_version — версия справочника
source_file    — источник записи
category       — категория перевода
xml_tag        — контекст XML-тега
embedding      — halfvec(4000)
```

Справочные записи имеют русский перевод и участвуют в RAG. Pending-записи с
пустым `russian` используются для подготовки embedding, но исключаются из
результатов поиска.

## Приоритет перевода

В `translate.py` используется следующий порядок:

1. локальный cache конкретного файла;
2. glossary из `system_prompt.py`;
3. общий cache предыдущих версий;
4. RAG-примеры из PostgreSQL;
5. LLM.

Технические строки возвращаются без изменений и не отправляются в LLM.
