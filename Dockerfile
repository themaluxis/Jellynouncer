FROM python:3.11-slim

# Set persistent storage
VOLUME /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    sqlite3 \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py .
COPY docker-entrypoint.sh /usr/local/bin/

# Create defaults directory structure
RUN mkdir -p /app/defaults/templates /app/defaults/config /app/defaults/scripts

# Copy default files to the correct locations
COPY templates/ /app/defaults/templates/
COPY config/config.json /app/defaults/config.json
COPY scripts/ /app/defaults/scripts/

# Create required directories
RUN mkdir -p /app/data /app/logs /app/config /app/templates /app/scripts && \
    chmod 755 /app/data /app/logs /app/config /app/templates /app/scripts && \
    chmod +x /app/defaults/scripts/*.sh 2>/dev/null || true && \
    chmod +x /app/defaults/scripts/*.py 2>/dev/null || true && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

# Expose port
EXPOSE 8080

# Set entrypoint to our script
ENTRYPOINT ["docker-entrypoint.sh"]

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Run the application directly
CMD ["python", "main.py"]