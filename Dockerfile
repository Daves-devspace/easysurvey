# ===== Builder stage =====
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies needed to build Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    libmariadb-dev \
    build-essential \
    libssl-dev \
    libffi-dev \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# ===== Runtime stage =====
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Runtime-only dependencies (added gettext-base for envsubst)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libmariadb3 libjpeg62-turbo zlib1g libmagic1 netcat-openbsd gettext-base \
 && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local /usr/local

# Copy application code
COPY . .

# Create directories with correct permissions
RUN mkdir -p /app/static /app/staticfiles /app/media && chmod -R 755 /app

# Entrypoint
COPY --chmod=0755 entrypoint.sh /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "GGI.wsgi:application", "--bind", "0.0.0.0:8000"]
