FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Docker CLI + Compose plugin, so the autoscaler can run `docker compose --scale`
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:28-cli /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /autoscaler
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY autoscaler.py .

# Runs as root: it needs access to /var/run/docker.sock
CMD ["python", "autoscaler.py"]
