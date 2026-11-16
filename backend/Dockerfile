FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# GDAL wheels require the system Expat runtime on the minimal Python image.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 zlib1g && rm -rf /var/lib/apt/lists/*
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock
COPY . .
RUN chmod -R a=rX /app && groupadd --gid 10001 noc && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin noc && mkdir -p /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning && chown 10001:10001 /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning && chmod 700 /var/lib/nwo-noc/documents /var/lib/nwo-noc/monitoring /var/lib/nwo-noc/signup /var/lib/nwo-noc/planning
USER 10001:10001
EXPOSE 8000
