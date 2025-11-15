"""FastAPI server for frontend integration with text and voice modes."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from typing import Optional

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .browser_use_integration import BrowserUseIntegration
from .cdp_browser_manager import CDPBrowserManager
from .config import Config
from .voice.agent_bridge import AgentBridge
from .voice.pipecat_pipeline import VoicePipeline

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Browser API")

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
_browser_manager: Optional[CDPBrowserManager] = None
_text_integration: Optional[BrowserUseIntegration] = None
_voice_integration: Optional[BrowserUseIntegration] = None
_voice_pipeline: Optional[VoicePipeline] = None
_voice_bridge: Optional[AgentBridge] = None


class QueryRequest(BaseModel):
    query: str
    voiceMode: bool = False


async def get_screenshot_base64(integration: BrowserUseIntegration) -> Optional[str]:
    """Get screenshot from browser integration as base64 string.
    If a highlight screenshot is available (from _preview_and_capture_highlight), use that instead."""
    if not integration or not integration._state:
        return None
    
    try:
        # First, check if there's a stored highlight screenshot from the agent
        # This will have element markings that the regular screenshot won't have
        if integration._state and integration._state.agent:
            agent = integration._state.agent
            if hasattr(agent, '_last_highlight_screenshot_base64') and agent._last_highlight_screenshot_base64:
                logger.debug('Using stored highlight screenshot for frontend')
                return agent._last_highlight_screenshot_base64
        
        # Fallback to regular screenshot if no highlight screenshot available
        state = await integration._state.controller.refresh_state(
            include_dom=False,
            include_screenshot=True,
        )
        
        if state and hasattr(state, 'screenshot') and state.screenshot:
            screenshot_b64 = state.screenshot
            if isinstance(screenshot_b64, str):
                # Ensure it's a data URL or base64
                if screenshot_b64.startswith('data:image'):
                    return screenshot_b64
                elif not screenshot_b64.startswith('data:'):
                    return f'data:image/png;base64,{screenshot_b64}'
                return screenshot_b64
    except Exception as e:
        logger.debug('Failed to get screenshot: %s', e)
    
    return None


@app.on_event("startup")
async def startup():
    """Initialize browser manager and integrations."""
    global _browser_manager, _text_integration, _voice_integration
    
    if not Config.validate():
        logger.error("Configuration validation failed")
        sys.exit(1)
    
    logger.info("Starting API server...")
    
    port = int(os.getenv('CHROME_DEBUG_PORT', '9223'))  # Different port to avoid conflicts
    headless = os.getenv('CHROMIUM_HEADLESS', 'false').lower() in {'1', 'true', 'yes', 'on'}
    
    _browser_manager = CDPBrowserManager(port=port, headless=headless)
    started = await _browser_manager.start()
    if not started or _browser_manager.endpoint is None:
        logger.error('Failed to start Chromium for API server')
        sys.exit(1)
    
    ws_url = await _browser_manager.websocket_url()
    if not ws_url:
        logger.error('Failed to get WebSocket URL')
        sys.exit(1)
    
    logger.info(f'CDP endpoint ready: {_browser_manager.endpoint}')
    logger.info(f'WebSocket URL: {ws_url}')
    
    # Initialize text mode integration
    _text_integration = BrowserUseIntegration(
        cdp_url=ws_url,
        default_search_engine=Config.DEFAULT_SEARCH_ENGINE,
    )
    if not await _text_integration.initialize():
        logger.error('Failed to initialize text integration')
        sys.exit(1)
    
    logger.info("API server ready")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    global _browser_manager, _text_integration, _voice_integration, _voice_pipeline
    
    if _voice_pipeline:
        try:
            await _voice_pipeline.stop()
        except Exception:
            pass
    
    if _text_integration:
        try:
            await _text_integration.shutdown()
        except Exception:
            pass
    
    if _voice_integration:
        try:
            await _voice_integration.shutdown()
        except Exception:
            pass
    
    if _browser_manager:
        try:
            await _browser_manager.stop()
        except Exception:
            pass


@app.post("/api/query")
async def query_text(request: QueryRequest):
    """Handle text mode queries with SSE streaming."""
    global _text_integration
    
    if not _text_integration:
        return StreamingResponse(
            iter([b'data: {"type": "error", "error": "Integration not initialized"}\n\n']),
            media_type="text/event-stream",
        )
    
    async def generate():
        import json
        queue = asyncio.Queue()
        step_counter = [0]
        run_complete = asyncio.Event()
        run_result = [None]
        run_error = [None]
        
        async def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
            """Stream step updates to frontend via queue."""
            if phase == 'before':
                step_counter[0] = step
                
                # Get screenshot if available
                screenshot = None
                if _text_integration:
                    try:
                        screenshot = await get_screenshot_base64(_text_integration)
                    except Exception as e:
                        logger.debug('Failed to get screenshot: %s', e)
                
                # Queue step data
                data = {
                    "type": "step",
                    "step": step,
                    "narration": narration or "",
                    "reasoning": reasoning or "",
                    "tool": tool or "",
                    "screenshot": screenshot,
                }
                await queue.put(data)
        
        # Update callbacks
        original_step = _text_integration.step_callback
        _text_integration.update_callbacks(step_callback=step_callback)
        
        # Run agent in background task
        async def run_agent():
            try:
                result = await _text_integration.run(request.query, is_continuation=False)
                run_result[0] = result
            except Exception as e:
                run_error[0] = e
            finally:
                run_complete.set()
        
        run_task = asyncio.create_task(run_agent())
        
        try:
            while not run_complete.is_set() or not queue.empty():
                try:
                    # Wait for queue item with timeout
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=0.1)
                        yield f"data: {json.dumps(data)}\n\n".encode()
                    except asyncio.TimeoutError:
                        # Check if run completed
                        if run_complete.is_set():
                            break
                        continue
                except Exception as e:
                    logger.debug('Error in queue processing: %s', e)
            
            # Wait for run to complete
            await run_task
            
            # Send completion or error
            if run_error[0]:
                error_data = {"type": "error", "error": str(run_error[0])}
                yield f'data: {json.dumps(error_data)}\n\n'.encode()
            elif run_result[0]:
                completion_data = {
                    "type": "complete",
                    "success": run_result[0].get("success", False),
                    "message": run_result[0].get("message", ""),
                }
                yield f'data: {json.dumps(completion_data)}\n\n'.encode()
                    
        except Exception as e:
            logger.error('Error processing query: %s', e, exc_info=True)
            error_data = {"type": "error", "error": str(e)}
            yield f'data: {json.dumps(error_data)}\n\n'.encode()
        finally:
            # Restore original callback
            _text_integration.update_callbacks(step_callback=original_step)
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket):
    """Handle voice mode WebSocket connection."""
    global _voice_integration, _voice_pipeline, _voice_bridge, _browser_manager
    
    await websocket.accept()
    logger.info("Voice WebSocket connection established")
    
    pipeline_task = None
    connection_closed = False
    
    def is_connected() -> bool:
        """Check if websocket is still connected."""
        try:
            return websocket.client_state.name == "CONNECTED"
        except Exception:
            return False
    
    async def safe_send_json(data: dict) -> bool:
        """Safely send JSON to websocket."""
        if not is_connected():
            return False
        try:
            await websocket.send_json(data)
            return True
        except (WebSocketDisconnect, RuntimeError, Exception) as e:
            logger.debug("Failed to send WebSocket message: %s", e)
            return False
    
    try:
        # Initialize voice integration if not already done
        if not _voice_integration:
            if not Config.validate_voice():
                await safe_send_json({"type": "error", "error": "Voice configuration invalid"})
                return
            
            ws_url = await _browser_manager.websocket_url()
            if not ws_url:
                await safe_send_json({"type": "error", "error": "Failed to get WebSocket URL"})
                return
            
            _voice_integration = BrowserUseIntegration(
                cdp_url=ws_url,
                default_search_engine=Config.DEFAULT_SEARCH_ENGINE,
            )
            if not await _voice_integration.initialize():
                await safe_send_json({"type": "error", "error": "Failed to initialize voice integration"})
                return
        
        # Step callback to send step data (narration, screenshots) to frontend
        # Set this FIRST so it's preserved when bridge saves original_step
        async def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
            """Send step updates to frontend via WebSocket."""
            if phase == 'before':
                if not is_connected():
                    logger.debug('Step callback: WebSocket not connected, skipping step %d', step)
                    return
                
                logger.debug('Step callback: Sending step %d to frontend', step)
                
                # Get screenshot if available
                screenshot = None
                if _voice_integration:
                    try:
                        screenshot = await get_screenshot_base64(_voice_integration)
                    except Exception as e:
                        logger.debug('Failed to get screenshot: %s', e)
                
                # Send step data
                sent = await safe_send_json({
                    "type": "step",
                    "step": step,
                    "narration": narration or "",
                    "reasoning": reasoning or "",
                    "tool": tool or "",
                    "screenshot": screenshot,
                })
                if sent:
                    logger.debug('Step callback: Successfully sent step %d to frontend', step)
                else:
                    logger.warning('Step callback: Failed to send step %d to frontend', step)
        
        # Set step callback on integration BEFORE creating bridge
        _voice_integration.update_callbacks(step_callback=step_callback)
        
        # Create agent bridge with safe callbacks for this connection
        async def on_user_speech(text: str) -> None:
            if is_connected():
                await safe_send_json({"type": "user_speech", "text": text})
        
        async def on_agent_response(text: str) -> None:
            if is_connected():
                await safe_send_json({"type": "agent_response", "text": text})
        
        # Create or update bridge
        if not _voice_bridge:
            _voice_bridge = AgentBridge(
                integration=_voice_integration,
                on_user_speech=on_user_speech,
                on_agent_response=on_agent_response,
            )
        else:
            # Update callbacks for this connection
            _voice_bridge.on_user_speech = on_user_speech
            _voice_bridge.on_agent_response = on_agent_response
        
        # Initialize or reuse voice pipeline
        if not _voice_pipeline:
            _voice_pipeline = VoicePipeline(
                agent_bridge=_voice_bridge,
                deepgram_api_key=Config.DEEPGRAM_API_KEY,
                elevenlabs_api_key=Config.ELEVENLABS_API_KEY,
                elevenlabs_voice_id=Config.ELEVENLABS_VOICE_ID,
                deepgram_language=Config.DEEPGRAM_LANGUAGE,
            )
            
            if not await _voice_pipeline.initialize():
                await safe_send_json({"type": "error", "error": "Failed to initialize voice pipeline"})
                return
            
            # Run pipeline in background (only once)
            pipeline_task = asyncio.create_task(_voice_pipeline.run())
        
        # Send ready message
        if not await safe_send_json({"type": "ready"}):
            logger.warning("Failed to send ready message, connection may be closed")
            return
        
        # Handle WebSocket messages
        while is_connected():
            try:
                data = await websocket.receive_json()
                
                if data.get("type") == "text_input":
                    # Handle text input (for testing or fallback)
                    text = data.get("text", "")
                    if text and _voice_bridge:
                        await _voice_bridge.process_user_text(text)
                
                elif data.get("type") == "stop":
                    # Stop voice pipeline
                    break
                    
            except WebSocketDisconnect:
                logger.info("Voice WebSocket disconnected by client")
                connection_closed = True
                break
            except Exception as e:
                logger.error("Error receiving WebSocket message: %s", e, exc_info=True)
                if is_connected():
                    await safe_send_json({"type": "error", "error": str(e)})
                break
    
    except WebSocketDisconnect:
        logger.info("Voice WebSocket disconnected during initialization")
        connection_closed = True
    except Exception as e:
        logger.error("Error in voice WebSocket handler: %s", e, exc_info=True)
        if is_connected():
            await safe_send_json({"type": "error", "error": str(e)})
    finally:
        # Don't stop the pipeline when a connection closes - keep it running
        # The pipeline will be stopped on server shutdown
        # Just clear the callbacks for this connection
        if _voice_bridge:
            _voice_bridge.on_user_speech = None
            _voice_bridge.on_agent_response = None
        # Clear step callback
        if _voice_integration:
            _voice_integration.update_callbacks(step_callback=None)
        
        # Close websocket if still connected
        if is_connected() and not connection_closed:
            try:
                await websocket.close()
            except Exception:
                pass


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)-8s | %(message)s',
    )
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

