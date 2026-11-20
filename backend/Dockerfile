# NOC118: Ubuntu24.04 vendor-maintained Python3.12; candidate requires full acceptance.
FROM ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8 AS dependencies
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o APT::Update::Error-Mode=any update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends python3.12 python3.12-venv ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --no-compile --require-hashes --only-binary=:all: -r requirements.lock && pip uninstall -y pip

FROM ubuntu:24.04@sha256:73b999393d07f2bf65a40373778a44dc9a6d54ad7718d29247d8ff9413c4d6a8
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
WORKDIR /app
RUN apt-get -o APT::Update::Error-Mode=any update && apt-get upgrade -y --no-install-recommends && apt-get install -y --no-install-recommends python3.12 ca-certificates libexpat1 zlib1g && rm -rf /var/lib/apt/lists/*
COPY --from=dependencies /opt/venv/ /opt/venv/
RUN python -c "import ssl, sqlite3, pyexpat, rasterio, pyproj, psycopg, cryptography, fastapi, redis, alembic; print(ssl.OPENSSL_VERSION, sqlite3.sqlite_version, pyexpat.EXPAT_VERSION, rasterio.__gdal_version__, pyproj.proj_version_str)"
COPY . .
RUN chmod -R a=rX /app && groupadd --gid 10001 noc && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin noc && mkdir -p /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning && chown 10001:10001 /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning && chmod 700 /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 CMD ["python", "-c", "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8000/health', timeout=3)"]
