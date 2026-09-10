FROM python:3.12-slim

WORKDIR /lecture-chooser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get update \
 && apt-get install -y --no-install-recommends fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY . .
# Kopia zapasowa scraped — bind mount przy pierwszym starcie nadpisze
# katalog pustym volume; entrypoint seeduje go z tej kopii.
RUN cp -a app/data/scraped /opt/scraped-backup \
 && chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["hypercorn", "--bind", "0.0.0.0:8000", "app:app"]
