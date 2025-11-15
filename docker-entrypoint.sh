#!/bin/bash
set -e

echo "Starting AI Browser Container..."
echo "Configuration:"
echo "  LLM Provider: ${LLM_PROVIDER:-gemini}"
echo "  Chromium Headless: ${CHROMIUM_HEADLESS:-true}"
echo "  Chrome Debug Port: ${CHROME_DEBUG_PORT:-9222}"

# Validate required environment variables
if [ "${LLM_PROVIDER:-gemini}" = "gemini" ] && [ -z "$GEMINI_API_KEY" ]; then
    echo "ERROR: GEMINI_API_KEY is required when LLM_PROVIDER=gemini"
    exit 1
fi

# Create cache directory if it doesn't exist
mkdir -p /root/.cache/aibrowser/chromium_profile

# Execute the command
exec "$@"


