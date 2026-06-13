FROM docker.io/crunchydata/pg_featureserv:latest
COPY config.toml /app/config.toml
ENTRYPOINT ["/app/pg_featureserv"]
CMD ["--config", "/app/config.toml"]
