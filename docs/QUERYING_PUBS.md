# Querying publications (pubs-duckdb MCP)

Dev/consumer tool, not part of the build. `pubs-duckdb` MCP server (MotherDuck DuckDB MCP, installed in `.venv`) queries the public pub-search `.duckdb` + `corpus.json` on the CDN via SQL/BM25 — no GCP perms (CDN objects are public). Claude Code wires it in `.mcp.json` (gitignored); OpenCode in `opencode.json` (see `opencode.json.example`). Tool = `execute_query` (arg `sql`).

**Bootstrap each session** (in-memory DB resets on restart; full script `scripts/pubs_mcp_bootstrap.sql`). Run statements as SEPARATE `execute_query` calls — the MCP chokes on batched DDL+SELECT:

```sql
INSTALL httpfs; LOAD httpfs; INSTALL fts; LOAD fts;
ATTACH 'https://maps-assets.geology.utah.gov/pubs/search/pubs-fts.duckdb' AS pubs (READ_ONLY);
USE memory;
CREATE OR REPLACE TABLE arts AS SELECT id, sid, title, topic, page, pdf, text
  FROM read_json_auto('https://maps-assets.geology.utah.gov/pubs/search/corpus.json');
PRAGMA create_fts_index('arts','id','title','text', overwrite=1);
```

Two grains:
- `pubs.docs(id,title,series,year,pdf,body)` — every pub (5597), `body` = full document text.
- `arts(id,sid,title,topic,page,pdf,text)` — Survey Notes split to article/page (1052), topic-tagged.

**Query templates** (copy, don't reinvent):
```sql
-- doc-level BM25 — USE pubs first (macro internals unqualified; fails from memory db)
USE pubs;
SELECT id, series, title FROM docs
WHERE fts_main_docs.match_bm25(id,'landslide slope failure') IS NOT NULL
ORDER BY fts_main_docs.match_bm25(id,'landslide slope failure') DESC LIMIT 20;

-- article-level BM25 — from memory db, returns page + topic
SELECT id, sid, page, topic, title FROM arts
WHERE fts_main_arts.match_bm25(id,'wildfire fire burn') IS NOT NULL
ORDER BY fts_main_arts.match_bm25(id,'wildfire fire burn') DESC LIMIT 20;
```

Gotchas: `USE pubs` before doc-level BM25 (else `pubs.fts_main_docs...` breaks on `fts_main_docs.terms`). `execute_query` output caps at 50KB → read big `body` in windows: `SELECT substr(body,1,34000) FROM docs WHERE id='RI-232';`. Semantic/VSS (`pubs-vss.duckdb`) NOT usable here — needs the query embedded (bge-small) first; that stays a viewer job. The `/graph/*.parquet` folder is 404/dead — ignore it and any non-warehouse CDN object.
