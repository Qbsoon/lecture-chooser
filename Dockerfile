FROM python:3.12-slim

WORKDIR /lecture-chooser

COPY requirements.txt .
# libatomic1 (and other Node runtime deps) must be present BEFORE Playwright's
# bundled node binary runs; --with-deps cannot help if node can't even start.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libatomic1 ca-certificates fonts-liberation vim-tiny nano \
 && pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY . .
# Kopia zapasowa scraped — bind mount przy pierwszym starcie nadpisze
# katalog pustym volume; entrypoint seeduje go z tej kopii.
RUN cp -a app/data/scraped /opt/scraped-backup \
 && chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["hypercorn", "--bind", "0.0.0.0:8000", "asgi:app"]
