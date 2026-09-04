# Obraz pod docker-compose.yml (image: lecture-chooser:latest).
# Budowanie (ręczne, z katalogu głównego repo):
#   docker build -t lecture-chooser:latest .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Zależności najpierw — warstwa cache'owana niezależnie od kodu aplikacji.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Aplikacja (kod + templates + static + data wewnątrz `app/`).
COPY app ./app

# Opcjonalnie: wektorowy PDF renderowany na serwerze.
# Domyślnie WYŁĄCZONE — endpoint zwraca wtedy 503, a PDF generuje się
# w przeglądarce użytkownika. Odkomentowanie dodaje ~400 MB do obrazu:
# RUN playwright install --with-deps chromium

EXPOSE 8000

# Produkcyjny serwer (instalowany razem z quart).
CMD ["hypercorn", "--bind", "0.0.0.0:8000", "app:app"]
