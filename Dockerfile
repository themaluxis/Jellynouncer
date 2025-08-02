FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directory structure
RUN mkdir -p /app/config /app/templates /app/data /app/logs /app/scripts /app/defaults/templates /app/defaults/scripts

# Copy application code
COPY main.py .
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Copy default configuration files to the defaults directory
COPY config/config.json /app/defaults/
COPY templates/*.j2 /app/defaults/templates/

# Copy scripts and make them executable in the defaults directory
COPY scripts/* /app/defaults/scripts/
RUN chmod +x /app/defaults/scripts/*

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Set entrypoint to our script
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command
CMD ["python", "main.py"]