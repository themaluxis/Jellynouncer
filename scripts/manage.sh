#!/bin/bash
# scripts/manage.sh - Management script

CONTAINER_NAME="jellyfin-discord-webhook"

case "$1" in
    "start")
        echo "Starting Jellyfin Discord Webhook Service..."
        docker-compose up -d
        ;;
    "stop")
        echo "Stopping Jellyfin Discord Webhook Service..."
        docker-compose down
        ;;
    "restart")
        echo "Restarting Jellyfin Discord Webhook Service..."
        docker-compose restart
        ;;
    "logs")
        echo "Showing logs (press Ctrl+C to exit)..."
        docker-compose logs -f
        ;;
    "health")
        echo "Checking service health..."
        curl -s http://localhost:8080/health | jq '.'
        ;;
    "webhooks")
        echo "Getting webhook status..."
        curl -s http://localhost:8080/webhooks | jq '.'
        ;;
    "test-webhook")
        webhook_name="${2:-default}"
        echo "Testing webhook: $webhook_name..."
        curl -X POST "http://localhost:8080/test-webhook?webhook_name=$webhook_name" | jq '.'
        ;;
    "sync")
        echo "Triggering manual library sync..."
        curl -X POST http://localhost:8080/sync | jq '.'
        ;;
    "shell")
        echo "Opening shell in container..."
        docker exec -it $CONTAINER_NAME bash
        ;;
    "db")
        echo "Opening database shell..."
        docker exec -it $CONTAINER_NAME sqlite3 /app/data/jellyfin_items.db
        ;;
    "backup")
        echo "Creating database backup..."
        timestamp=$(date +%Y%m%d_%H%M%S)
        docker exec $CONTAINER_NAME sqlite3 /app/data/jellyfin_items.db ".backup /app/data/backup_${timestamp}.db"
        docker cp $CONTAINER_NAME:/app/data/backup_${timestamp}.db ./backups/
        echo "Backup created: ./backups/backup_${timestamp}.db"
        ;;
    "restore")
        if [ -z "$2" ]; then
            echo "Usage: $0 restore <backup_file>"
            echo "Available backups:"
            ls -la ./backups/
            exit 1
        fi
        echo "Restoring database from $2..."
        docker cp ./backups/$2 $CONTAINER_NAME:/app/data/restore.db
        docker exec $CONTAINER_NAME bash -c "mv /app/data/jellyfin_items.db /app/data/jellyfin_items.db.old && mv /app/data/restore.db /app/data/jellyfin_items.db"
        echo "Database restored. Restarting service..."
        docker-compose restart
        ;;
    "cleanup")
        echo "Cleaning up old data..."
        docker system prune -f
        docker volume prune -f
        echo "Cleanup complete."
        ;;
    *)
        echo "Jellyfin Discord Webhook Service Management"
        echo ""
        echo "Usage: $0 {start|stop|restart|logs|health|stats|webhooks|test-webhook|sync|shell|db|backup|restore|cleanup}"
        echo ""
        echo "Commands:"
        echo "  start         - Start the service"
        echo "  stop          - Stop the service"
        echo "  restart       - Restart the service"
        echo "  logs          - Show live logs"
        echo "  health        - Check service health"
        echo "  stats         - Show database statistics"
        echo "  webhooks      - Show webhook configuration and status"
        echo "  test-webhook  - Test a webhook (usage: $0 test-webhook [webhook_name])"
        echo "  sync          - Trigger manual library sync"
        echo "  shell         - Open shell in container"
        echo "  db            - Open database shell"
        echo "  backup        - Create database backup"
        echo "  restore       - Restore database from backup"
        echo "  cleanup       - Clean up Docker resources"
        exit 1
        ;;
esac
