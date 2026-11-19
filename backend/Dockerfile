# NOC118: explicit reviewed Debian12 LTS base, Python3.12.16 unchanged.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS dependencies
WORKDIR /build
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --no-compile --prefix=/install --require-hashes --only-binary=:all: -r requirements.lock

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Retain GDAL's required Expat/zlib runtime; do not erase package metadata/CVEs.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends libexpat1 zlib1g && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip
COPY --from=dependencies /install/ /usr/local/
# ABI/import acceptance on the actual final base, not only the build stage.
RUN python -c "import ssl, sqlite3, pyexpat, rasterio, pyproj, psycopg, cryptography, fastapi, redis, alembic; print(ssl.OPENSSL_VERSION, sqlite3.sqlite_version, pyexpat.EXPAT_VERSION, rasterio.__gdal_version__, pyproj.proj_version_str)"
COPY . .
RUN chmod -R a=rX /app && groupadd --gid 10001 noc && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin noc && mkdir -p /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning && chown 10001:10001 /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning && chmod 700 /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 CMD ["python", "-c", "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8000/health', timeout=3)"]
