FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py .
COPY templates/ ./templates/
COPY config/ ./config/
COPY scripts/ ./scripts/

# Create required directories
RUN mkdir -p /app/data /app/logs && \
    chmod 755 /app/data /app/logs && \
    chmod +x /app/scripts/*.sh 2>/dev/null || true && \
    chmod +x /app/scripts/*.py 2>/dev/null || true

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Run the application directly
CMD ["python", "main.py"]