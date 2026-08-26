"""
RAG (Retrieval Augmented Generation) для DW2 перевода.

Загружает переводы из PostgreSQL и возвращает релевантные примеры
для подстановки в промпт LLM.
"""

import os
import sys
import json
import argparse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Проверяем зависимости
try:
    import psycopg
    import psycopg.rows
    import psycopg.sql
except ImportError:
    print("Ошибка: psycopg3 не установлен. Установите: pip install psycopg[binary]")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("Ошибка: numpy не установлен. Установите: pip install numpy")
    sys.exit(1)


# ============================================================================
# Конфигурация подключения
# ============================================================================

DEFAULT_DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "dw2russian",
    "user": "postgres",
    "password": os.environ.get("DW2_PG_PASSWORD", ""),
}

# Размер эмбеддинга — должен совпадать с моделью, которая генерирует эмбеддинги
# Для qwen/qwen3.6-35b-a3b обычно 1024 или 4096
DEFAULT_EMBEDDING_DIM = 1024


def get_db_connection(db_config: Optional[dict] = None):
    """Создаёт подключение к PostgreSQL (psycopg3)."""
    config = db_config or DEFAULT_DB_CONFIG
    if not config.get("password"):
        print("Ошибка: укажите DW2_PG_PASSWORD в环境变量 или передайте db_config")
        sys.exit(1)
    return psycopg.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["dbname"],
        user=config["user"],
        password=config["password"],
    )


# ============================================================================
# Система миграций базы данных
# ============================================================================

# Путь к директории миграций (относительно rag.py)
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Текущая версия схемы (должна совпадать с номером последней миграции)
CURRENT_SCHEMA_VERSION = 3


def _get_current_db_version(conn) -> int:
    """Возвращает текущую версию схемы в базе данных. Создаёт таблицу если нужно."""
    cursor = conn.cursor()
    
    # Создаём таблицу версий если не существует
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            description TEXT
        )
    """)
    conn.commit()
    
    # Получаем текущую максимальную версию
    cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
    result = cursor.fetchone()
    return result[0] if result else 0


def _get_available_migrations() -> list[tuple[int, str]]:
    """
    Возвращает список доступных миграций в порядке возрастания.
    
    Returns:
        Список кортежей (version, file_path)
    """
    if not _MIGRATIONS_DIR.exists():
        return []
    
    migrations = []
    for file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        # Формат имени: NNN_description.sql
        name = file.stem
        parts = name.split("_", 1)
        if len(parts) >= 1 and parts[0].isdigit():
            version = int(parts[0])
            migrations.append((version, str(file)))
    
    return migrations


def _apply_migration(cursor, file_path: str, description: str = ""):
    """Применяет одну миграцию."""
    print(f"  [APPLYING] {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        sql = f.read()
    
    # Выполняем SQL
    cursor.execute(sql)
    
    # Записываем версию
    cursor.execute(
        "INSERT INTO schema_migrations (version, description) VALUES (%s, %s)",
        (int(Path(file_path).stem.split("_")[0]), description)
    )
    
    print(f"  [DONE]    {file_path}")


def migrate_db(
    db_config: Optional[dict] = None,
    target_version: int = None,
    show_status: bool = False,
) -> dict:
    """
    Применяет миграции базы данных.
    
    Args:
        db_config: Конфигурация подключения к БД
        target_version: Применить миграции до этой версии (по умолчанию — все)
        show_status: Только показать текущую версию без применения
    
    Returns:
        Словарь с результатами миграции
    """
    if target_version is None:
        target_version = CURRENT_SCHEMA_VERSION
    
    conn = get_db_connection(db_config)
    
    try:
        current_version = _get_current_db_version(conn)
        available = _get_available_migrations()
        
        if show_status:
            print(f"\n{'=' * 60}")
            print("DW2 Russian RAG — Статус миграций")
            print(f"{'=' * 60}")
            print(f"Текущая версия в БД:    {current_version}")
            print(f"Целевая версия в коде:   {target_version}")
            print(f"Доступно миграций:       {len(available)}")
            print()
            
            if current_version < target_version:
                pending = [(v, p) for v, p in available if v > current_version and v <= target_version]
                if pending:
                    print(f"Требуется применить {len(pending)} миграций:")
                    for v, p in pending:
                        desc = Path(p).stem.replace(f"{v}_", "", 1).replace("_", " ").title()
                        print(f"  {v}. {desc}")
                else:
                    print("Нет доступных миграций для применения.")
            elif current_version == target_version:
                print("✓ База данных актуальна. Миграции не требуются.")
            else:
                print(f"⚠ Внимание: версия БД ({current_version}) выше целевой ({target_version}).")
                print("  Возможно, требуется ручное обновление.")
            
            print(f"{'=' * 60}\n")
            
            return {
                "current_version": current_version,
                "target_version": target_version,
                "migrations_applied": 0,
                "status": "up-to-date" if current_version >= target_version else "pending",
            }
        
        # Применяем миграции
        pending = [(v, p) for v, p in available if v > current_version and v <= target_version]
        
        if not pending:
            print("✓ База данных актуальна. Миграции не требуются.")
            return {
                "current_version": current_version,
                "target_version": target_version,
                "migrations_applied": 0,
                "status": "up-to-date",
            }
        
        print(f"\n{'=' * 60}")
        print("DW2 Russian RAG — Применение миграций")
        print(f"{'=' * 60}")
        print(f"Текущая версия: {current_version} → Целевая: {target_version}")
        print(f"Будет применено {len(pending)} миграций:\n")
        
        cursor = conn.cursor()
        applied = 0
        
        for version, file_path in pending:
            try:
                desc = Path(file_path).stem.replace(f"{version}_", "", 1).replace("_", " ").title()
                _apply_migration(cursor, file_path, desc)
                conn.commit()
                applied += 1
            except Exception as e:
                conn.rollback()
                print(f"  [ERROR] Ошибка при применении миграции {version}: {e}")
                print(f"  [INFO]  Транзакция откатана. База данных не изменена.")
                return {
                    "current_version": current_version,
                    "target_version": target_version,
                    "migrations_applied": applied,
                    "status": "error",
                    "error": str(e),
                }
        
        conn.close()
        
        print(f"\n{'=' * 60}")
        print(f"✓ Миграции применены успешно!")
        print(f"  Применено: {applied}")
        print(f"  Новая версия: {target_version}")
        print(f"{'=' * 60}\n")
        
        return {
            "current_version": current_version,
            "target_version": target_version,
            "migrations_applied": applied,
            "new_version": target_version,
            "status": "success",
        }
        
    except Exception as e:
        conn.close()
        raise


# ============================================================================
# Генерация эмбеддингов
# ============================================================================

def generate_embedding(text: str, model: str = None, api_base: str = None) -> list[float]:
    """
    Генерирует эмбеддинг для текста через локальную модель.
    
    Поддерживаемые бэкенды:
    - LM Studio (OpenAI-совместимый API) — по умолчанию
    - Прямой вызов через lmstudio-python
    
    Args:
        text: Текст для генерации эмбеддинга
        model: Имя модели эмбеддингов (например, 'text-embedding-qwen3-embedding-8b')
        api_base: URL LM Studio API (например, 'http://localhost:1234/v1')
    
    Returns:
        Список float-значений эмбеддинга
    """
    # Пытаемся через OpenAI-совместимый API (LM Studio)
    api_base = api_base or os.environ.get("LM_STUDIO_API_BASE", "http://localhost:1234/v1")
    model = model or os.environ.get("EMBEDDING_MODEL", "text-embedding-qwen3-embedding-8b")
    
    api_key = os.environ.get("LM_STUDIO_API_KEY", "none")
    
    try:
        from openai import OpenAI
        client = OpenAI(base_url=api_base, api_key=api_key)
        
        response = client.embeddings.create(
            model=model,
            input=text,
            encoding_format="float"
        )
        
        embedding = response.data[0].embedding[:4000]
        return embedding
        
    except Exception as e:
        print(f"[WARNING] Не удалось сгенерировать эмбеддинг через OpenAI API: {e}")
        print(f"[INFO] Попробуйте запустить LM Studio с включённым Embeddings API")
        return None


def generate_embeddings_batch(texts: list[str], model: str = None, api_base: str = None) -> Optional[list[list[float]]]:
    """
    Генерирует эмбеддинги для списка текстов одним API-вызовом.

    Возвращает список списков float в том же порядке, что и входной `texts`.
    """
    if not texts:
        return []

    api_base = api_base or os.environ.get("LM_STUDIO_API_BASE", "http://localhost:1234/v1")
    model = model or os.environ.get("EMBEDDING_MODEL", "text-embedding-qwen3-embedding-8b")
    api_key = os.environ.get("LM_STUDIO_API_KEY", "none")

    try:
        from openai import OpenAI
        client = OpenAI(base_url=api_base, api_key=api_key)

        response = client.embeddings.create(
            model=model,
            input=texts,
            encoding_format="float",
        )

        embeddings: list[list[float]] = []
        for item in response.data:
            embedding = getattr(item, "embedding", None)
            if embedding is None and isinstance(item, dict):
                embedding = item.get("embedding")
            if embedding is None:
                continue

            embeddings.append(list(embedding)[:4000])

        if len(embeddings) != len(texts):
            print(f"[WARNING] Сгенерировано {len(embeddings)} эмбеддингов для {len(texts)} текстов")
            return None

        return embeddings
    except Exception as e:
        print(f"[WARNING] Не удалось сгенерировать пачку эмбеддингов через OpenAI API: {e}")
        return None


def _native_api_endpoint(path: str, api_base: str = None) -> str:
    """Строит URL native API LM Studio из OpenAI-compatible base URL."""
    api_base = api_base or os.environ.get("LM_STUDIO_API_BASE", "http://localhost:1234/v1")
    api_root = api_base.rstrip("/")
    if api_root.endswith("/api/v1"):
        return f"{api_root}/{path.lstrip('/')}"
    if api_root.endswith("/v1"):
        api_root = api_root[:-3]
    return f"{api_root}/api/v1/{path.lstrip('/')}"


def unload_model(model: str, api_base: str = None) -> bool:
    """Выгружает один instance модели из LM Studio."""
    if not model:
        return True

    endpoint = _native_api_endpoint("models/unload", api_base)
    payload = json.dumps({"instance_id": model}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("LM_STUDIO_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        print(f"[ERROR] LM Studio не выгрузил модель {model} ({exc.code}): {exc.reason}")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[ERROR] Не удалось подключиться к LM Studio для выгрузки модели {model}: {exc}")
    return False


def unload_all_models(api_base: str = None, model_names: list[str] = None) -> bool:
    """Выгружает загруженные модели LM Studio, известные текущему процессу."""
    names = list(model_names or [])
    names.extend([
        os.environ.get("EMBEDDING_MODEL", "text-embedding-qwen3-embedding-8b"),
        os.environ.get("LLM_MODEL", "qwen/qwen3.6-35b-a3b"),
    ])
    unique_names = list(dict.fromkeys(name for name in names if name))
    results = [unload_model(name, api_base=api_base) for name in unique_names]
    return all(results)


def unload_embedding_model(model: str = None, api_base: str = None) -> bool:
    """Совместимый алиас для выгрузки embedding-модели."""
    model = model or os.environ.get("EMBEDDING_MODEL", "text-embedding-qwen3-embedding-8b")
    return unload_model(model, api_base=api_base)


def _halfvec_literal(embedding: list[float]) -> str:
    """Преобразует список float в строковое представление для `::halfvec` в PostgreSQL."""
    trimmed = [float(v) for v in embedding[:4000]]
    return "[" + ",".join(str(v) for v in trimmed) + "]"


def generate_missing_embeddings(
    *,
    source_version: str = None,
    db_config: Optional[dict] = None,
    embedding_model: str = None,
    api_base: str = None,
    batch_size: int = 100,
) -> int:
    """Дозаполняет только отсутствующие embedding и безопасно возобновляется после остановки."""
    if not unload_all_models(api_base=api_base, model_names=[embedding_model]):
        raise RuntimeError("Не удалось выгрузить модели LM Studio перед генерацией embedding")

    conn = get_db_connection(db_config)
    cursor = conn.cursor()
    try:
        if source_version is None:
            cursor.execute("""
                SELECT id, english FROM translations
                WHERE embedding IS NULL
                ORDER BY id
            """)
        else:
            cursor.execute("""
                SELECT id, english FROM translations
                WHERE embedding IS NULL AND source_version = %s
                ORDER BY id
            """, (source_version,))
        rows = cursor.fetchall()
        if not rows:
            print("[INFO] Все записи уже имеют embedding")
            return 0

        processed = 0
        for offset in range(0, len(rows), max(1, batch_size)):
            batch = rows[offset:offset + max(1, batch_size)]
            embeddings = generate_embeddings_batch(
                [english for _, english in batch],
                model=embedding_model,
                api_base=api_base,
            )
            if not embeddings or len(embeddings) != len(batch):
                print(f"[WARNING] Пачка embedding пропущена: {offset}/{len(rows)}")
                continue

            cursor.executemany(
                """
                    UPDATE translations
                    SET embedding = %s::halfvec, updated_at = NOW()
                    WHERE id = %s AND embedding IS NULL
                """,
                [(_halfvec_literal(embedding), record_id)
                 for (record_id, _), embedding in zip(batch, embeddings)],
            )
            conn.commit()
            processed += len(batch)
            print(f"[INFO] Обработано {processed}/{len(rows)} недостающих embedding")

        return processed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def count_missing_embeddings(db_config: Optional[dict] = None) -> int:
    """Возвращает количество строк без embedding, не обращаясь к embedding-модели."""
    conn = get_db_connection(db_config)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM translations WHERE embedding IS NULL")
        return cursor.fetchone()[0]
    finally:
        conn.close()


def register_pending_translations(
    texts: set[str] | list[str],
    *,
    source_version: str,
    db_config: Optional[dict] = None,
    batch_size: int = 100,
) -> int:
    """Сохраняет английские фразы, которым ещё предстоит перевод и embedding."""
    pending = sorted({text.strip() for text in texts if text and text.strip()})
    if not pending:
        return 0

    conn = get_db_connection(db_config)
    try:
        cursor = conn.cursor()
        insert_sql = """
            INSERT INTO translations
                (english, russian, source_file, source_version, category, xml_tag)
            VALUES (%s, '', 'pending_translation', %s, 'pending', 'pending')
            ON CONFLICT (source_version, english) DO NOTHING
        """
        for offset in range(0, len(pending), max(1, batch_size)):
            chunk = pending[offset:offset + max(1, batch_size)]
            cursor.executemany(insert_sql, [(text, source_version) for text in chunk])
            conn.commit()
        print(f"[INFO] Зарегистрировано {len(pending)} недопереведённых фраз для {source_version}")
        return len(pending)
    finally:
        conn.close()


def generate_embedding_local(text: str) -> list[float]:
    """
    Генерирует эмбеддинг через lmstudio-python SDK.
    """
    try:
        import lmstudio as lms
        model = lms.embeddings(os.environ.get("EMBEDDING_MODEL", "qwen/qwen3.6-35b-a3b"))
        result = model.encode(text)
        return result.tolist() if hasattr(result, 'tolist') else list(result)
    except Exception as e:
        print(f"[WARNING] Не удалось сгенерировать эмбеддинг через lmstudio: {e}")
        return None


# ============================================================================
# Загрузка данных в PostgreSQL
# ============================================================================

def extract_category_from_path(file_path: str) -> str:
    """
    Определяет категорию перевода на основе пути к файлу.
    
    Примеры:
        DW2/ShipHulls.xml -> ship
        DW2/GameEvents.xml -> event
        Galactopedia/... -> lore
        DLC_.../... -> dlc
    """
    file_path = file_path.lower()
    
    if "galactopedia" in file_path or "lore" in file_path:
        return "lore"
    elif "gameevent" in file_path:
        return "event"
    elif "shiphull" in file_path:
        return "ship"
    elif "troop" in file_path or "unit" in file_path:
        return "unit"
    elif "research" in file_path:
        return "research"
    elif "race" in file_path or "government" in file_path:
        return "race"
    elif "touritem" in file_path:
        return "tour"
    elif "spaceitem" in file_path:
        return "spaceitem"
    elif "resource" in file_path:
        return "resource"
    elif "facility" in file_path:
        return "facility"
    elif "dLC" in file_path or "dlc" in file_path:
        return "dlc"
    else:
        return "general"


def extract_xml_tag(tag_desc: str) -> str:
    """Извлекает последний тег из полного пути (например, 'ShipHull/Name' -> 'Name')."""
    if '/' in tag_desc:
        return tag_desc.split('/')[-1]
    return tag_desc


def load_translations_to_db(
    reference_pairs: dict[str, str] | list[tuple[str, str]] | None = None,
    *,
    source_version: str,
    db_config: Optional[dict] = None,
    generate_embeddings: bool = True,
    embedding_model: str = None,
    api_base: str = None,
    batch_size: int = 100,
):
    """
    Загружает уже собранный справочник переводов в PostgreSQL.

    Смысл метода: ему передаётся словарь или список пар "английский -> русский",
    который строится в batch_translate.py на основе предыдущих переведённых версий
    из `--cache-from`. Сам метод в rag.py не парсит XML/TXT и не ищет файлы.
    """
    if reference_pairs is None:
        raise ValueError("reference_pairs must be provided; rag.py does not parse XML/TXT files")

    if isinstance(reference_pairs, dict):
        pairs = [(english, russian) for english, russian in reference_pairs.items() if english and russian and english != russian]
    elif isinstance(reference_pairs, list | tuple):
        pairs = []
        for item in reference_pairs:
            if isinstance(item, tuple) and len(item) == 2:
                english, russian = item
                if english and russian and english != russian:
                    pairs.append((english, russian))
    else:
        raise TypeError("reference_pairs must be dict[str, str] or list[tuple[str, str]]")

    if not pairs:
        print(f"[INFO] Нет данных для загрузки в RAG для версии {source_version}")
        return 0

    conn = get_db_connection(db_config)
    cursor = conn.cursor()

    print(f"[INFO] Проверка и применение миграций базы данных...")
    try:
        migrate_result = migrate_db(db_config=db_config, show_status=False)
        if migrate_result.get("status") == "success":
            print(f"[INFO] Миграции применены: {migrate_result['migrations_applied']}")
        elif migrate_result.get("status") == "error":
            print(f"[WARNING] Ошибка миграции: {migrate_result.get('error', 'unknown')}")
        else:
            print("[INFO] База данных актуальна.")
    except Exception as e:
        print(f"[WARNING] Не удалось применить миграции: {e}")

    insert_sql = """
        INSERT INTO translations (english, russian, source_file, source_version, category, xml_tag)
        VALUES (%s, %s, 'reference_cache', %s, 'reference', 'reference')
        ON CONFLICT (source_version, english) DO UPDATE
        SET russian = EXCLUDED.russian,
            source_file = EXCLUDED.source_file,
            category = EXCLUDED.category,
            xml_tag = EXCLUDED.xml_tag
    """

    total_pairs = 0
    for i in range(0, len(pairs), batch_size):
        chunk = pairs[i:i + batch_size]
        cursor.executemany(insert_sql, [(english, russian, source_version) for english, russian in chunk])
        conn.commit()
        total_pairs += len(chunk)
        print(f"[INFO] Вставлено {total_pairs}/{len(pairs)} пар переводов для {source_version}")

    conn.close()

    if generate_embeddings:
        print(f"\n[INFO] Проверка недостающих embedding для {source_version}...")
        generate_missing_embeddings(
            source_version=source_version,
            db_config=db_config,
            embedding_model=embedding_model,
            api_base=api_base,
            batch_size=batch_size,
        )

    return total_pairs


def _read_text_file(file_path: Path, encodings=None) -> list[str]:
    """Читает текстовый файл, пробуя несколько кодировок."""
    if encodings is None:
        encodings = ['utf-8', 'utf-8-sig', 'cp1252', 'cp1251']
    
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        return f.readlines()


# ============================================================================
# Поиск релевантных переводов (RAG retrieval)
# ============================================================================

def search_similar_translations(
    query: str,
    top_k: int = 5,
    db_config: Optional[dict] = None,
    include_category: str = None,
    use_hybrid: bool = True,
    generate_query_embedding: bool = True,
) -> list[dict]:
    """
    Ищет похожие переводы через векторный поиск (cosine similarity).
    
    Args:
        query: Английский текст для поиска
        top_k: Количество результатов
        db_config: Конфигурация подключения
        include_category: Фильтр по категории (ship, event, lore, etc.)
        use_hybrid: Использовать ли гибридный поиск (вектор + ключевые слова)
    
    Returns:
        Список словарей с переводами и релевантностью
    """
    conn = get_db_connection(db_config)
    cursor = conn.cursor(row_factory=psycopg.rows.dict_row)
    
    # После выгрузки embedding-модели используем заранее сохранённый embedding
    # текущей target-фразы. Это позволяет выполнять RAG во время работы LLM.
    cursor.execute(
        """
            SELECT embedding::text
            FROM translations
            WHERE english = %s AND embedding IS NOT NULL
            ORDER BY CASE WHEN russian <> '' THEN 0 ELSE 1 END, id
            LIMIT 1
        """,
        (query,),
    )
    stored_embedding = cursor.fetchone()
    if stored_embedding:
        embedding_str = stored_embedding["embedding"]
    elif generate_query_embedding:
        if not unload_all_models():
            print("[WARNING] Не удалось выгрузить модели перед генерацией embedding запроса")
            conn.close()
            return []
        query_embedding = generate_embedding(query)
        if not query_embedding:
            print("[WARNING] Не удалось сгенерировать эмбеддинг для запроса, возвращаем пустой результат")
            conn.close()
            return []
        embedding_str = f"[{','.join(map(str, query_embedding))}]"
    else:
        print("[WARNING] Для RAG-запроса нет сохранённого embedding; embedding-модель выгружена")
        conn.close()
        return []
    
    base_where = "WHERE russian <> '' AND embedding IS NOT NULL"
    params: list = []
    
    if include_category:
        base_where += " AND category = %s"
        params.append(include_category)
    
    if use_hybrid:
        # Гибридный поиск: векторный + текстовый
        # Векторный поиск (косинусовое расстояние)
        vector_query = f"""
            SELECT 
                english,
                russian,
                source_file,
                source_version,
                category,
                xml_tag,
                1 - (embedding <=> %s::halfvec) AS similarity
            FROM translations
            {base_where}
            ORDER BY embedding <=> %s::halfvec
            LIMIT %s
        """
        vector_params = [*params, embedding_str, embedding_str, top_k]

        cursor.execute(vector_query, vector_params)
        results = cursor.fetchall()
        
        # Текстовый поиск (как дополнение)
        text_query = f"""
            SELECT 
                english,
                russian,
                source_file,
                source_version,
                category,
                xml_tag,
                1 - (embedding <=> %s::halfvec) AS similarity,
                ts_rank(to_tsvector('english', english), plainto_tsquery('english', %s)) AS text_rank
            FROM translations
            {base_where}
            ORDER BY text_rank DESC
            LIMIT %s
        """
        text_params = [*params, embedding_str, query, top_k]
        
        cursor.execute(text_query, text_params)
        text_results = cursor.fetchall()
        
        # Объединяем результаты (простое объединение без дубликатов)
        seen = set()
        combined = []
        for r in results + text_results:
            if r['english'] not in seen:
                seen.add(r['english'])
                combined.append(dict(r))
        
        conn.close()
        return combined[:top_k * 2]  # Возвращаем больше результатов
    
    else:
        # Только векторный поиск
        query_sql = f"""
            SELECT 
                english,
                russian,
                source_file,
                source_version,
                category,
                xml_tag,
                1 - (embedding <=> %s::halfvec) AS similarity
            FROM translations
            {base_where}
            ORDER BY embedding <=> %s::halfvec
            LIMIT %s
        """
        query_params = [*params, embedding_str, embedding_str, top_k]

        cursor.execute(query_sql, query_params)
        results = cursor.fetchall()
        conn.close()
        return [dict(r) for r in results]


def build_rag_prompt(
    query_text: str,
    similar_translations: list[dict],
    system_prompt_template: str = None,
) -> str:
    """
    Формирует промпт с примерами релевантных переводов для RAG.
    
    Args:
        query_text: Текст для перевода
        similar_translations: Результаты поиска похожих переводов
        system_prompt_template: Шаблон системного промпта (опционально)
    
    Returns:
        Полный промпт для LLM
    """
    if system_prompt_template is None:
        from system_prompt import SYSTEM_PROMPT
        system_prompt_template = SYSTEM_PROMPT
    
    # Формируем секцию с примерами
    examples_section = ""
    if similar_translations:
        examples_section = "\n\nRELEVANT TRANSLATION EXAMPLES:\n"
        for i, trans in enumerate(similar_translations, 1):
            examples_section += f"{i}. EN: \"{trans['english']}\" -> RU: \"{trans['russian']}\"\n"
            if trans.get('category'):
                examples_section += f"   (category: {trans['category']}, source: {trans.get('source_file', 'N/A')})\n"
    
    # Добавляем примеры в промпт
    full_prompt = system_prompt_template + examples_section + f"\n\nTranslate this text:\nText: {query_text}"
    
    return full_prompt


# ============================================================================
# Статистика и утилиты
# ============================================================================

def get_db_stats(db_config: Optional[dict] = None) -> dict:
    """Возвращает статистику по базе переводов."""
    conn = get_db_connection(db_config)
    cursor = conn.cursor()
    
    stats = {}
    
    cursor.execute("SELECT COUNT(*) FROM translations")
    stats["total_translations"] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM translations WHERE embedding IS NOT NULL")
    stats["with_embeddings"] = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT category, COUNT(*) as count 
        FROM translations 
        GROUP BY category 
        ORDER BY count DESC
    """)
    stats["by_category"] = {row[0]: row[1] for row in cursor.fetchall()}
    
    cursor.execute("""
        SELECT source_version, COUNT(*) as count 
        FROM translations 
        GROUP BY source_version 
        ORDER BY count DESC
    """)
    stats["by_version"] = {row[0]: row[1] for row in cursor.fetchall()}
    
    conn.close()
    return stats


def print_db_stats(db_config: Optional[dict] = None):
    """Выводит статистику базы переводов."""
    stats = get_db_stats(db_config)
    
    print("\n" + "=" * 60)
    print("DW2 Russian RAG — Статистика базы переводов")
    print("=" * 60)
    print(f"Всего переводов: {stats['total_translations']}")
    print(f"С эмбеддингами: {stats['with_embeddings']}")
    print(f"\nПо категориям:")
    for cat, count in stats.get("by_category", {}).items():
        print(f"  {cat}: {count}")
    print(f"\nПо версиям:")
    for ver, count in stats.get("by_version", {}).items():
        print(f"  {ver}: {count}")
    print("=" * 60 + "\n")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="DW2 Russian RAG — загрузка и поиск переводов")
    subparsers = parser.add_subparsers(dest="command", help="Доступные команды")
    
    # Команда: migrate
    migrate_parser = subparsers.add_parser("migrate", help="Применить миграции базы данных")
    migrate_parser.add_argument("--target", type=int, default=None, help="Применить до этой версии (по умолчанию — все)")
    migrate_parser.add_argument("--status", action="store_true", help="Только показать статус без применения")
    migrate_parser.add_argument("--db-host", default="localhost")
    migrate_parser.add_argument("--db-port", type=int, default=5432)
    migrate_parser.add_argument("--db-name", default="dw2russian")
    migrate_parser.add_argument("--db-user", default="postgres")
    
    # Команда: load
    load_parser = subparsers.add_parser("load", help="Загрузить переводы в PostgreSQL")
    load_parser.add_argument("--english-dir", required=True, help="Путь к английской директории")
    load_parser.add_argument("--russian-dir", required=True, help="Путь к русской директории")
    load_parser.add_argument("--version", required=True, help="Версия игры")
    load_parser.add_argument("--db-host", default="localhost", help="Host PostgreSQL")
    load_parser.add_argument("--db-port", type=int, default=5432, help="Port PostgreSQL")
    load_parser.add_argument("--db-name", default="dw2russian", help="Имя базы данных")
    load_parser.add_argument("--db-user", default="postgres", help="Пользователь PostgreSQL")
    load_parser.add_argument("--no-embeddings", action="store_true", help="Не генерировать эмбеддинги")
    load_parser.add_argument("--embedding-model", help="Модель для эмбеддингов")
    load_parser.add_argument("--api-base", help="LM Studio API base URL")
    load_parser.add_argument("--batch-size", type=int, default=100, help="Размер батча")
    
    # Команда: search
    search_parser = subparsers.add_parser("search", help="Поиск похожих переводов")
    search_parser.add_argument("query", help="Текст для поиска")
    search_parser.add_argument("--top-k", type=int, default=5, help="Количество результатов")
    search_parser.add_argument("--category", help="Фильтр по категории")
    search_parser.add_argument("--no-hybrid", action="store_true", help="Отключить гибридный поиск")
    search_parser.add_argument("--db-host", default="localhost")
    search_parser.add_argument("--db-port", type=int, default=5432)
    search_parser.add_argument("--db-name", default="dw2russian")
    search_parser.add_argument("--db-user", default="postgres")
    
    # Команда: stats
    stats_parser = subparsers.add_parser("stats", help="Показать статистику базы")
    stats_parser.add_argument("--db-host", default="localhost")
    stats_parser.add_argument("--db-port", type=int, default=5432)
    stats_parser.add_argument("--db-name", default="dw2russian")
    stats_parser.add_argument("--db-user", default="postgres")
    
    # Команда: prompt
    prompt_parser = subparsers.add_parser("prompt", help="Сформировать RAG-промпт")
    prompt_parser.add_argument("query", help="Текст для перевода")
    prompt_parser.add_argument("--top-k", type=int, default=5, help="Количество примеров")
    prompt_parser.add_argument("--category", help="Фильтр по категории")
    prompt_parser.add_argument("--output", help="Файл для сохранения промпта")
    prompt_parser.add_argument("--db-host", default="localhost")
    prompt_parser.add_argument("--db-port", type=int, default=5432)
    prompt_parser.add_argument("--db-name", default="dw2russian")
    prompt_parser.add_argument("--db-user", default="postgres")
    
    args = parser.parse_args()
    
    if args.command == "migrate":
        db_config = {
            "host": args.db_host,
            "port": args.db_port,
            "dbname": args.db_name,
            "user": args.db_user,
            "password": os.environ.get("DW2_PG_PASSWORD", ""),
        }
        result = migrate_db(
            db_config=db_config,
            target_version=args.target,
            show_status=args.status,
        )
    
    elif args.command == "load":
        db_config = {
            "host": args.db_host,
            "port": args.db_port,
            "dbname": args.db_name,
            "user": args.db_user,
            "password": os.environ.get("DW2_PG_PASSWORD", ""),
        }
        load_translations_to_db(
            english_dir=args.english_dir,
            russian_dir=args.russian_dir,
            version=args.version,
            db_config=db_config,
            generate_embeddings=not args.no_embeddings,
            embedding_model=args.embedding_model,
            api_base=args.api_base,
            batch_size=args.batch_size,
        )
    
    elif args.command == "search":
        db_config = {
            "host": args.db_host,
            "port": args.db_port,
            "dbname": args.db_name,
            "user": args.db_user,
            "password": os.environ.get("DW2_PG_PASSWORD", ""),
        }
        if count_missing_embeddings(db_config=db_config):
            generate_missing_embeddings(db_config=db_config)
        results = search_similar_translations(
            query=args.query,
            top_k=args.top_k,
            db_config=db_config,
            include_category=args.category,
            use_hybrid=not args.no_hybrid,
        )
        
        if results:
            print(f"\nНайдено {len(results)} похожих переводов:\n")
            for i, r in enumerate(results, 1):
                print(f"{i}. [{r.get('category', 'N/A')}] \"{r['english']}\"")
                print(f"   → \"{r['russian']}\"")
                if r.get('source_file'):
                    print(f"   (из: {r['source_file']})")
                if r.get('similarity'):
                    print(f"   similarity: {r['similarity']:.4f}")
                print()
        else:
            print("Ничего не найдено.")
    
    elif args.command == "stats":
        db_config = {
            "host": args.db_host,
            "port": args.db_port,
            "dbname": args.db_name,
            "user": args.db_user,
            "password": os.environ.get("DW2_PG_PASSWORD", ""),
        }
        print_db_stats(db_config)
    
    elif args.command == "prompt":
        db_config = {
            "host": args.db_host,
            "port": args.db_port,
            "dbname": args.db_name,
            "user": args.db_user,
            "password": os.environ.get("DW2_PG_PASSWORD", ""),
        }
        if count_missing_embeddings(db_config=db_config):
            generate_missing_embeddings(db_config=db_config)
        results = search_similar_translations(
            query=args.query,
            top_k=args.top_k,
            db_config=db_config,
            include_category=args.category,
        )
        prompt = build_rag_prompt(args.query, results)
        
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(prompt)
            print(f"[INFO] Промпт сохранён в {args.output}")
        else:
            print(prompt)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
