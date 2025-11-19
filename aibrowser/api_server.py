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

# Import screen stream router
from .screen_stream.router import router as screen_stream_router
from .screen_stream import router as screen_stream_router_module

# Import WebRTC experimental router
from .webrtc.router import router as webrtc_router

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Browser API - Unified Server")

# CORS middleware for frontend
# Allow localhost for development and Vercel/production domains
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://layerpathaivoicebrowser.vercel.app",
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

# Include screen stream router for WebRTC video streaming
app.include_router(screen_stream_router)

# Include WebRTC experimental router for voice mode
app.include_router(webrtc_router)

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
_text_awaiting_user_input: bool = False  # Track if text agent is awaiting user input


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
    """Ensure browser is initialized and running."""
    global _browser_manager, _text_integration, _last_activity_time
    
    if _browser_manager and await _browser_manager.is_running():
        _last_activity_time = time.time()
        return True
    
    logger.info("Initializing browser...")
    
    port = int(os.getenv('CHROME_DEBUG_PORT', '9222'))
    headless = os.getenv('CHROMIUM_HEADLESS', 'false').lower() in {'1', 'true', 'yes', 'on'}
    
    _browser_manager = CDPBrowserManager(port=port, headless=headless)
    
    # Share browser manager with screen stream module early
    # so it can start the browser if needed
    screen_stream_router_module._browser_pool = _browser_manager
    
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
    """Cleanup browser after idle timeout - DISABLED.
    
    Browser cleanup is now handled explicitly via /api/reset-browser.
    Automatic cleanup was causing issues with frequent reinitializations.
    """
    # Disabled - browser stays running once initialized
    while True:
        await asyncio.sleep(3600)  # Sleep for 1 hour, effectively disabled


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
    """Initialize API server and browser on startup."""
    global _cleanup_task
    
    if not Config.validate():
        logger.error("Configuration validation failed")
        sys.exit(1)
    
    logger.info("Starting API server and initializing shared browser...")
    
    # Initialize the shared browser (used by both text and voice modes)
    if not await ensure_browser_initialized():
        logger.error("Failed to initialize browser on startup")
        sys.exit(1)
    
    _cleanup_task = asyncio.create_task(cleanup_browser_if_idle())
    
    logger.info("API server ready with shared browser initialized (one browser, one tab for both text and voice modes)")


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
    global _text_integration, _active_connections, _last_activity_time, _text_awaiting_user_input
    
    if not await ensure_browser_initialized():
        return StreamingResponse(
            iter([b'data: {"type": "error", "error": "Failed to initialize browser"}\n\n']),
            media_type="text/event-stream",
        )
    
    import uuid
    connection_id = str(uuid.uuid4())
    _active_connections.add(connection_id)
    _last_activity_time = time.time()
    
    # Check if this is a continuation of existing conversation
    is_continuation = _text_awaiting_user_input
    logger.debug(f"Processing query (is_continuation={is_continuation}): {request.query[:50]}...")
    
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
                
                # Only capture screenshot if the agent cached one (e.g., on error or highlight)
                # Don't do expensive screenshot capture on every step
                screenshot = None
                if _text_integration:
                    try:
                        # Check if agent has a cached highlight screenshot
                        if _text_integration._state and _text_integration._state.agent:
                            agent = _text_integration._state.agent
                            if hasattr(agent, '_last_highlight_screenshot_base64') and agent._last_highlight_screenshot_base64:
                                screenshot = agent._last_highlight_screenshot_base64
                                logger.debug('Using cached highlight screenshot for step %d', step)
                    except Exception as e:
                        logger.debug('Failed to get cached screenshot: %s', e)
                
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
            global _text_awaiting_user_input
            try:
                result = await _text_integration.run(request.query, is_continuation=is_continuation)
                run_result[0] = result
                # Update awaiting_user_input state for next query
                _text_awaiting_user_input = result.get('awaiting_user_input', False)
                logger.debug(f"Agent run complete, awaiting_user_input={_text_awaiting_user_input}")
            except asyncio.CancelledError:
                logger.info('Agent task was cancelled')
                run_error[0] = Exception('Task cancelled by user')
                raise
            except Exception as e:
                run_error[0] = e
            finally:
                run_complete.set()
        
        run_task = asyncio.create_task(run_agent())
        client_disconnected = False
        
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
                except (GeneratorExit, asyncio.CancelledError):
                    # Client disconnected or stopped
                    logger.info('Client disconnected, cancelling agent task')
                    client_disconnected = True
                    run_task.cancel()
                    raise
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
                    
        except (GeneratorExit, asyncio.CancelledError):
            # Client disconnected, cancel the agent task
            if not run_task.done():
                logger.info('Cancelling agent task due to client disconnect')
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    logger.info('Agent task cancelled successfully')
        except Exception as e:
            logger.error('Error processing query: %s', e, exc_info=True)
            # Cancel task on error
            if not run_task.done():
                run_task.cancel()
            error_data = {"type": "error", "error": str(e)}
            yield f'data: {json.dumps(error_data)}\n\n'.encode()
        finally:
            # Ensure task is cancelled if still running
            if not run_task.done():
                logger.info('Ensuring agent task is cancelled in finally block')
                run_task.cancel()
                try:
                    await run_task
                except asyncio.CancelledError:
                    pass
            
            if _text_integration:
                _text_integration.update_callbacks(step_callback=original_step)
            _active_connections.discard(connection_id)
            _last_activity_time = time.time()
            
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.websocket("/ws/voice")
async def websocket_voice_deprecated(websocket: WebSocket):
    """DEPRECATED: Voice mode now uses WebRTC (/api/webrtc-experimental).
    
    This endpoint returns an error directing users to use WebRTC instead.
    The LocalAudioTransport-based voice mode does not work in Docker/cloud deployments.
    """
    await websocket.accept()
    logger.warning("Deprecated /ws/voice endpoint called - directing to WebRTC")
    
    try:
        await websocket.send_json({
            "type": "error",
            "error": "Voice mode via WebSocket is deprecated. Please use WebRTC (/api/webrtc-experimental) instead. "
                     "The WebSocket voice mode requires physical audio devices and does not work in Docker/cloud deployments."
        })
    except Exception as e:
        logger.error("Error sending deprecation message: %s", e)
    finally:
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


@app.post("/api/stop-voice")
async def stop_voice():
    """Stop voice pipeline when frontend closes or voice mode is disabled."""
    global _voice_pipeline, _voice_bridge, _voice_integration
    
    logger.info("Stop voice endpoint called - shutting down voice pipeline")
    
    try:
        # Stop pipeline first
        if _voice_pipeline:
            try:
                await _voice_pipeline.stop()
            except Exception as e:
                logger.error(f"Error stopping voice pipeline: {e}")
        
        # Clear global references
        _voice_pipeline = None
        _voice_bridge = None
        
        # Shutdown voice integration
        if _voice_integration:
            try:
                await _voice_integration.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down voice integration: {e}")
            _voice_integration = None
        
        logger.info("Voice pipeline stopped successfully")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error in stop voice endpoint: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@app.post("/api/reset-browser")
async def reset_browser():
    """Reset the shared browser to blank page when frontend session starts/ends.
    
    Both text and voice modes use the same browser, so this resets the single
    shared browser instance and clears conversation state.
    """
    global _browser_manager, _text_integration, _last_activity_time, _text_awaiting_user_input
    
    # Update activity time to prevent idle cleanup
    _last_activity_time = time.time()
    
    # Reset conversation state
    _text_awaiting_user_input = False
    
    try:
        # Ensure browser is initialized before trying to reset
        if not _browser_manager:
            logger.debug("Browser manager not initialized, initializing now for reset")
            if not await ensure_browser_initialized():
                logger.warning("Failed to initialize browser for reset")
                return {"status": "error", "message": "Failed to initialize browser"}
        
        # Reset the shared browser (used by both text and voice modes)
        if _browser_manager and await _browser_manager.is_running():
            logger.info("Resetting shared browser to blank page and clearing session state")
            
            # Get CDP websocket URL
            cdp_url = await _browser_manager.websocket_url()
            if cdp_url:
                try:
                    # Connect to CDP and navigate to blank page
                    async with aiohttp.ClientSession() as session:
                        async with session.ws_connect(cdp_url) as ws:
                            # Enable Page domain
                            await ws.send_json({"id": 1, "method": "Page.enable"})
                            await asyncio.wait_for(ws.receive(), timeout=5.0)
                            
                            # Navigate to about:blank
                            await ws.send_json({
                                "id": 2,
                                "method": "Page.navigate",
                                "params": {"url": "about:blank"}
                            })
                            result = await asyncio.wait_for(ws.receive(), timeout=5.0)
                            logger.debug(f"Navigation to blank result: {result}")
                    
                    logger.info("Shared browser navigated to blank page via CDP")
                except Exception as e:
                    logger.error(f"Error navigating to blank page: {e}", exc_info=True)
                
                # Clear conversation state without reinitializing
                # (to avoid race conditions with active voice mode)
                if _text_integration and _text_integration._state:
                    try:
                        # Clear the agent's conversation history
                        if hasattr(_text_integration._state, 'agent') and _text_integration._state.agent:
                            agent = _text_integration._state.agent
                            if hasattr(agent, '_context_log'):
                                agent._context_log = []
                                logger.debug("Cleared agent context log")
                            if hasattr(agent, 'messages'):
                                # Keep only system message if it exists
                                system_msgs = [m for m in agent.messages if m.get('role') == 'system']
                                agent.messages = system_msgs
                                logger.debug("Reset agent message history")
                        
                        # Force state refresh to update to blank page
                        if _text_integration._state.controller:
                            logger.debug("Refreshing integration state on blank page")
                            await _text_integration._state.controller.refresh_state(
                                include_dom=True,
                                include_screenshot=False,
                            )
                        
                        logger.info("Cleared conversation state without reinitializing (avoids race conditions)")
                    except Exception as e:
                        logger.warning("Error clearing conversation state: %s", e, exc_info=True)
                
                logger.info("Shared browser reset complete (affects both text and voice modes)")
                return {"status": "success", "shared_browser_reset": True}
            else:
                logger.warning("No CDP URL available for browser reset")
                return {"status": "error", "message": "No CDP URL available"}
        else:
            logger.info("No browser running, nothing to reset")
            return {"status": "not_running"}
    except Exception as e:
        logger.error(f"Error resetting browser: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


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