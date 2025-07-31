.PHONY: help build start stop restart logs health stats sync shell db backup test clean

help:  ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build:  ## Build the Docker image
	docker-compose build

start:  ## Start the service
	docker-compose up -d

stop:  ## Stop the service
	docker-compose down

restart:  ## Restart the service
	docker-compose restart

logs:  ## Show live logs
	docker-compose logs -f

health:  ## Check service health
	curl -s http://localhost:8080/health | jq '.'

stats:  ## Show database statistics
	curl -s http://localhost:8080/stats | jq '.'

sync:  ## Trigger manual library sync
	curl -X POST http://localhost:8080/sync

shell:  ## Open shell in container
	docker exec -it jellyfin-discord-webhook bash

db:  ## Open database shell
	docker exec -it jellyfin-discord-webhook sqlite3 /app/data/jellyfin_items.db

backup:  ## Create database backup
	@mkdir -p backups
	@timestamp=$$(date +%Y%m%d_%H%M%S) && \
	docker exec jellyfin-discord-webhook sqlite3 /app/data/jellyfin_items.db ".backup /app/data/backup_$${timestamp}.db" && \
	docker cp jellyfin-discord-webhook:/app/data/backup_$${timestamp}.db ./backups/ && \
	echo "Backup created: ./backups/backup_$${timestamp}.db"

test:  ## Run webhook tests
	python scripts/test_webhook.py

test-multi:  ## Test multi-webhook routing
	python scripts/test_webhook.py multi-webhook

webhooks:  ## Show webhook configuration and status
	curl -s http://localhost:8080/webhooks | jq '.'

test-webhook:  ## Test webhook (specify webhook name with WEBHOOK=name)
	curl -X POST "http://localhost:8080/test-webhook?webhook_name=$(or $(WEBHOOK),default)" | jq '.'

validate:  ## Validate configuration
	python scripts/config_validator.py

template-test:  ## Test Jinja2 templates
	python scripts/template_tester.py

clean:  ## Clean up Docker resources
	docker system prune -f
	docker volume prune -f

deploy:  ## Full deployment with validation
	@echo "🚀 Starting deployment..."
	@python scripts/config_validator.py
	@docker-compose up -d --build
	@echo "⏳ Waiting for service..."
	@sleep 10
	@make health
	@echo "🎉 Deployment complete!"