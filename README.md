# EasyDocs Platform

A polished Django-based workflow platform for managing client services, employee operations, document-driven processes, automated reminders, and real-time notifications. The system is designed to support operational teams with a structured backend, role-aware workflows, and background job processing for high-volume business processes.

This repository is built as a professional portfolio project and demonstrates a full-stack web application architecture using Django, PostgreSQL, Redis, Celery, Docker, and modern deployment practices.

## 1. Key Features

- Client and service workflow management with process tracking
- Employee and administrative modules for internal operations
- Automated reminders and scheduled background tasks
- Notification delivery through email, Firebase, and real-time channels
- Secure handling of sensitive configuration using environment-based settings
- Docker-based development and deployment setup for repeatable environments
- Document and media management integrated into the core workflows

## 2. Tech Stack

### Frontend
- Django templates
- HTML, CSS, and JavaScript
- Static asset pipeline for UI components

### Backend
- Python 3.11+
- Django 5.2
- Django REST Framework
- Celery for background jobs
- Channels and Daphne for asynchronous/websocket support

### Database & Caching
- PostgreSQL
- Redis

### Deployment & DevOps
- Docker Compose
- Nginx
- Gunicorn
- Sentry for monitoring

## 3. Prerequisites

Before getting started, make sure you have the following installed:

- Python 3.11 or newer
- pip and virtualenv
- PostgreSQL 15+
- Redis 7+
- Docker and Docker Compose (recommended for local development)
- Build tools such as gcc and development headers if your system requires them for Python packages

## 4. Installation & Local Setup

### Option A: Local virtual environment

```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Create a local environment file and update it with your values:

```bash
cp .env.example .env
```

Run database migrations:

```bash
python manage.py migrate
```

Create an admin user:

```bash
python manage.py createsuperuser
```

### Option B: Docker Compose

```bash
docker compose up --build
```

This will start the web application, PostgreSQL, Redis, Celery workers, and Nginx.

## 5. Environment Variables

Create a `.env` file in the project root with the following template. Replace placeholder values with your own settings.

```env
SECRET_KEY=replace-with-a-secure-secret-key
DEBUG=True
DJANGO_ENV=development
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
SITE_DOMAIN=http://localhost:8080

POSTGRES_DB=easydocs
POSTGRES_USER=easydocsuser
POSTGRES_PASSWORD=change-me
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=your-email@example.com
EMAIL_HOST_PASSWORD=your-password
DEFAULT_FROM_EMAIL=no-reply@example.com

FERNET_KEY=replace-with-a-generated-fernet-key
SENTRY_DSN=

GOOGLE_DRIVE_FOLDER_ID=
GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_PATH=/path/to/service-account.json

HF_API_KEY=replace-me
OPENROUTER_KEY=replace-me
BOT_SECRET=replace-with-strong-secret
CLIENT_SECRET=
```

> Do not commit real secrets or production credentials to version control.

## 6. Usage

### Start the web app

```bash
python manage.py runserver
```

### Start background workers

In separate terminals:

```bash
celery -A GGI worker -l info
```

```bash
celery -A GGI beat -l info
```

### Start with Docker

```bash
docker compose up
```

## 7. Folder Structure

```text
apps/
  accounts/
  EasyDocs/
  Employee/
  notifications/
GGI/
  settings.py
  urls.py
  asgi.py
  celery.py
templates/
static/
media/
docker-compose.yml
requirements.txt
requirements-dev.txt
```

## 8. Contributing

Contributions are welcome. If you would like to improve the project, please open an issue or submit a pull request with a clear description of the change and any relevant testing details.

## 9. License

This project is licensed under the MIT License. See the LICENSE file for more details.
