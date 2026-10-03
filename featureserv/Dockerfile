# OGC API Features for ArcGIS Pro and QGIS: pygeoapi over the warehouse GeoParquet, scale-to-zero.
# The layer list comes from the catalog refresh at startup (start.py), so nothing is read at build.
FROM python:3.12-slim
RUN useradd --uid 65532 --create-home nonroot
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ugs_parquet.py start.py pygeoapi.base.yml test_ugs_parquet.py ./
# The plugin subclasses pygeoapi internals; this fails the build when a pygeoapi change breaks it.
RUN python -m pytest -q -p no:cacheprovider test_ugs_parquet.py && rm test_ugs_parquet.py
USER nonroot
ENV PYTHONPATH=/app \
    FEATURES_COLLECTIONS=gs://ut-dnr-ugs-maps-prod-public/warehouse/featureserv/collections.json \
    SERVER_URL=https://ugs-warehouse-features-xedvkyurga-uc.a.run.app
EXPOSE 9000
CMD ["python", "start.py"]
