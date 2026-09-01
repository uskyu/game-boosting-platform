# =============================================================================
# Makefile - Game Boosting Platform
# Simplifies Docker and development commands
# =============================================================================

.PHONY: help build up down restart logs shell migrate clean dev prod

# Default target
help:
	@echo "Game Boosting Platform - Available Commands:"
	@echo ""
	@echo "  make build      - Build all Docker images"
	@echo "  make up         - Start all services (production)"
	@echo "  make down       - Stop all services"
	@echo "  make restart    - Restart all services"
	@echo "  make logs       - View logs from all services"
	@echo "  make migrate    - Run database migrations"
	@echo "  make shell      - Open shell in backend container"
	@echo "  make clean      - Remove all containers, images, and volumes"
	@echo "  make dev        - Start development environment"
	@echo "  make prod       - Start production environment"
	@echo ""

# =============================================================================
# Docker Commands
# =============================================================================

# Build all images
build:
	docker-compose build

# Start services (production)
up:
	docker-compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 10
	@echo "Services started. Running migrations..."
	@make migrate
	@echo ""
	@echo "✅ Game Boosting Platform is running!"
	@echo "   Frontend: http://localhost:80"
	@echo "   Backend:  http://localhost:8000"
	@echo "   API Docs: http://localhost:8000/api/v1/docs"

# Stop services
down:
	docker-compose down

# Restart services
restart:
	docker-compose restart

# View logs
logs:
	docker-compose logs -f

# Logs for specific service
logs-db:
	docker-compose logs -f db

logs-backend:
	docker-compose logs -f backend

logs-frontend:
	docker-compose logs -f frontend

# =============================================================================
# Database Commands
# =============================================================================

# Run migrations
migrate:
	docker-compose exec backend alembic upgrade head

# Create new migration
migration:
	@read -p "Migration message: " msg; \
	docker-compose exec backend alembic revision --autogenerate -m "$$msg"

# Rollback migration
rollback:
	docker-compose exec backend alembic downgrade -1

# =============================================================================
# Development Commands
# =============================================================================

# Open shell in backend container
shell:
	docker-compose exec backend /bin/bash

# Open MySQL shell
mysql:
	docker-compose exec db mysql -u boosting_user -p game_boosting

# Development mode (with hot reload)
dev:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
	@echo ""
	@echo "✅ Development environment started!"
	@echo "   Frontend (Vite): http://localhost:3000"
	@echo "   Backend (Reload): http://localhost:8000"
	@echo "   API Docs: http://localhost:8000/api/v1/docs"

# Production mode
prod:
	docker-compose up -d --build

# =============================================================================
# Cleanup Commands
# =============================================================================

# Stop and remove all containers
clean:
	docker-compose down -v --remove-orphans
	docker system prune -f

# Full cleanup including images
clean-all:
	docker-compose down -v --remove-orphans --rmi all
	docker system prune -af

# =============================================================================
# Status Commands
# =============================================================================

# Show status of all services
status:
	docker-compose ps

# Health check
health:
	@echo "Checking service health..."
	@curl -s http://localhost:8000/health | python -m json.tool || echo "Backend not responding"
	@curl -s http://localhost:80/nginx-health || echo "Frontend not responding"
