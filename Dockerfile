FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY README.md ./
COPY core ./core
COPY smtp ./smtp
COPY imap ./imap

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir ./core ./smtp ./imap

RUN addgroup --system --gid 10001 agent-arbiter \
    && adduser --system --uid 10001 --ingroup agent-arbiter \
        --home /nonexistent --no-create-home agent-arbiter

USER 10001:10001
EXPOSE 8025

ENTRYPOINT ["arbiter-server"]
CMD ["--config-dir", "/config", "--config-name", "config", "serve"]
