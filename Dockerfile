FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/
COPY orchestrator/ orchestrator/
COPY configs/ configs/

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "bare_metal_automation.dashboard.asgi:application"]
