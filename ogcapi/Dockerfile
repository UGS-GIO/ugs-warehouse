# Serverless OGC API Features — DuckDB over GeoParquet (no database). Scale-to-zero on Cloud Run.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Bake the DuckDB extensions into the image so a cold start LOADs them locally (no runtime
# download → faster, network-independent cold start).
RUN python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL spatial; INSTALL httpfs;')"

COPY main.py ./

# Cloud Run sets $PORT (default 8080). One worker — the work is I/O (CDN range reads) + DuckDB.
ENV PORT=8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
