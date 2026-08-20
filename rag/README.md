# DW2 Russian RAG — PostgreSQL Knowledge Base

RAG (Retrieval Augmented Generation) система для улучшения переводов DW2 через PostgreSQL + векторный поиск.

## Возможности

- **Загрузка переводов** из English/Russian директорий в PostgreSQL
- **Векторный поиск** похожих переводов через pgvector
- **Гибридный поиск** (векторы + полнотекстовый поиск)
- **RAG-промпты** — автоматическая подстановка релевантных примеров в запрос к LLM
- **Категоризация** переводов (ship, event, lore, research, etc.)

## Установка

### 1. PostgreSQL + pgvector

Убедитесь, что PostgreSQL установлен и запущен. Миграции создадут расширение `vector` автоматически.

Для установки pgvector см.: https://github.com/pgvector/pgvector

### 2. Python зависимости

```bash
pip install psycopg2-binary numpy openai lmstudio
```

### 3. Настройка подключения

```bash
# Пароль для PostgreSQL
export DW2_PG_PASSWORD="your_password"

# (Опционально) Модель для эмбеддингов
export EMBEDDING_MODEL="qwen/qwen3.6-35b-a3b"

# (Опционально) LM Studio API
export LM_STUDIO_API_BASE="http://localhost:1234/v1"
```

## Использование

### Инициализация базы

Миграции применяются автоматически при первой загрузке данных. Для ручной инициализации:

```bash
# Показать статус миграций
python rag/rag.py migrate --status

# Применить все доступные миграции
python rag/rag.py migrate

# Применить до конкретной версии
python rag/rag.py migrate --target 2
```

> **Примечание:** Старый `schema.sql` больше не нужен — все DDL-запросы перенесены в `migrations/`.

### Миграции

Система автоматического версионирования схемы базы данных.

| Версия | Файл | Описание |
|--------|------|----------|
| 1 | `001_initial.sql` | Таблица `translations`, индексы, представления |
| 2 | `002_en_search_and_indexes.sql` | Англоязычный поиск, индексы по файлам/категориям |

**Как это работает:**

1. При первом запуске создаётся таблица `schema_migrations`
2. При загрузке данных (`load`) или вручную (`migrate`) проверяется текущая версия
3. Применяются все недостающие миграции по порядку
4. Если в БД версия 1, а в коде версия 3 → применяются 002 и 003

**Пример:**

```bash
# Текущая версия БД: 1, в коде: 2
# Будет применена миграция 002
python rag/rag.py migrate

# После миграции:
python rag/rag.py migrate --status
# → База данных актуальна. Миграции не требуются.
```

### Загрузка переводов

```bash
# Загрузить переводы из версии 1.3.6.6
python rag/rag.py load \
    --english-dir 1.3.6.6/English \
    --russian-dir 1.3.6.6/Russian \
    --version 1.3.6.6

# Без генерации эмбеддингов (быстрее)
python rag/rag.py load \
    --english-dir 1.3.6.6/English \
    --russian-dir 1.3.6.6/Russian \
    --version 1.3.6.6 \
    --no-embeddings

# С указанием модели для эмбеддингов
python rag/rag.py load \
    --english-dir 1.3.6.6/English \
    --russian-dir 1.3.6.6/Russian \
    --version 1.3.6.6 \
    --embedding-model "Qwen/Qwen2.5-7B-Instruct" \
    --api-base "http://localhost:1234/v1"
```

### Поиск похожих переводов

```bash
# Поиск по тексту
python rag/rag.py search "Ancient Shakturi Advance Ship" --top-k 5

# Поиск с фильтром по категории
python rag/rag.py search "ship hull" --category ship

# Только векторный поиск (без текстового)
python rag/rag.py search "lore text" --no-hybrid
```

### Просмотр статистики

```bash
python rag/rag.py stats
```

### Генерация RAG-промпта

```bash
# Сформировать промпт с примерами
python rag/rag.py prompt "Ancient Shakturi Advance Ship" --top-k 3

# Сохранить промпт в файл
python rag/rag.py prompt "Ancient Shakturi Advance Ship" --output /tmp/rag_prompt.txt
```

## Интеграция с batch_translate.py

### Вариант 1: Прямая интеграция

Добавить RAG-поиск в `translate.py` перед вызовом LLM:

```python
# В translate.py, функция translate_text()
from rag.rag import search_similar_translations, build_rag_prompt

def translate_text(text: str) -> str:
    # ... существующая логика кэшей ...
    
    # RAG: поиск релевантных примеров
    similar = search_similar_translations(text, top_k=3)
    rag_prompt = build_rag_prompt(text, similar)
    
    # Вызов LLM с RAG-промптом
    chat = lms.Chat(rag_prompt)
    chat.add_user_message(f"Text: {text}")
    result = model.respond(chat, config={"temperature": 0.1, "maxTokens": 1024*10})
    
    return result.content.strip()
```

### Вариант 2: Через переменную окружения

```bash
# Включить RAG-поиск
export DW2_RAG_ENABLED=1
export DW2_RAG_TOP_K=5

# Запуск перевода с RAG
python batch_translate.py --all --target-version 1.3.6.6 --cache-from 1.3.6.3
```

## Структура базы данных

```
translations
├── id            — уникальный идентификатор
├── english       — английский текст
├── russian       — русский перевод
├── source_file   — путь к файлу (DW2/ShipHulls.xml)
├── source_version — версия игры (1.3.6.6)
├── category      — категория (ship, event, lore, etc.)
├── xml_tag       — XML тег контекста
├── embedding     — векторное представление (vector(1024))
├── created_at    — дата создания
└── updated_at    — дата обновления
```

## Категории переводов

| Категория | Файлы |
|-----------|-------|
| `lore` | Galactopedia, lore файлы |
| `event` | GameEvents*.xml |
| `ship` | ShipHulls*.xml |
| `unit` | TroopDefinitions*.xml |
| `research` | ResearchProjectDefinitions.xml |
| `race` | Races.xml, Governments*.xml |
| `tour` | TourItems.xml |
| `spaceitem` | SpaceItemDefinitions.xml |
| `resource` | Resources.xml |
| `facility` | PlanetaryFacilityDefinitions*.xml |
| `dlc` | DLC файлы |
| `general` | Остальные файлы |

## Архитектура

```
┌─────────────────────────────────────────────────────┐
│                  batch_translate.py                  │
│                      translate.py                    │
│                    translate_text()                  │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│              RAG Retrieval Layer                     │
│  ┌──────────────┐  ┌──────────────────────────┐    │
│  │ Vector Search │  │ Full-Text Search (GIN)  │    │
│  │ (pgvector)    │  │ (tsvector)              │    │
│  └──────┬───────┘  └──────────┬───────────────┘    │
│         └──────────┬───────────┘                     │
│                    ▼                                 │
│          build_rag_prompt()                          │
│     (adds examples to SYSTEM_PROMPT)                 │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│              PostgreSQL (dw2russian)                 │
│  ┌──────────────────────────────────────────────┐   │
│  │  translations table                          │   │
│  │  - english, russian, category, embedding     │   │
│  │  - IVFFlat index on embedding                │   │
│  │  - GIN index on english (tsvector)           │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│              Local LLM (Qwen)                        │
│         Получает промпт с примерами                  │
└─────────────────────────────────────────────────────┘
```
