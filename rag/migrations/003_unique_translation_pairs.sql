-- ============================================================================
-- Миграция 3: Уникальность справочных пар
-- ============================================================================
-- Удаляем дубли, сохраняя запись с максимальным id, затем запрещаем повторную
-- вставку одной и той же английской строки в рамках версии.
-- ============================================================================

DELETE FROM translations older
USING translations newer
WHERE older.source_version = newer.source_version
  AND older.english = newer.english
  AND older.id < newer.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_translations_source_version_english
    ON translations (source_version, english);
