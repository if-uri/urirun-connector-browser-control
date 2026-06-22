FROM python:3.13-slim

WORKDIR /app
RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*
COPY . /app
RUN pip install --no-cache-dir -e ".[test]"

# Defense-in-depth: drop root (connector/node ports are >1024, non-root is fine).
RUN useradd -m -u 10001 app && chown -R app:app .
USER app

CMD ["bash", "docker/run-smoke.sh"]
