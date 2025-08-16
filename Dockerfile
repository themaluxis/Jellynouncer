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

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all Python application files except backup files
COPY *.py .
# Remove any backup files that might have been copied
RUN rm -f *.bak 2>/dev/null || true

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/

# Create defaults directory structure
RUN mkdir -p /app/defaults/templates /app/defaults/config

# Copy default files to the correct locations
COPY templates/ /app/defaults/templates/
COPY config/config.json /app/defaults/config.json

# Create required directories
RUN mkdir -p /app/data /app/logs /app/config /app/templates && \
    chmod 755 /app/data /app/logs /app/config /app/templates && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

# Set default environment variables
ENV HOST=0.0.0.0
ENV PORT=8080

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