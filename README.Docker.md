# AI Browser - Docker Deployment Guide

Complete guide for deploying and using the AI Browser application with Docker.

## Overview

AI Browser is an AI-powered browser automation tool that uses LLMs (Gemini, Claude, or OpenAI) to control a real Chromium browser. It can perform web tasks through text commands, voice interaction, or API calls.

## Prerequisites

- **Docker Engine 20.10+** installed
- **Docker Compose 2.0+** installed (or Docker Compose V2 plugin)
- **API Keys** for at least one LLM provider:
  - Google Gemini API key (for Gemini)
  - Anthropic API key (for Claude)
  - OpenAI API key (for OpenAI)
- **Voice Mode** (optional) requires:
  - ElevenLabs API key
  - Deepgram API key

## Quick Start

### 1. Create `.env` File

Create a `.env` file in the project root (same directory as `docker-compose.yml`) with your API keys:

```bash
# LLM Provider (choose one: gemini, claude, openai)
LLM_PROVIDER=claude

# Claude Configuration (required if LLM_PROVIDER=claude)
ANTHROPIC_API_KEY=your_anthropic_api_key_here
CLAUDE_MODEL=claude-sonnet-4-1

# Gemini Configuration (required if LLM_PROVIDER=gemini)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# OpenAI Configuration (required if LLM_PROVIDER=openai)
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o

# Browser Configuration
CHROMIUM_HEADLESS=true
CHROME_DEBUG_PORT=9222
DEFAULT_SEARCH_ENGINE=google

# Voice Mode (optional, required for voice mode)
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
DEEPGRAM_API_KEY=your_deepgram_api_key_here
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
DEEPGRAM_LANGUAGE=en-US

# Screenshot Configuration (optional)
SAVE_HIGHLIGHT_SCREENSHOTS=true
SCREENSHOT_DIR=./my_screenshots
```

### 2. Build and Run

```bash
# Build the Docker image
docker-compose build

# Start the service (text mode by default)
docker-compose up -d

# View logs
docker-compose logs -f aibrowser

# Stop the service
docker-compose down
```

## Running Modes

The application supports three modes of operation:

### 1. Text Mode (Interactive Command Line)

Default mode for text-based browser automation.

**Start:**
```bash
docker-compose up -d aibrowser
```

**Interact:**
```bash
# Attach to the running container
docker attach aibrowser
```

Type your browser tasks directly. The agent will execute them step by step.

**Example commands:**
- `Search for Python tutorials`
- `Go to GitHub and find the trending repositories`
- `Open Wikipedia and search for artificial intelligence`

**Detach from container:**
- Press `Ctrl+P` then `Ctrl+Q` to detach (keeps container running)
- Or press `Ctrl+C` to stop the container

**Stop:**
```bash
docker-compose stop aibrowser
```

### 2. Voice Mode (Speech Interaction)

Voice-enabled mode using Pipecat for audio input/output.

**Start:**
```bash
docker-compose --profile voice up -d aibrowser-voice
```

**View logs:**
```bash
docker-compose --profile voice logs -f aibrowser-voice
```

**Stop:**
```bash
docker-compose --profile voice stop aibrowser-voice
```

**⚠️ Important Note:** Voice mode requires audio devices. In Docker containers, audio access is limited. Voice mode may not work properly in headless Docker environments without proper audio device passthrough. For production voice usage, consider using the API mode with a frontend that handles audio on the client side.

### 3. API Mode (Web/Frontend Integration)

REST API server for integration with web frontends or other applications.

**Start:**
```bash
docker-compose --profile api up -d aibrowser-api
```

**Access:**
- API Server: `http://localhost:8000`
- CDP Port: `9224`
- API Documentation: `http://localhost:8000/docs`

**Stop:**
```bash
docker-compose --profile api stop aibrowser-api
```

**API Endpoints:**
- `POST /api/query` - Submit a browser task (returns SSE stream)
- `GET /api/screenshot` - Get current browser screenshot
- `POST /api/cleanup` - Clean up browser resources
- `WS /api/voice` - WebSocket endpoint for voice mode

## Switching Between Modes

Only one mode should run at a time (they use different CDP ports). To switch:

```bash
# Stop current mode
docker-compose stop aibrowser

# Start different mode
docker-compose --profile voice up -d aibrowser-voice
# or
docker-compose --profile api up -d aibrowser-api
# or
docker-compose up -d aibrowser  # text mode
```

## Configuration

### Environment Variables

All configuration is done through the `.env` file. Key variables:

#### LLM Provider Settings

**For Claude:**
```bash
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=your_key
CLAUDE_MODEL=claude-sonnet-4-1  # Options: claude-sonnet-4-0, claude-sonnet-4-1, claude-3-5-sonnet-20241022, claude-3-5-sonnet-latest
CLAUDE_TEMPERATURE=0.3
CLAUDE_MAX_TOKENS=4096
```

**For Gemini:**
```bash
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TEMPERATURE=0.3
GEMINI_MAX_OUTPUT_TOKENS=8000
```

**For OpenAI:**
```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key
OPENAI_MODEL=gpt-4o  # Options: gpt-4o, gpt-4-turbo, gpt-4o-mini, o1-preview, o3-mini
OPENAI_TEMPERATURE=0.2
OPENAI_MAX_TOKENS=4096
```

#### Browser Settings

```bash
CHROMIUM_HEADLESS=true          # Set to true for Docker (required)
CHROME_DEBUG_PORT=9222          # CDP port (text mode)
DEFAULT_SEARCH_ENGINE=google    # google, bing, duckduckgo
```

#### Voice Settings

```bash
ELEVENLABS_API_KEY=your_key
DEEPGRAM_API_KEY=your_key
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM  # Default: Rachel
DEEPGRAM_LANGUAGE=en-US
```

#### Screenshot Settings

```bash
SAVE_HIGHLIGHT_SCREENSHOTS=true
SCREENSHOT_DIR=./my_screenshots  # Relative to container /app directory
```

Screenshots are saved to `./my_screenshots` on your host machine (mounted as a volume).

## Docker Compose Services

### `aibrowser` (Text Mode)

- **Port:** `9222` (CDP)
- **Command:** `python -m aibrowser.main`
- **Volumes:**
  - `chromium_profile` - Browser profile data
  - `./my_screenshots:/app/my_screenshots` - Screenshot directory

### `aibrowser-voice` (Voice Mode)

- **Port:** `9223` (CDP)
- **Command:** `python -m aibrowser.main_voice`
- **Profile:** `voice`
- **Volumes:**
  - `chromium_profile_voice` - Browser profile data
  - `./my_screenshots:/app/my_screenshots` - Screenshot directory

### `aibrowser-api` (API Mode)

- **Ports:** `8000` (API), `9224` (CDP)
- **Command:** `python run_api_server.py`
- **Profile:** `api`
- **Volumes:**
  - `chromium_profile_api` - Browser profile data
  - `./my_screenshots:/app/my_screenshots` - Screenshot directory

## Common Commands

```bash
# Build image
docker-compose build

# Start text mode
docker-compose up -d aibrowser

# Start voice mode
docker-compose --profile voice up -d aibrowser-voice

# Start API mode
docker-compose --profile api up -d aibrowser-api

# View logs
docker-compose logs -f aibrowser
docker-compose --profile voice logs -f aibrowser-voice
docker-compose --profile api logs -f aibrowser-api

# Attach to text mode container
docker attach aibrowser

# Check running containers
docker-compose ps

# Stop specific service
docker-compose stop aibrowser

# Stop all services
docker-compose down

# Restart with new config
docker-compose down && docker-compose up -d
```

## Troubleshooting

### Container Won't Start / API Key Errors

**Error:** `ERROR: ANTHROPIC_API_KEY or CLAUDE_API_KEY is required`

**Solution:**
1. Verify your `.env` file is in the project root (same directory as `docker-compose.yml`)
2. Check the variable name is exactly `ANTHROPIC_API_KEY` (case-sensitive)
3. Ensure there are no extra spaces or quotes around the value
4. Restart the container: `docker-compose restart aibrowser`

### Chromium Fails to Start

**Error:** `Failed to start Chromium`

**Solutions:**
- Ensure Docker has sufficient shared memory (`--shm-size=2gb` is set in docker-compose.yml)
- Check logs: `docker-compose logs aibrowser`
- Verify Playwright browsers are installed (they should be in the image)

### Voice Mode Audio Errors

**Error:** `[Errno -9996] Invalid input device (no default output device)`

**Cause:** Docker containers don't have direct access to host audio devices by default.

**Solutions:**
1. **Use API Mode Instead:** For production, use API mode with a frontend that handles audio on the client side
2. **Audio Passthrough (Linux):** Mount audio devices (complex, not recommended for most use cases)
3. **Run Locally:** Voice mode works better when running directly on the host (not in Docker)

**Recommendation:** Use text mode or API mode for Docker deployments. Voice mode is better suited for local development.

### Port Conflicts

**Error:** Port already in use

**Solution:**
- Change `CHROME_DEBUG_PORT` in `.env` to a different port
- Update port mapping in `docker-compose.yml` if needed
- Check what's using the port: `netstat -ano | findstr :9222` (Windows) or `lsof -i :9222` (Linux/Mac)

### Model Name Errors

**Error:** Invalid model name

**Solution:** Use valid model names:
- **Claude:** `claude-sonnet-4-0`, `claude-sonnet-4-1`, `claude-3-5-sonnet-20241022`, `claude-3-5-sonnet-latest`
- **OpenAI:** `gpt-4o`, `gpt-4-turbo`, `gpt-4o-mini`, `o1-preview`, `o3-mini`
- **Gemini:** `gemini-2.5-flash`, `gemini-pro`, etc.

### Screenshots Not Saving

**Solution:**
- Verify `SAVE_HIGHLIGHT_SCREENSHOTS=true` in `.env`
- Check the `./my_screenshots` directory exists and is writable
- Verify the volume mount in `docker-compose.yml`: `./my_screenshots:/app/my_screenshots`
- Screenshots are saved relative to `/app` in the container, which maps to `./my_screenshots` on the host

### Container Keeps Restarting

**Solution:**
- Check logs: `docker-compose logs aibrowser`
- Verify all required environment variables are set
- Ensure API keys are valid
- Check for dependency conflicts (see build logs)

## Production Deployment

For production deployment:

1. **Use Environment-Specific `.env` Files**
   - Don't commit `.env` to version control
   - Use secrets management (Docker secrets, AWS Secrets Manager, etc.)

2. **Set Resource Limits**
   Add to `docker-compose.yml`:
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '2'
         memory: 4G
   ```

3. **Configure Logging**
   - Set up log aggregation (ELK stack, CloudWatch, etc.)
   - Configure log rotation

4. **Use Reverse Proxy**
   - Use nginx or traefik for API mode
   - Set up SSL/TLS certificates
   - Configure rate limiting

5. **Health Checks**
   - Add health check endpoints
   - Configure container health checks

6. **Monitoring**
   - Set up monitoring (Prometheus, Grafana)
   - Configure alerts for container failures

## Security Notes

- **Never commit `.env` files** to version control
- **Rotate API keys** if they're accidentally exposed
- **Use secrets management** in production
- **Limit network exposure** - only expose necessary ports
- **Keep Docker images updated** - regularly rebuild with security updates

## Next Steps

- Set up a reverse proxy (nginx/traefik) for API mode
- Add health checks and monitoring
- Configure logging aggregation
- Deploy to cloud (AWS, GCP, Azure, DigitalOcean)
- Integrate with frontend application
- Set up CI/CD pipeline

## Support

For issues or questions:
- Check the logs: `docker-compose logs -f`
- Verify environment variables: `docker-compose config`
- Review the troubleshooting section above
