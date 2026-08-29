CREATE INDEX translations_engilsh_idx ON translations(english);

CREATE FUNCTION update_new_embeddings_by_old_data() RETURNS VOID AS $$
  WITH prepared AS (
    SELECT DISTINCT ON (old_tran.english)
      new_tran.id as new_id,
      old_tran.id as old_id,
      old_tran.embedding
    FROM translations as new_tran
    JOIN translations as old_tran ON old_tran.english = new_tran.english
    WHERE new_tran.embedding IS NULL
      AND old_tran.embedding IS NOT NULL
      ORDER BY old_tran.english, old_tran.source_version DESC
    )
    UPDATE translations
      SET embedding = prepared.embedding
    FROM prepared
    WHERE translations.id = prepared.new_id;
  $$ LANGUAGE SQL;