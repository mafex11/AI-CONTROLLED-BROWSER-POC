"""CDP-based screen capture for streaming browser frames."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

import aiohttp
from PIL import Image
import io

logger = logging.getLogger(__name__)


class CDPScreenCapture:
    """Captures browser screen via Chrome DevTools Protocol."""

    def __init__(
        self,
        cdp_url: str,
        fps: int = 2,
        quality: int = 80,
        format: str = "jpeg",
    ) -> None:
        """
        Initialize CDP screen capture.

        Args:
            cdp_url: WebSocket URL for Chrome DevTools Protocol
            fps: Frames per second for capture (default 2)
            quality: JPEG quality 1-100 (default 80)
            format: Image format 'jpeg' or 'png'
        """
        self.cdp_url = cdp_url
        self.fps = fps
        self.quality = quality
        self.format = format
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._frame_queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._capture_task: Optional[asyncio.Task] = None
        self._message_id = 0
        self._frame_width = 1920
        self._frame_height = 1080

    async def start(self) -> None:
        """Start capturing frames from the browser."""
        if self._running:
            return

        self._session = aiohttp.ClientSession()
        
        # Connect to CDP - handle both browser and page URLs
        if "/devtools/browser/" in self.cdp_url:
            # Need to get a page target instead
            # First connect to get the page list
            base_url = self.cdp_url.replace("ws://", "http://").split("/devtools/")[0]
            async with self._session.get(f"{base_url}/json") as resp:
                targets = await resp.json()
                # Find the first page target
                page_target = None
                for target in targets:
                    if target.get("type") == "page":
                        page_target = target
                        break
                
                if page_target and "webSocketDebuggerUrl" in page_target:
                    self.cdp_url = page_target["webSocketDebuggerUrl"]
                    logger.info("Using page target: %s", self.cdp_url)
        
        self._ws = await self._session.ws_connect(self.cdp_url)
        self._running = True

        # Start the capture loop
        self._capture_task = asyncio.create_task(self._capture_loop())
        logger.info("CDP screen capture started at %d FPS", self.fps)

    async def stop(self) -> None:
        """Stop capturing frames."""
        if not self._running:
            return

        self._running = False

        if self._capture_task:
            self._capture_task.cancel()
            try:
                await self._capture_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()

        if self._session:
            await self._session.close()

        logger.info("CDP screen capture stopped")

    async def get_frame(self) -> Optional[bytes]:
        """
        Get the next frame as raw RGB bytes.

        Returns:
            RGB frame bytes or None if queue is empty
        """
        try:
            return await asyncio.wait_for(self._frame_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            return None

    async def _send_command(self, method: str, params: dict = None) -> int:
        """Send a CDP command and return the message ID."""
        self._message_id += 1
        msg = {
            "id": self._message_id,
            "method": method,
            "params": params or {},
        }
        await self._ws.send_json(msg)
        return self._message_id

    async def _capture_loop(self) -> None:
        """Continuously capture screenshots at the specified FPS."""
        interval = 1.0 / self.fps

        while self._running:
            try:
                # Request a screenshot
                msg_id = await self._send_command(
                    "Page.captureScreenshot",
                    {
                        "format": self.format,
                        "quality": self.quality if self.format == "jpeg" else None,
                    },
                )

                # Wait for the response
                frame_data = await self._wait_for_response(msg_id)
                
                if frame_data:
                    # Convert base64 image to RGB bytes
                    rgb_bytes = await self._decode_frame(frame_data)
                    if rgb_bytes:
                        # Non-blocking queue put
                        if self._frame_queue.full():
                            try:
                                self._frame_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        
                        try:
                            self._frame_queue.put_nowait(rgb_bytes)
                        except asyncio.QueueFull:
                            logger.debug("Frame queue full, dropping frame")

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as error:
                logger.error("Error capturing frame: %s", error, exc_info=True)
                await asyncio.sleep(interval)

    async def _wait_for_response(self, msg_id: int, timeout: float = 5.0) -> Optional[str]:
        """Wait for a CDP response with the given message ID."""
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=0.5)
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.json()
                    if data.get("id") == msg_id:
                        result = data.get("result", {})
                        return result.get("data")
                    
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    logger.error("WebSocket closed or error")
                    return None

            except asyncio.TimeoutError:
                continue
            except Exception as error:
                logger.debug("Error reading WebSocket message: %s", error)
                continue

        return None

    async def _decode_frame(self, base64_data: str) -> Optional[bytes]:
        """Decode base64 image to RGB bytes."""
        try:
            # Decode base64
            img_bytes = base64.b64decode(base64_data)
            
            # Open with PIL
            img = Image.open(io.BytesIO(img_bytes))
            
            # Update frame size from actual image
            self._frame_width, self._frame_height = img.size
            
            # Convert to RGB if needed
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            # Return raw RGB bytes
            return img.tobytes()

        except Exception as error:
            logger.error("Error decoding frame: %s", error)
            return None

    @property
    def frame_size(self) -> tuple[int, int]:
        """Get the current frame size (width, height)."""
        return (self._frame_width, self._frame_height)

