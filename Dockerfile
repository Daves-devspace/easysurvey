# Use official Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      gcc \
      libpq-dev \
      curl \
      netcat-openbsd \
      libjpeg-dev \
      zlib1g-dev \
      libmagic1 \
      pkg-config \
      libmariadb-dev \
      build-essential \
      libssl-dev \
      libffi-dev \
      ca-certificates \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Install dockerize for robust service wait
ENV DOCKERIZE_VERSION=v0.6.1
RUN curl -sSL "https://github.com/jwilder/dockerize/releases/download/$DOCKERIZE_VERSION/dockerize-linux-amd64-$DOCKERIZE_VERSION.tar.gz" \
    | tar -C /usr/local/bin -xzv \
 && chmod +x /usr/local/bin/dockerize

# Create a non-root user and set permissions
RUN addgroup --system django \
 && adduser  --system --ingroup django django \
 && mkdir -p /app \
 && chown -R django:django /app

# Set work directory
WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# Copy project files
COPY . .

# Copy entrypoint script and make executable
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch to non-root user
USER django

# Set entrypoint
ENTRYPOINT ["/entrypoint.sh"]

# Default command: run Gunicorn
CMD ["gunicorn", "GGI.wsgi:application", "--bind", "0.0.0.0:8000"]
