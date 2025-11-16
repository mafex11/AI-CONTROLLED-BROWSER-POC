# AI Browser Frontend

A Next.js frontend for the AI Browser project with Shadcn UI components and Framer Motion animations.

## Features

- Query text field for browser tasks
- Voice mode toggle button
- Real-time display of agent narration
- Screenshot display with expand/collapse
- Smooth animations with Framer Motion

## Getting Started

1. Install dependencies:
```bash
npm install
```

2. Run the development server:
```bash
npm run dev
```

3. Open [http://localhost:3000](http://localhost:3000) in your browser.

## Backend Setup

Before running the frontend, make sure the backend API server is running:

```bash
# From the project root
python run_api_server.py
```

The API server will run on `http://localhost:8000` by default.

## Environment Variables

Create a `.env.local` file in the frontend directory to configure the backend URL:

```
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

## Project Structure

- `app/` - Next.js app directory with pages and API routes
- `components/` - React components including Shadcn UI components
- `lib/` - Utility functions
- `app/api/query/` - API route that proxies to the Python backend

## Modes

### Text Mode
- Enter a query in the text field
- Click the Send button
- View real-time narration and screenshots as the agent works

### Voice Mode
- Toggle the voice mode button (microphone icon)
- The frontend connects to the backend via WebSocket
- Speak your browser tasks
- View agent responses in real-time

## Backend Integration

The frontend connects to a FastAPI backend (`aibrowser/api_server.py`) that:
- Handles text queries via Server-Sent Events (SSE)
- Handles voice mode via WebSocket connections
- Streams narration and screenshots in real-time
- Uses a separate browser instance (port 9223) to avoid conflicts with main.py/main_voice.py
