#!/bin/bash
# scripts/deploy.sh - Deployment script

set -e

echo "🚀 Deploying Jellyfin Discord Webhook Service..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "❌ .env file not found! Please create one based on .env.example"
    exit 1
fi

# Create necessary directories
mkdir -p data logs config templates

# Set permissions
chmod 755 data logs config templates

# Pull latest images
docker-compose pull

# Build and start services
docker-compose up -d --build

# Wait for service to be ready
echo "⏳ Waiting for service to start..."
sleep 10

# Health check
if curl -f http://localhost:8080/health > /dev/null 2>&1; then
    echo "✅ Service is healthy!"
else
    echo "❌ Service health check failed. Check logs:"
    docker-compose logs --tail=50
    exit 1
fi

echo "🎉 Deployment complete! Service is running on http://localhost:8080"