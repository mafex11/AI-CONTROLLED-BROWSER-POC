@echo off
REM Helper script for Docker backend operations (Windows)

SET COMPOSE_FILE=docker-compose.backend.yml

IF "%1"=="build" (
    echo Building backend Docker image...
    docker-compose -f %COMPOSE_FILE% build
    GOTO END
)

IF "%1"=="start" (
    echo Starting backend...
    docker-compose -f %COMPOSE_FILE% up
    GOTO END
)

IF "%1"=="start-bg" (
    echo Starting backend in background...
    docker-compose -f %COMPOSE_FILE% up -d
    GOTO END
)

IF "%1"=="stop" (
    echo Stopping backend...
    docker-compose -f %COMPOSE_FILE% down
    GOTO END
)

IF "%1"=="restart" (
    echo Restarting backend...
    docker-compose -f %COMPOSE_FILE% restart
    GOTO END
)

IF "%1"=="logs" (
    echo Showing backend logs...
    docker-compose -f %COMPOSE_FILE% logs -f backend
    GOTO END
)

IF "%1"=="shell" (
    echo Opening shell in backend container...
    docker-compose -f %COMPOSE_FILE% exec backend bash
    GOTO END
)

IF "%1"=="rebuild" (
    echo Rebuilding backend (no cache)...
    docker-compose -f %COMPOSE_FILE% build --no-cache
    docker-compose -f %COMPOSE_FILE% up
    GOTO END
)

IF "%1"=="clean" (
    echo Cleaning up backend (including volumes)...
    docker-compose -f %COMPOSE_FILE% down -v
    GOTO END
)

IF "%1"=="health" (
    echo Checking backend health...
    curl -f http://localhost:8000/health || echo Backend is not responding
    GOTO END
)

IF "%1"=="status" (
    echo Backend container status:
    docker-compose -f %COMPOSE_FILE% ps
    GOTO END
)

echo Usage: %0 {build^|start^|start-bg^|stop^|restart^|logs^|shell^|rebuild^|clean^|health^|status}
echo.
echo Commands:
echo   build      - Build the Docker image
echo   start      - Start backend (foreground)
echo   start-bg   - Start backend (background)
echo   stop       - Stop backend
echo   restart    - Restart backend
echo   logs       - Show and follow logs
echo   shell      - Open shell in container
echo   rebuild    - Rebuild without cache
echo   clean      - Stop and remove volumes
echo   health     - Check health endpoint
echo   status     - Show container status

:END

