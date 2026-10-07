-- v7.8: real BM25 for main search — memory_keywords keyword index table
-- Main search's BM25 component upgraded from ILIKE (fake) to jieba-tokenized TF weighting (reusing the wiki v7.5 approach)
CREATE TABLE IF NOT EXISTS public.memory_keywords (
    memory_id bigint NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    token      text  NOT NULL,
    freq       real  NOT NULL DEFAULT 1,
    PRIMARY KEY (memory_id, token)
);
CREATE INDEX IF NOT EXISTS idx_memory_keywords_token ON memory_keywords(token);
CREATE INDEX IF NOT EXISTS idx_memory_keywords_mid ON memory_keywords(memory_id);
