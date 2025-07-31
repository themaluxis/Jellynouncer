FROM python:3.11-slim AS production

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
WORKDIR /app
COPY main.py .

# Create necessary directories
RUN mkdir -p /app/config /app/templates /app/data /app/logs

# Copy default templates and config
COPY templates/ /app/templates/
COPY scripts/ /app/scripts/
COPY config/ /app/config/

# Set proper permissions
RUN chmod +x /app/main.py

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Run the application
CMD ["python", "main.py"]