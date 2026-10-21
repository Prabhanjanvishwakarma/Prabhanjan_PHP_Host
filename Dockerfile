FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tar unzip gcc g++ build-essential \
    nodejs npm php-cli php-curl php-mbstring php-xml php-zip \
    && curl -fsSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz \
       | tar -xz -C /usr/local/bin \
    && chmod +x /usr/local/bin/ngrok \
    && php -v && ngrok version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 10000
CMD ["python", "app.py"]
