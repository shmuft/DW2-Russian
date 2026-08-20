# DW2 Russian RAG — Скилл работы с БД и миграциями

## Обзор

Этот скилл описывает работу с PostgreSQL базой данных `dw2russian` и системой миграций для RAG-системы переводов DistantWorlds 2.

## Структура

```
rag/
├── migrations/
│   ├── README.md              # Документация по миграциям
│   ├── 001_initial.sql        # Версия 1: базовая схема
│   └── 002_en_search_and_indexes.sql  # Версия 2: доп. индексы
├── rag.py                     # Основная логика + миграции
├── README.md                  # Общая документация
└── requirements.txt           # Зависимости
```

## Текущая версия схемы

`CURRENT_SCHEMA_VERSION = 2` (определено в `rag.py`)

## Миграции

### Добавление новой миграции

1. Создайте файл `NNN_description.sql` в `rag/migrations/`
2. Номер определяет порядок применения
3. SQL должен быть **идемпотентным**:
   - Используйте `CREATE TABLE IF NOT EXISTS`
   - Используйте `CREATE INDEX IF NOT EXISTS`
   - Для изменений: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
   - Для удаления: `DROP TABLE IF EXISTS`
4. Не используйте `DROP TABLE` без `IF EXISTS`

### Применение миграций

```bash
# Показать статус
python rag/rag.py migrate --status

# Применить все доступные
python rag/rag.py migrate

# Применить до конкретной версии
python rag/rag.py migrate --target 2
```

### Автоматическое применение

Миграции применяются автоматически при:
- Загрузке данных: `python rag/rag.py load ...`
- Ручном запуске: `python rag/rag.py migrate`

## Работа с БД

### Подключение

```python
from rag.rag import get_db_connection, DEFAULT_DB_CONFIG

conn = get_db_connection({
    "host": "localhost",
    "port": 5432,
    "dbname": "dw2russian",
    "user": "postgres",
    "password": os.environ.get("DW2_PG_PASSWORD", ""),
})
```

### Основные функции

| Функция | Описание |
|---------|----------|
| `migrate_db()` | Применяет миграции |
| `load_translations_to_db()` | Загружает переводы из файлов |
| `search_similar_translations()` | Векторный поиск |
| `build_rag_prompt()` | Формирует промпт с примерами |
| `get_db_stats()` | Статистика по БД |

### CLI команды

```bash
# Миграции
python rag/rag.py migrate [--status] [--target N]

# Загрузка данных
python rag/rag.py load --english-dir ... --russian-dir ... --version ...

# Поиск
python rag/rag.py search "текст" [--top-k N] [--category cat]

# Статистика
python rag/rag.py stats

# Промпт
python rag/rag.py prompt "текст" [--output file.txt]
```

## Схема базы данных

### Таблица `translations`

| Колонка | Тип | Описание |
|---------|-----|----------|
| id | SERIAL | Первичный ключ |
| english | TEXT | Английский текст |
| russian | TEXT | Русский перевод |
| source_file | TEXT | Путь к файлу |
| source_version | TEXT | Версия игры |
| category | TEXT | Категория (ship, event, lore...) |
| xml_tag | TEXT | XML тег контекста |
| embedding | vector(1024) | Векторное представление |
| created_at | TIMESTAMP | Дата создания |
| updated_at | TIMESTAMP | Дата обновления |

### Таблица `schema_migrations`

| Колонка | Тип | Описание |
|---------|-----|----------|
| version | INTEGER | Номер версии (PK) |
| applied_at | TIMESTAMP | Дата применения |
| description | TEXT | Описание миграции |

### Индексы

- `idx_translations_embedding` — IVFFlat для векторного поиска
- `idx_translations_english_gin` — GIN для полнотекстового поиска (русский)
- `idx_translations_english_en_gin` — GIN для английского (версия 2+)
- `idx_translations_source_file` — по файлу (версия 2+)
- `idx_translations_category` — по категории (версия 2+)
- `idx_translations_source_version` — по версии (версия 2+)

### Представления

- `v_translation_stats` — статистика по категориям и версиям
- `v_top_categories` — топ категорий по объёму
- `v_translation_coverage` — покрытие переводов (версия 2+)

## Зависимости

```bash
pip install "psycopg[binary]" numpy openai
```

> **Важно:** Используется **psycopg3** (не psycopg2!). Пакет называется `psycopg[binary]`.

## psycopg3 — ключевые отличия от psycopg2

### Импорт
```python
# psycopg2
import psycopg2
import psycopg2.extras

# psycopg3
import psycopg
import psycopg.rows
import psycopg.sql
```

### Подключение
```python
# psycopg2
conn = psycopg2.connect(host='...', dbname='...', user='...', password='...')

# psycopg3
conn = psycopg.connect(host='...', dbname='...', user='...', password='...')
```

### Курсоры
```python
# psycopg2 — RealDictCursor
cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# psycopg3 — dict_row
cursor = conn.cursor(row_factory=psycopg.rows.dict_row)
```

### Выполнение запросов
```python
# psycopg2
cursor.execute("SELECT * FROM table WHERE id = %s", (1,))
rows = cursor.fetchall()

# psycopg3 — то же самое, но можно использовать connection.execute() для простых запросов
conn.execute("CREATE TABLE IF NOT EXISTS test (id INT)")
conn.commit()
```

### Безопасное построение SQL
```python
# psycopg.sql для безопасной подстановки идентификаторов
from psycopg.sql import Identifier, Literal, SQL

query = psycopg.sql.SQL("SELECT * FROM {} WHERE name = {}").format(
    Identifier("table_name"),
    Literal("value")
)
cursor.execute(query)
```

### Контекстный менеджер
```python
# psycopg3 поддерживает with для транзакций
with conn:
    conn.execute("INSERT INTO table VALUES (1)")
    # автоматически commit при успехе, rollback при ошибке
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| DW2_PG_PASSWORD | Пароль PostgreSQL | "" |
| EMBEDDING_MODEL | Модель для эмбеддингов | qwen/qwen3.6-35b-a3b |
| LM_STUDIO_API_BASE | URL LM Studio API | http://localhost:1234/v1 |
| LM_STUDIO_API_KEY | API ключ LM Studio | none |

## Частые операции

### Проверить статус миграций
```bash
python rag/rag.py migrate --status
```

### Применить все миграции
```bash
python rag/rag.py migrate
```

### Загрузить данные с авто-миграцией
```bash
python rag/rag.py load \
    --english-dir 1.3.6.6/English \
    --russian-dir 1.3.6.6/Russian \
    --version 1.3.6.6
```

### Посмотреть статистику
```bash
python rag/rag.py stats
```

### Поиск похожих переводов
```bash
python rag/rag.py search "ship hull" --category ship --top-k 5
```
