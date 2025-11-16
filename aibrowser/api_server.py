"""FastAPI server for frontend integration with text and voice modes."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import time
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
# Allow localhost for development and Vercel/production domains
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    # Add your Vercel domain here after deployment
    # "https://your-app.vercel.app",
]
# Also allow any origin if ALLOW_ALL_ORIGINS is set (for development)
if os.getenv("ALLOW_ALL_ORIGINS", "false").lower() in {"true", "1", "yes"}:
    allowed_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
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

# Connection tracking for cleanup
_active_connections: set = set()
_last_activity_time: float = 0.0
_cleanup_task: Optional[asyncio.Task] = None
_idle_timeout: float = float(os.getenv("BROWSER_IDLE_TIMEOUT", "300"))  # 5 minutes default


class QueryRequest(BaseModel):
    query: str
    voiceMode: bool = False
    provider: Optional[str] = None


async def get_screenshot_base64(integration: BrowserUseIntegration) -> Optional[str]:
    """Get screenshot from browser integration as base64 string."""
    if not integration or not integration._state:
        return None
    
    try:
        if integration._state and integration._state.agent:
            agent = integration._state.agent
            if hasattr(agent, '_last_highlight_screenshot_base64') and agent._last_highlight_screenshot_base64:
                logger.debug('Using stored highlight screenshot for frontend')
                return agent._last_highlight_screenshot_base64
        
        state = await integration._state.controller.refresh_state(
            include_dom=False,
            include_screenshot=True,
        )
        
        if state and hasattr(state, 'screenshot') and state.screenshot:
            screenshot_b64 = state.screenshot
            if isinstance(screenshot_b64, str):
                if screenshot_b64.startswith('data:image'):
                    return screenshot_b64
                elif not screenshot_b64.startswith('data:'):
                    return f'data:image/png;base64,{screenshot_b64}'
                return screenshot_b64
    except Exception as e:
        logger.debug('Failed to get screenshot: %s', e)
    
    return None


async def ensure_browser_initialized() -> bool:
    #Start browser only when first query is made.
    global _browser_manager, _text_integration, _last_activity_time
    
    if _browser_manager and await _browser_manager.is_running():
        _last_activity_time = time.time()
        return True
    
    logger.info("Initializing browser for first query...")
    
    port = int(os.getenv('CHROME_DEBUG_PORT', '9222'))
    headless = os.getenv('CHROMIUM_HEADLESS', 'false').lower() in {'1', 'true', 'yes', 'on'}
    
    _browser_manager = CDPBrowserManager(port=port, headless=headless)
    started = await _browser_manager.start()
    if not started or _browser_manager.endpoint is None:
        logger.error('Failed to start Chromium')
        return False
    
    ws_url = await _browser_manager.websocket_url()
    if not ws_url:
        logger.error('Failed to get WebSocket URL')
        return False
    
    logger.info(f'CDP endpoint ready: {_browser_manager.endpoint}')
    logger.info(f'WebSocket URL: {ws_url}')
    
    _text_integration = BrowserUseIntegration(
        cdp_url=ws_url,
        default_search_engine=Config.DEFAULT_SEARCH_ENGINE,
    )
    if not await _text_integration.initialize():
        logger.error('Failed to initialize text integration')
        return False
    
    _last_activity_time = time.time()
    logger.info("Browser initialized and ready")
    return True


async def cleanup_browser_if_idle() -> None:
    """Cleanup browser after idle timeout."""
    global _browser_manager, _text_integration, _last_activity_time
    
    while True:
        try:
            await asyncio.sleep(30) 
            
            if _browser_manager and await _browser_manager.is_running():
                current_time = time.time()
                idle_time = current_time - _last_activity_time
                
                if idle_time > _idle_timeout and len(_active_connections) == 0:
                    logger.info(f"Browser idle for {idle_time:.1f}s, cleaning up...")
                    await cleanup_browser()
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}", exc_info=True)
            await asyncio.sleep(60)


async def cleanup_browser() -> None:
    """Cleanup browser and integration resources."""
    global _browser_manager, _text_integration, _voice_integration, _voice_pipeline, _voice_bridge
    
    logger.info("Cleaning up browser resources...")
    
    if _voice_pipeline:
        try:
            await _voice_pipeline.stop()
        except Exception:
            pass
        _voice_pipeline = None
    
    if _text_integration:
        try:
            await _text_integration.shutdown()
        except Exception:
            pass
        _text_integration = None
    
    if _voice_integration:
        try:
            await _voice_integration.shutdown()
        except Exception:
            pass
        _voice_integration = None
    
    if _browser_manager:
        try:
            await _browser_manager.stop()
        except Exception:
            pass
        _browser_manager = None
    
    logger.info("Browser cleanup complete")


@app.on_event("startup")
async def startup():
    """Initialize API server without starting browser."""
    global _cleanup_task
    
    if not Config.validate():
        logger.error("Configuration validation failed")
        sys.exit(1)
    
    logger.info("Starting API server... (browser will start on first query)")
    
    _cleanup_task = asyncio.create_task(cleanup_browser_if_idle())
    
    logger.info("API server ready")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    global _cleanup_task
    
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    
    await cleanup_browser()


@app.post("/api/query")
async def query_text(request: QueryRequest):
    """Handle text mode queries with SSE streaming."""
    global _text_integration, _active_connections, _last_activity_time
    
    if not await ensure_browser_initialized():
        return StreamingResponse(
            iter([b'data: {"type": "error", "error": "Failed to initialize browser"}\n\n']),
            media_type="text/event-stream",
        )
    
    import uuid
    connection_id = str(uuid.uuid4())
    _active_connections.add(connection_id)
    _last_activity_time = time.time()
    
    original_provider = Config.LLM_PROVIDER
    provider_changed = False
    if request.provider:
        provider = request.provider.strip().lower()
        if provider in {'gemini', 'claude', 'openai'} and provider != Config.LLM_PROVIDER:
            provider_changed = True
            Config.LLM_PROVIDER = provider
            logger.info(f"Switching provider to: {provider}")
            cdp_url = None
            if _text_integration and _text_integration._state:
                try:
                    cdp_url = _text_integration.cdp_url
                except Exception:
                    pass
            
            try:
                if _text_integration:
                    await _text_integration.shutdown()
                
                if not cdp_url and _browser_manager:
                    cdp_url = await _browser_manager.websocket_url()
                
                if cdp_url:
                    _text_integration = BrowserUseIntegration(
                        cdp_url=cdp_url,
                        default_search_engine=Config.DEFAULT_SEARCH_ENGINE,
                    )
                    if not await _text_integration.initialize():
                        Config.LLM_PROVIDER = original_provider
                        return StreamingResponse(
                            iter([b'data: {"type": "error", "error": "Failed to initialize with provider"}\n\n']),
                            media_type="text/event-stream",
                        )
                else:
                    Config.LLM_PROVIDER = original_provider
                    return StreamingResponse(
                        iter([b'data: {"type": "error", "error": "Failed to get browser connection"}\n\n']),
                        media_type="text/event-stream",
                    )
            except Exception as e:
                logger.error(f"Failed to switch provider: {e}", exc_info=True)
                Config.LLM_PROVIDER = original_provider
                return StreamingResponse(
                    iter([b'data: {"type": "error", "error": f"Failed to switch provider: {str(e)}"}\n\n']),
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
                
                screenshot = None
                if _text_integration:
                    try:
                        screenshot = await get_screenshot_base64(_text_integration)
                    except Exception as e:
                        logger.debug('Failed to get screenshot: %s', e)
                
                data = {
                    "type": "step",
                    "step": step,
                    "narration": narration or "",
                    "reasoning": reasoning or "",
                    "tool": tool or "",
                    "screenshot": screenshot,
                }
                await queue.put(data)
        
        original_step = _text_integration.step_callback
        _text_integration.update_callbacks(step_callback=step_callback)
        
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
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=0.1)
                        yield f"data: {json.dumps(data)}\n\n".encode()
                    except asyncio.TimeoutError:
                        if run_complete.is_set():
                            break
                        continue
                except Exception as e:
                    logger.debug('Error in queue processing: %s', e)
            
            await run_task
            
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
            if _text_integration:
                _text_integration.update_callbacks(step_callback=original_step)
            _active_connections.discard(connection_id)
            _last_activity_time = time.time()
            
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket):
    """Handle voice mode WebSocket connection."""
    global _voice_integration, _voice_pipeline, _voice_bridge, _browser_manager, _active_connections, _last_activity_time
    
    await websocket.accept()
    logger.info("Voice WebSocket connection established")
    
    pipeline_task = None
    connection_closed = False
    voice_connection_id = None
    
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
        if not await ensure_browser_initialized():
            await safe_send_json({"type": "error", "error": "Failed to initialize browser"})
            return
        
        voice_connection_id = f"voice_{id(websocket)}"
        _active_connections.add(voice_connection_id)
        _last_activity_time = time.time()
        
        if not _voice_integration:
            if not Config.validate_voice():
                await safe_send_json({"type": "error", "error": "Voice configuration invalid"})
                if voice_connection_id:
                    _active_connections.discard(voice_connection_id)
                return
            
            ws_url = await _browser_manager.websocket_url()
            if not ws_url:
                await safe_send_json({"type": "error", "error": "Failed to get WebSocket URL"})
                if voice_connection_id:
                    _active_connections.discard(voice_connection_id)
                return
            
            _voice_integration = BrowserUseIntegration(
                cdp_url=ws_url,
                default_search_engine=Config.DEFAULT_SEARCH_ENGINE,
            )
            if not await _voice_integration.initialize():
                await safe_send_json({"type": "error", "error": "Failed to initialize voice integration"})
                if voice_connection_id:
                    _active_connections.discard(voice_connection_id)
                return
        
        # Step callback to send step data (narration, screenshots) to frontend
        async def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
            """Send step updates to frontend via WebSocket."""
            if phase == 'before':
                if not is_connected():
                    logger.debug('Step callback: WebSocket not connected, skipping step %d', step)
                    return
                
                logger.debug('Step callback: Sending step %d to frontend', step)
                
                screenshot = None
                if _voice_integration:
                    try:
                        screenshot = await get_screenshot_base64(_voice_integration)
                    except Exception as e:
                        logger.debug('Failed to get screenshot: %s', e)
                
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
        
        _voice_integration.update_callbacks(step_callback=step_callback)
        
        async def on_user_speech(text: str) -> None:
            if is_connected():
                await safe_send_json({"type": "user_speech", "text": text})
        
        async def on_agent_response(text: str) -> None:
            if is_connected():
                await safe_send_json({"type": "agent_response", "text": text})
        
        if not _voice_bridge:
            _voice_bridge = AgentBridge(
                integration=_voice_integration,
                on_user_speech=on_user_speech,
                on_agent_response=on_agent_response,
            )
        else:
            _voice_bridge.on_user_speech = on_user_speech
            _voice_bridge.on_agent_response = on_agent_response
        
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
            
            _voice_pipeline.set_websocket_sender(safe_send_json)
            
            pipeline_task = asyncio.create_task(_voice_pipeline.run())
        else:
            _voice_pipeline.set_websocket_sender(safe_send_json)
        
        if not await safe_send_json({"type": "ready"}):
            logger.warning("Failed to send ready message, connection may be closed")
            return
        
        while is_connected():
            try:
                data = await websocket.receive_json()
                
                if data.get("type") == "text_input":
                    text = data.get("text", "")
                    if text and _voice_bridge:
                        await _voice_bridge.process_user_text(text)
                
                elif data.get("type") == "stop":
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
        if _voice_bridge:
            _voice_bridge.on_user_speech = None
            _voice_bridge.on_agent_response = None
        if _voice_integration:
            _voice_integration.update_callbacks(step_callback=None)
        
        if voice_connection_id:
            _active_connections.discard(voice_connection_id)
        _last_activity_time = time.time()
        
        if is_connected() and not connection_closed:
            try:
                await websocket.close()
            except Exception:
                pass


@app.post("/api/cleanup")
async def cleanup_endpoint():
    """Cleanup endpoint called when frontend tab closes."""
    global _active_connections, _last_activity_time
    
    logger.info("Cleanup endpoint called - clearing active connections")
    _active_connections.clear()
    _last_activity_time = 0.0
    
    asyncio.create_task(cleanup_browser())
    
    return {"status": "cleanup initiated"}


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
    
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)