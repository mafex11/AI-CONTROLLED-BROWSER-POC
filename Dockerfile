# Multi-stage build for AI Browser backend API
FROM python:3.13-slim AS base

# Install system dependencies for Chromium/Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    libxss1 \
    build-essential \
    portaudio19-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install uv for faster package installation
RUN pip install --no-cache-dir uv

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies using uv, excluding Windows-only packages
RUN grep -v -E "(pywin32|win32_setctime)" requirements.txt > /tmp/requirements-docker.txt && \
    uv pip install --system -r /tmp/requirements-docker.txt

# Install Playwright and browsers
RUN pip install playwright && \
    playwright install chromium && \
    playwright install-deps chromium

# Copy application code
COPY aibrowser/ ./aibrowser/
COPY run_api_server.py .
COPY run_webrtc_server.py .

# Create cache directory for Chromium profile
RUN mkdir -p /root/.cache/aibrowser/chromium_profile

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV CHROMIUM_HEADLESS=true
ENV CHROME_DEBUG_PORT=9224
ENV PYTHONPATH=/app
ENV PORT=8000

# Expose API port (Cloud Run uses PORT env var)
EXPOSE 8000

# Health check for Cloud Run
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/docs', timeout=5)" || exit 1

# Run the API server
CMD ["python", "run_api_server.py"]
