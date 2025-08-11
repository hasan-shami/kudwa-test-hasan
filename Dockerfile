FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# minimal system deps
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# create non-root user
ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g ${APP_GID} ${APP_USER} \
 && useradd -m -u ${APP_UID} -g ${APP_GID} -s /bin/bash ${APP_USER}

WORKDIR /app

# install python deps (cached layer)
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && . /opt/venv/bin/activate \
 && pip install -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

# copy app
COPY . /app

# prep writable dirs (note: host mounts will overlay these)
RUN mkdir -p /app/data /app/logs \
 && chown -R ${APP_USER}:${APP_USER} /app

USER ${APP_USER}

# default server config (can be overridden via env/compose)
ENV HOST=0.0.0.0 \
    PORT=8000 \
    UVICORN_WORKERS=1

EXPOSE 8000

# Use sh -c so we can expand env vars in CMD
CMD ["sh","-c","uvicorn app.main:app --host ${HOST} --port ${PORT} --workers ${UVICORN_WORKERS}"]
