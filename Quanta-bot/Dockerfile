FROM python:3.10-slim

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirement files and install dependencies
COPY production/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire repository into the container
COPY . .

# Ensure the runtime directory exists for persistent SQLite/logs via volume mounts
RUN mkdir -p /app/production/runtime

# Default command to run the live orchestrator with the production config
CMD ["python", "production/main.py", "--config", "production/configs/production_config.json"]
