#!/bin/bash
set -e

# Create necessary directories if they don't exist
mkdir -p /app/config /app/templates /app/data /app/logs

# Copy default config file if it doesn't exist in the mounted volume
if [ ! -f /config/config.json ]; then
    echo "Config file not found in volume, copying default config..."
    cp /app/config/config.json /config/
fi

# Copy default templates if they don't exist in the mounted volume
for template_file in /app/templates/*.j2; do
    filename=$(basename "$template_file")
    if [ ! -f "/templates/$filename" ]; then
        echo "Template file $filename not found in volume, copying default template..."
        cp "$template_file" "/templates/"
    fi
done

# Create symlinks to use the mounted volumes
rm -rf /app/config/* /app/templates/*
ln -sf /config/* /app/config/
ln -sf /templates/* /app/templates/

# Set proper permissions
chmod -R 755 /app/config /app/templates /app/data /app/logs
chmod -R 755 /config /templates

echo "Configuration initialized. Starting application..."

# Execute the command (likely python main.py)
exec "$@"