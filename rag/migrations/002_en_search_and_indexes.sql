-- ============================================================================
-- Миграция 2: Добавление полнотекстового поиска на английском
-- ============================================================================
-- Версия: 2
-- Описание: Добавление GIN индекса для английского текста и
--           частичного совпадения (pg_trgm)
-- ============================================================================

-- Включаем расширение для треморов (частичное совпадение)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- GIN индекс для полнотекстового поиска на английском
CREATE INDEX IF NOT EXISTS idx_translations_english_en_gin 
    ON translations USING gin (to_tsvector('english', english));

-- Индекс по source_file для быстрого фильтрации по файлам
CREATE INDEX IF NOT EXISTS idx_translations_source_file 
    ON translations (source_file);

-- Индекс по category для группировки
CREATE INDEX IF NOT EXISTS idx_translations_category 
    ON translations (category);

-- Индекс по source_version для фильтрации по версиям
CREATE INDEX IF NOT EXISTS idx_translations_source_version 
    ON translations (source_version);

-- Одна справочная пара должна быть единственной внутри версии.
-- Перед созданием индекса оставляем последнюю запись для каждой пары.
DELETE FROM translations older
USING translations newer
WHERE older.source_version = newer.source_version
    AND older.english = newer.english
    AND older.id < newer.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_translations_source_version_english
        ON translations (source_version, english);

-- Представление: уникальные пары переводов по файлу и категории
CREATE OR REPLACE VIEW v_translation_coverage AS
SELECT 
    source_file,
    category,
    COUNT(DISTINCT source_version) AS versions_covered,
    COUNT(*) AS total_pairs,
    COUNT(DISTINCT english) AS unique_english
FROM translations
GROUP BY source_file, category
ORDER BY total_pairs DESC;
