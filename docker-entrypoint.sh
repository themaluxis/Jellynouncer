#!/bin/bash
set -e

# Create necessary directories if they don't exist
mkdir -p /app/config /app/templates /app/data /app/logs

# Check and copy default configuration files if they don't exist
if [ ! -f /app/config/config.json ]; then
    echo "Config file not found, copying default configuration..."
    cp /app/defaults/config.json /app/config/
fi

# Copy default templates if they don't exist
for template in new_item.j2 upgraded_item.j2 server_status.j2
do
    if [ ! -f "/app/templates/$template" ]; then
        echo "Template $template not found, copying default..."
        cp "/app/defaults/templates/$template" "/app/templates/"
    fi
done

# Copy script files if they don't exist and make them executable
if [ "$(ls -A /app/defaults/scripts)" ]; then
    for script in /app/defaults/scripts/*
    do
        script_name=$(basename "$script")
        if [ ! -f "/app/scripts/$script_name" ]; then
            echo "Script $script_name not found, copying default..."
            cp "$script" "/app/scripts/$script_name"
            chmod +x "/app/scripts/$script_name"
        else
            # Ensure existing scripts are executable
            chmod +x "/app/scripts/$script_name"
        fi
    done
fi

# Set proper permissions for mounted volumes
# This handles the case where volumes are mounted by a user with different UID/GID
if [ -n "${PUID}" ] && [ -n "${PGID}" ]; then
    echo "🔐 Setting permissions for user ${PUID}:${PGID}"
    chown -R ${PUID}:${PGID} /app/config /app/templates /app/data /app/logs /app/scripts
fi

# Create symlinks to use the mounted volumes
rm -rf /app/config/* /app/templates/*
ln -sf /config/* /app/config/
ln -sf /templates/* /app/templates/

# Set proper permissions
chmod -R 755 /app/config /app/templates /app/data /app/logs
chmod -R 755 /config /templates

echo "Configuration initialized. Starting application..."

# Execute main.py
exec "$@"