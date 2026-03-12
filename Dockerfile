# =========================
# Builder stage
# =========================
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libjpeg-dev zlib1g-dev libmariadb-dev build-essential \
    libssl-dev libffi-dev curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# =========================
# Runtime stage
# =========================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libmariadb3 libjpeg62-turbo zlib1g libmagic1 netcat-openbsd gettext-base \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 1000 appuser \
 && useradd -u 1000 -g appuser -m -s /bin/bash appuser \
 && mkdir -p /app/staticfiles /app/media \
 && chown -R appuser:appuser /app

# Copy Python packages from builder
COPY --from=builder /usr/local /usr/local

# Copy app code and entrypoint
COPY --chown=appuser:appuser . .
COPY --chown=appuser:appuser --chmod=0755 entrypoint.sh /app/entrypoint.sh

# Run as non-root
USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "GGI.asgi:application"]
