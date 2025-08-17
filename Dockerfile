FROM python:3.13.7-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    sqlite3 \
    jq \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy ONLY requirements.txt first
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create directory structure
RUN mkdir -p /app/defaults/templates /app/defaults/config \
    /app/data /app/logs /app/config /app/templates && \
    chmod 755 /app/data /app/logs /app/config /app/templates

# Set default environment variables
ENV HOST=0.0.0.0
ENV PORT=8080
ENV TERM=xterm-256color
ENV PYTHONUNBUFFERED=1

# Copy static files that rarely change first
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Copy default configuration (templates and config)
COPY templates/ /app/defaults/templates/
COPY config/config.json /app/defaults/config.json

# Copy Python application files LAST (these change most frequently)
COPY main.py ./
COPY jellynouncer/ ./jellynouncer/

# Expose port (configurable via build arg or environment)
ARG PORT=8080
EXPOSE ${PORT}

# Set entrypoint to our script
ENTRYPOINT ["docker-entrypoint.sh"]

# Health check using environment variable for port
HEALTHCHECK --interval=300s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Run the application directly
CMD ["python", "main.py"]