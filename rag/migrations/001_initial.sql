-- ============================================================================
-- Миграция 1: Инициализация базы данных RAG
-- ============================================================================
-- Версия: 1
-- Описание: Создание основной таблицы переводов, индексов и представлений
-- ============================================================================

-- Включаем расширение для векторного поиска
CREATE EXTENSION IF NOT EXISTS vector;

-- Основная таблица пар переводов
CREATE TABLE IF NOT EXISTS translations (
    id SERIAL PRIMARY KEY,
    english TEXT NOT NULL,
    russian TEXT NOT NULL,
    source_file TEXT,
    source_version TEXT,
    category TEXT,
    xml_tag TEXT,
    embedding halfvec(4000),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- IVFFlat индекс для быстрого поиска по косинусному сходству
CREATE INDEX IF NOT EXISTS idx_translations_embedding 
    ON translations USING ivfflat (embedding halfvec_cosine_ops) WITH (lists = 100);

-- GIN индекс для полнотекстового поиска на русском
CREATE INDEX IF NOT EXISTS idx_translations_english_gin 
    ON translations USING gin (to_tsvector('russian', english));

-- Функция автообновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_translations_updated_at ON translations;
CREATE TRIGGER update_translations_updated_at
    BEFORE UPDATE ON translations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Представление: статистика переводов по категориям и версиям
CREATE OR REPLACE VIEW v_translation_stats AS
SELECT 
    category,
    source_version,
    COUNT(*) AS translation_count,
    MIN(created_at) AS first_added,
    MAX(created_at) AS last_updated
FROM translations
GROUP BY category, source_version
ORDER BY translation_count DESC;

-- Представление: топ категорий по объёму переводов
CREATE OR REPLACE VIEW v_top_categories AS
SELECT 
    category,
    COUNT(*) AS count,
    COUNT(DISTINCT source_version) AS versions
FROM translations
GROUP BY category
ORDER BY count DESC;
