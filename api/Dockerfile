FROM alpine:3.19
RUN apk add --no-cache curl tar ca-certificates
RUN curl -L https://github.com/CrunchyData/pg_featureserv/releases/download/v1.3.1/pg_featureserv_1.3.1_linux_amd64.tar.gz -o pg_featureserv.tar.gz && tar -xzf pg_featureserv.tar.gz -C /usr/local/bin --strip-components=1 pg_featureserv_1.3.1_linux_amd64/pg_featureserv && rm pg_featureserv.tar.gz
COPY config.toml /app/config.toml
ENTRYPOINT ["/usr/local/bin/pg_featureserv"]
CMD ["--config", "/app/config.toml"]
