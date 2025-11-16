# Multi-stage build for AI Browser application
FROM python:3.13-slim AS base

# Install system dependencies for Chromium/Playwright and audio
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
    libasound2-dev \
    portaudio19-dev \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install uv first
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
COPY . .

# Create cache directory for Chromium profile
RUN mkdir -p /root/.cache/aibrowser/chromium_profile

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV CHROMIUM_HEADLESS=true
ENV CHROME_DEBUG_PORT=9222
ENV PYTHONPATH=/app

# Expose CDP port
EXPOSE 9222

# Use entrypoint script
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default command (can be overridden)
CMD ["python", "-m", "aibrowser.main"]

