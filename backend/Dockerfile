ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Debian mirror override for networks where deb.debian.org is slow or blocked.
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
RUN if [ "$DEBIAN_MIRROR" != "http://deb.debian.org/debian" ]; then \
      sed -i "s|http://deb.debian.org/debian|$DEBIAN_MIRROR|g; s|http://deb.debian.org/debian-security|$DEBIAN_MIRROR-security|g" \
        /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PLAYWRIGHT_DOWNLOAD_HOST=""
# WITH_MEDIA=0 skips whisper/paddleocr (~3 GB) for hosts on slow networks.
ARG WITH_MEDIA=1

WORKDIR /app
COPY requirements.txt requirements-media.txt requirements-media-extras.txt ./
RUN pip install --upgrade pip --index-url "$PIP_INDEX_URL" \
    && pip install --index-url "$PIP_INDEX_URL" -r requirements.txt -r requirements-media.txt \
    && if [ "$WITH_MEDIA" = "1" ]; then \
         pip install --index-url "$PIP_INDEX_URL" -r requirements-media-extras.txt; \
       fi \
    && if [ -n "$PLAYWRIGHT_DOWNLOAD_HOST" ]; then \
         PLAYWRIGHT_DOWNLOAD_HOST="$PLAYWRIGHT_DOWNLOAD_HOST" python -m playwright install --with-deps chromium; \
       else \
         python -m playwright install --with-deps chromium; \
       fi

COPY app app
COPY trainers trainers

EXPOSE 8140
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8140"]
