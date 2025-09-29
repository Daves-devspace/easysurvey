FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps (only what’s needed to build Python deps)
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

# Install python dependencies first (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Runtime libs only (no compilers, just what the app needs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libmariadb3 libjpeg62-turbo zlib1g libmagic1 netcat-openbsd \
 && rm -rf /var/lib/apt/lists/*

# Copy python deps from builder
COPY --from=builder /usr/local /usr/local

# Copy app code
COPY . .

# Create dirs with proper perms
RUN mkdir -p /app/static /app/staticfiles /app/media

# Entrypoint
COPY --chmod=0755 entrypoint.sh /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "GGI.wsgi:application", "--bind", "0.0.0.0:8000"]

















# ENV PYTHONDONTWRITEBYTECODE=1 \
#     PYTHONUNBUFFERED=1

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     gcc \
#     libpq-dev \
#     curl \
#     netcat-openbsd \
#     libjpeg-dev \
#     zlib1g-dev \
#     libmagic1 \
#     pkg-config \
#     libmariadb-dev \
#     build-essential \
#     libssl-dev \
#     libffi-dev \
#     ca-certificates \
#  && apt-get clean \
#  && rm -rf /var/lib/apt/lists/*

# # Install dockerize
# ENV DOCKERIZE_VERSION=v0.6.1
# RUN curl -sSL "https://github.com/jwilder/dockerize/releases/download/$DOCKERIZE_VERSION/dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz" \
#     | tar -C /usr/local/bin -xzv \
#  && chmod +x /usr/local/bin/dockerize

# WORKDIR /app

# # Install requirements first
# COPY requirements.txt .
# RUN pip install --upgrade pip && pip install -r requirements.txt

# # Copy project code
# COPY . /app

# # Create necessary directories with proper permissions
# RUN mkdir -p /app/static /app/staticfiles /app/media && \
#     chmod -R 755 /app

# # Entrypoint
# COPY --chmod=0755 entrypoint.sh /app/entrypoint.sh

# # Run as root for now (address permissions later)
# # USER django

# ENTRYPOINT ["/app/entrypoint.sh"]
# CMD ["gunicorn", "GGI.wsgi:application", "--bind", "0.0.0.0:8000"]