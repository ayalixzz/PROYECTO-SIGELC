FROM node:24-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
ENV DJANGO_SETTINGS_MODULE=config.settings_hospedado \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-hosted.txt ./
RUN pip install --no-cache-dir -r requirements-hosted.txt \
    && addgroup --system sigelc \
    && adduser --system --ingroup sigelc --no-create-home sigelc
COPY backend/manage.py ./
COPY backend/config ./config
COPY backend/gestion_contractual ./gestion_contractual
COPY --from=frontend /app/frontend/dist /app/frontend/dist
USER sigelc
EXPOSE 10000
CMD ["sh", "-ec", "python manage.py migrate --check && exec gunicorn --bind \"0.0.0.0:${PORT:-10000}\" --workers 1 --threads 4 --timeout 90 config.wsgi:application"]
