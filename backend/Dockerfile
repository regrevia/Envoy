FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-media.txt requirements-media.txt
RUN pip install --upgrade pip \
    && pip install -r requirements-media.txt \
    && python -m playwright install --with-deps chromium

COPY app app
COPY trainers trainers

EXPOSE 8140
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8140"]
