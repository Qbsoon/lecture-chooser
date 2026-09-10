FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get update \
 && apt-get install -y --no-install-recommends fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["hypercorn", "--bind", "0.0.0.0:8000", "app:app"]
