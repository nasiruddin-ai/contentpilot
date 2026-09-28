FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Open-source fonts for rendered visuals (Inter matches the default brand kit; DejaVu is the fallback).
# Bengali: Noto Sans/Serif Bengali (the rest of the Noto set is removed to keep the image small)
# and libraqm, which Pillow needs to shape Bengali conjuncts and vowel signs correctly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-inter fonts-dejavu-core fonts-noto-core libraqm0 \
    && find /usr/share/fonts/truetype/noto -type f ! -name "Noto*Bengali*" -delete \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/media \
    && chown appuser /data/media
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
