FROM docker.io/library/alpine:3.19 AS builder
RUN apk add --no-cache curl
RUN curl -L https://github.com/CrunchyData/pg_featureserv/releases/download/v1.3.1/pg_featureserv_1.3.1_linux_amd64.tar.gz | tar -xz

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /pg_featureserv /app/pg_featureserv
COPY config.toml /app/config.toml
ENTRYPOINT ["/app/pg_featureserv"]
CMD ["--config", "/app/config.toml"]
