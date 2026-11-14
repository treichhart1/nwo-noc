FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod -R a=rX /app && groupadd --gid 10001 noc && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin noc && mkdir -p /var/lib/nwo-noc/monitoring && chown 10001:10001 /var/lib/nwo-noc/monitoring && chmod 700 /var/lib/nwo-noc/monitoring
USER 10001:10001
EXPOSE 8000
