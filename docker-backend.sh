#!/bin/bash
# Helper script for Docker backend operations

set -e

COMPOSE_FILE="docker-compose.backend.yml"

case "$1" in
    build)
        echo "Building backend Docker image..."
        docker-compose -f "$COMPOSE_FILE" build
        ;;
    start)
        echo "Starting backend..."
        docker-compose -f "$COMPOSE_FILE" up
        ;;
    start-bg)
        echo "Starting backend in background..."
        docker-compose -f "$COMPOSE_FILE" up -d
        ;;
    stop)
        echo "Stopping backend..."
        docker-compose -f "$COMPOSE_FILE" down
        ;;
    restart)
        echo "Restarting backend..."
        docker-compose -f "$COMPOSE_FILE" restart
        ;;
    logs)
        echo "Showing backend logs..."
        docker-compose -f "$COMPOSE_FILE" logs -f backend
        ;;
    shell)
        echo "Opening shell in backend container..."
        docker-compose -f "$COMPOSE_FILE" exec backend bash
        ;;
    rebuild)
        echo "Rebuilding backend (no cache)..."
        docker-compose -f "$COMPOSE_FILE" build --no-cache
        docker-compose -f "$COMPOSE_FILE" up
        ;;
    clean)
        echo "Cleaning up backend (including volumes)..."
        docker-compose -f "$COMPOSE_FILE" down -v
        ;;
    health)
        echo "Checking backend health..."
        curl -f http://localhost:8000/health || echo "Backend is not responding"
        ;;
    status)
        echo "Backend container status:"
        docker-compose -f "$COMPOSE_FILE" ps
        ;;
    *)
        echo "Usage: $0 {build|start|start-bg|stop|restart|logs|shell|rebuild|clean|health|status}"
        echo ""
        echo "Commands:"
        echo "  build      - Build the Docker image"
        echo "  start      - Start backend (foreground)"
        echo "  start-bg   - Start backend (background)"
        echo "  stop       - Stop backend"
        echo "  restart    - Restart backend"
        echo "  logs       - Show and follow logs"
        echo "  shell      - Open shell in container"
        echo "  rebuild    - Rebuild without cache"
        echo "  clean      - Stop and remove volumes"
        echo "  health     - Check health endpoint"
        echo "  status     - Show container status"
        exit 1
        ;;
esac

