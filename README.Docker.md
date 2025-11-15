# Docker Deployment Guide

This guide explains how to deploy the AI Browser application using Docker.

## Prerequisites

- Docker Engine 20.10+ installed
- Docker Compose 2.0+ installed (or use Docker Compose V2 plugin)
- Environment variables configured (see `.env.example`)

## Quick Start

### 1. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 2. Build and Run

```bash
# Build the Docker image
docker-compose build

# Start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

## Configuration

### Environment Variables

The application uses environment variables for configuration. Key variables:

- **GEMINI_API_KEY** (required): Your Google Gemini API key
- **CHROMIUM_HEADLESS**: Set to `true` for headless mode (default: `true`)
- **CHROME_DEBUG_PORT**: CDP debugging port (default: `9222`)

See `.env.example` for all available options.

### Running Modes

#### Standard Mode (Text Input)

```bash
docker-compose up aibrowser
```

#### Voice Mode

Requires ElevenLabs and Deepgram API keys:

```bash
docker-compose --profile voice up aibrowser-voice
```

## Docker Compose Services

### `aibrowser`

Main service running the interactive browser agent.

**Ports:**
- `9222`: Chrome DevTools Protocol (CDP) port

**Volumes:**
- `chromium_profile`: Persists browser profile data

### `aibrowser-voice` (profile: voice)

Voice-enabled service using Pipecat for audio interaction.

**Ports:**
- `9223`: Chrome DevTools Protocol (CDP) port

**Volumes:**
- `chromium_profile_voice`: Persists browser profile data

## Building the Image

```bash
# Build with Docker Compose
docker-compose build

# Or build directly with Docker
docker build -t aibrowser:latest .
```

## Running Standalone Container

```bash
docker run -it --rm \
  -e GEMINI_API_KEY=your_key \
  -e CHROMIUM_HEADLESS=true \
  -p 9222:9222 \
  --shm-size=2gb \
  aibrowser:latest
```

## Development

For development with live code reload:

```bash
# Mount local code as volume
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

(You would need to create `docker-compose.dev.yml` for development overrides)

## Troubleshooting

### Chromium fails to start

- Ensure Docker has sufficient shared memory (`--shm-size=2gb`)
- Check logs: `docker-compose logs aibrowser`
- Verify Playwright browsers are installed (they should be in the image)

### API key errors

- Verify `.env` file exists and contains valid API keys
- Check environment variables: `docker-compose config`

### Port conflicts

- Change `CHROME_DEBUG_PORT` in `.env` if port 9222 is already in use
- Update port mapping in `docker-compose.yml`

## Production Deployment

For production deployment:

1. Use environment-specific `.env` files
2. Set up proper secrets management
3. Configure logging and monitoring
4. Use a reverse proxy (nginx, traefik) if exposing HTTP endpoints
5. Set resource limits in `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 4G
```

## Next Steps

After Docker setup, consider:

- Setting up a reverse proxy (nginx/traefik)
- Adding health checks
- Configuring logging aggregation
- Setting up monitoring (Prometheus, Grafana)
- Deploying to cloud (AWS, GCP, Azure, DigitalOcean)


