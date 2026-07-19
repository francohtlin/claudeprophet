FROM node:22-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CODEX_HOME=/app/.codex \
    CODEX_FORECAST_MODEL=gpt-5.5 \
    CODEX_FORECAST_SANDBOX=workspace-write

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements*.txt ./
RUN python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

RUN npm install -g @openai/codex

COPY . .
RUN chmod +x scripts/*.sh

CMD ["scripts/render_start.sh"]
