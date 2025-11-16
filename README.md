# AI Voice Controlled Browser POC

A proof-of-concept application that demonstrates a voice agent capable of performing browser navigation and completing multi-step tasks on websites. Built using **Pipecat** for voice interaction and **Browser Use** for browser automation.

## Overview

This POC showcases a voice-controlled browser agent that can:
- Accept voice commands through speech recognition (Deepgram)
- Verbally explain its actions as it navigates and interacts with web pages
- Complete multi-step tasks with natural language narration
- Pause before final actions to explain options and wait for user confirmation


## Features

- **Voice Mode**: Voice-controlled browser interaction using Pipecat, Deepgram (STT), and ElevenLabs (TTS)
- **Text Mode**: Interactive command-line interface for browser control
- **Multiple LLM Providers**: Support for Gemini, Claude, and OpenAI
- **Real-time Browser Control**: Direct control of Chromium browser via Chrome DevTools Protocol
- **Verbal Narration**: Agent explains its actions verbally as it navigates and interacts with web pages

## Prerequisites

- Python 3.8 or higher
- Chrome or Chromium browser installed
- [uv](https://github.com/astral-sh/uv) package manager
- API keys for:
  - LLM provider (Gemini, Claude, or OpenAI)
  - Deepgram (for voice mode - speech-to-text)
  - ElevenLabs (for voice mode - text-to-speech)

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/mafex11/AI-CONTROLLED-BROWSER-POC
   cd AI-CONTROLLED-BROWSER-POC
   ```

2. **Create a virtual environment:**
   ```bash
   python -m venv venv
   ```

3. **Activate the virtual environment:**
   
   On Windows:
   ```bash
   venv\Scripts\activate
   ```
   
   On macOS/Linux:
   ```bash
   source venv/bin/activate
   ```

4. **Install uv:**
   
   Follow the installation instructions at [https://github.com/astral-sh/uv](https://github.com/astral-sh/uv)
   
   Or install via pip:
   ```bash
   pip install uv
   ```

5. **Install dependencies:**
   ```bash
   uv pip install -r requirements.txt
   ```

## Configuration

Create a `.env` file in the project root with your API keys:

```env
# LLM Provider (choose one: gemini, claude, or openai)
LLM_PROVIDER=gemini

# Gemini Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

# Claude Configuration (if using Claude)
# ANTHROPIC_API_KEY=your_anthropic_api_key_here
# CLAUDE_MODEL=claude-sonnet-4-5

# OpenAI Configuration (if using OpenAI)
# OPENAI_API_KEY=your_openai_api_key_here
# OPENAI_MODEL=gpt-5-nano

# Voice Mode Configuration (required for voice mode)
DEEPGRAM_API_KEY=your_deepgram_api_key_here
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM

# Optional Configuration
DEFAULT_SEARCH_ENGINE=google
CHROME_DEBUG_PORT=9222
CHROMIUM_HEADLESS=false
```

## Usage

### Text Mode

Run the interactive text-based browser control:

```bash
python -m aibrowser.main
```

In text mode, you can type commands and the AI will execute browser actions. Type `exit`, `quit`, or `q` to stop.

### Voice Controlled Mode (POC Demo)

Run the voice-controlled browser:

```bash
python -m aibrowser.main_voice
```

In voice mode, speak your commands and the AI will respond with voice feedback. The agent will:
- Listen to your voice commands via Deepgram speech-to-text
- Verbally explain each action it takes
- Navigate and interact with web pages automatically
- Pause before final actions to explain options and wait for confirmation


**Note**: Voice mode requires both `DEEPGRAM_API_KEY` and `ELEVENLABS_API_KEY` to be set in your `.env` file.

## Environment Variables

### Required Variables

- `LLM_PROVIDER`: One of `gemini`, `claude`, or `openai`
- API key for your chosen LLM provider:
  - `GEMINI_API_KEY` (if using Gemini)
  - `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` (if using Claude)
  - `OPENAI_API_KEY` (if using OpenAI)
- `DEEPGRAM_API_KEY`: Required for voice mode
- `ELEVENLABS_API_KEY`: Required for voice mode

### Optional Variables

- `GEMINI_MODEL`: Gemini model name (default: `gemini-2.5-flash`)
- `CLAUDE_MODEL`: Claude model name (default: `claude-sonnet-4-1`)
- `OPENAI_MODEL`: OpenAI model name (default: `gpt-5-nano`)
- `ELEVENLABS_VOICE_ID`: ElevenLabs voice ID (default: `21m00Tcm4TlvDq8ikWAM`)
- `DEEPGRAM_LANGUAGE`: Language code for Deepgram (default: `en-US`)
- `DEFAULT_SEARCH_ENGINE`: Default search engine (default: `google`)
- `CHROME_DEBUG_PORT`: Chrome DevTools Protocol port (default: `9222`)
- `CHROMIUM_HEADLESS`: Run browser in headless mode (default: `false`)

