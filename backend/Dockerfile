FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY verity ./verity
COPY config.toml ./
COPY tasks ./tasks

RUN pip install --no-cache-dir . && rm -rf verity pyproject.toml *.egg-info

ENV VERITY_SERVER__HOST=0.0.0.0 \
    VERITY_CONFIG=/app/config.toml \
    VERITY_DATA_DIR=/app/data \
    VERITY_TASKS_DIR=/app/tasks \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "export VERITY_SERVER__PORT=${PORT:-8080}; exec verity-api"]
