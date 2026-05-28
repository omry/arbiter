FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

RUN addgroup --system --gid 10001 agent-arbiter \
    && adduser --system --uid 10001 --ingroup agent-arbiter \
        --home /nonexistent --no-create-home agent-arbiter

USER 10001:10001
EXPOSE 8025

ENTRYPOINT ["agent-arbiter"]
CMD ["--config-path", "/config", "--config-name", "config"]
