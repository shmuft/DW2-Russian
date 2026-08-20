-- RAG schema for DW2 translation knowledge base
-- Run: psql -U postgres -d dw2russian -f schema.sql

-- Enable vector extension for similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Translation pairs with semantic embeddings
CREATE TABLE IF NOT EXISTS translations (
    id SERIAL PRIMARY KEY,
    english TEXT NOT NULL,
    russian TEXT NOT NULL,
    source_file TEXT,              -- relative path to source file (e.g., DW2/ShipHulls.xml)
    source_version TEXT,           -- game version (e.g., 1.3.6.6)
    category TEXT,                 -- derived from file path: ship, event, lore, ui, race, etc.
    xml_tag TEXT,                  -- XML element context (ShipHull, EventName, Description, etc.)
    embedding vector(4096),        -- semantic embedding (change size to match your model)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- IVFFlat index for fast cosine similarity search
-- lists = 100 is good for ~1K-100K rows. Adjust based on data size.
CREATE INDEX IF NOT EXISTS idx_translations_embedding 
    ON translations USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- GIN index for full-text search on English text (fallback / hybrid search)
CREATE INDEX IF NOT EXISTS idx_translations_english_gin 
    ON translations USING gin (to_tsvector('russian', english));

-- Trigger to auto-update updated_at timestamp
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

-- View: recent translations grouped by category
CREATE OR REPLACE VIEW v_translation_stats AS
SELECT 
    category,
    source_version,
    COUNT(*) as translation_count,
    MIN(created_at) as first_added,
    MAX(created_at) as last_updated
FROM translations
GROUP BY category, source_version
ORDER BY translation_count DESC;

-- View: top categories by translation volume
CREATE OR REPLACE VIEW v_top_categories AS
SELECT 
    category,
    COUNT(*) as count,
    COUNT(DISTINCT source_version) as versions
FROM translations
GROUP BY category
ORDER BY count DESC;
