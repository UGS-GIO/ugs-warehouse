FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git
RUN git clone --depth=1 --branch v1.3.1 https://github.com/CrunchyData/pg_featureserv.git /src \
    && cd /src \
    && CGO_ENABLED=0 go build -ldflags="-w -s" -o pg_featureserv

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=builder /src/pg_featureserv /usr/local/bin/pg_featureserv
COPY --from=builder /src/assets /app/assets
COPY config.toml /app/config.toml
ENTRYPOINT ["/usr/local/bin/pg_featureserv"]
CMD ["--config", "/app/config.toml"]
