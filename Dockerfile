FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create persistent directories (will be overridden by volumes in production)
RUN mkdir -p secure_vault/avatars secure_vault/docs secure_vault/posters

EXPOSE 8000

# Use 2 workers in production; override via WORKERS env var
CMD uvicorn app:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-2}
