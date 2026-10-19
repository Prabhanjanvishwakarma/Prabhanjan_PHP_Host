
FROM php:8.3-cli-bookworm

RUN apt-get update && apt-get install -y \
    python3 python3-venv curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
    -o /etc/apt/trusted.gpg.d/ngrok.asc \
    && echo "deb https://ngrok-agent.s3.amazonaws.com bookworm main" \
    > /etc/apt/sources.list.d/ngrok.list \
    && apt-get update \
    && apt-get install -y ngrok \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY bot.py .

RUN mkdir -p /data/site /data/logs \
    && chmod 755 /data/site /data/logs

ENV PATH="/opt/venv/bin:$PATH"
ENV SITE_DIR="/data/site"
ENV LOG_DIR="/data/logs"
ENV PHP_PORT="8080"

CMD ["python", "bot.py"]
