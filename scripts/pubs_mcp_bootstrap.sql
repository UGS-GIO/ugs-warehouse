-- Bootstrap the pubs-duckdb MCP session. The MCP DB is in-memory, so this re-runs each session.
-- Run each statement via the execute_query tool (the MCP chokes on batched DDL+SELECT in one call).
-- Only warehouse pub-search assets are wired. (The /graph/*.parquet folder is dead — do not use it.)

-- 1. Doc-level full text: every publication, one row per pub. `body` = full document text.
INSTALL httpfs; LOAD httpfs; INSTALL fts; LOAD fts;
ATTACH 'https://maps-assets.geology.utah.gov/pubs/search/pubs-fts.duckdb' AS pubs (READ_ONLY);

-- 2. Article-level full text: Survey Notes split to page + topic (finer than pubs.docs).
--    Built into the writable in-memory db + FTS-indexed. Run these three as SEPARATE calls.
USE memory;
CREATE OR REPLACE TABLE arts AS
  SELECT id, sid, title, topic, page, pdf, text
  FROM read_json_auto('https://maps-assets.geology.utah.gov/pubs/search/corpus.json');
PRAGMA create_fts_index('arts', 'id', 'title', 'text', overwrite=1);

-- ── Query patterns ──────────────────────────────────────────────────────────
-- Doc-level BM25 (USE pubs first, or qualify pubs.fts_main_docs.match_bm25):
--   USE pubs;
--   SELECT id, series, title FROM docs
--   WHERE fts_main_docs.match_bm25(id,'debris flow') IS NOT NULL
--   ORDER BY fts_main_docs.match_bm25(id,'debris flow') DESC LIMIT 20;
--
-- Article-level BM25 (from memory db; returns page + topic):
--   SELECT id, sid, page, topic, title FROM arts
--   WHERE fts_main_arts.match_bm25(id,'wildfire fire burn') IS NOT NULL
--   ORDER BY fts_main_arts.match_bm25(id,'wildfire fire burn') DESC LIMIT 20;
--
-- Read a pub's full text for RAG:  SELECT body FROM pubs.docs WHERE id='PI-90';
-- Read an article's text:          SELECT text FROM arts WHERE id='SNT-45-3#0';
