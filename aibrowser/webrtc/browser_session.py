"""Browser session helpers for the WebRTC prototype."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from ..browser_use_integration import BrowserUseIntegration
from ..cdp_browser_manager import CDPBrowserManager

logger = logging.getLogger(__name__)


class BrowserSessionPool:
    """Lazily creates a BrowserUseIntegration for the WebRTC pipeline."""

    def __init__(self) -> None:
        self._browser_manager: Optional[CDPBrowserManager] = None
        self._integration: Optional[BrowserUseIntegration] = None
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> BrowserUseIntegration:
        """Ensure the Chromium backend and BrowserUse integration are running."""
        async with self._lock:
            if self._integration and self._browser_manager:
                if await self._browser_manager.is_running():
                    return self._integration

            port = int(os.getenv("CHROME_DEBUG_PORT", "9222"))
            headless = os.getenv("CHROMIUM_HEADLESS", "false").lower() in {"1", "true", "yes", "on"}

            logger.info("Starting dedicated Chromium instance for WebRTC pipeline (headless=%s)", headless)
            self._browser_manager = CDPBrowserManager(port=port, headless=headless)

            if not await self._browser_manager.start():
                raise RuntimeError("Failed to start Chromium for WebRTC pipeline")

            ws_url = await self._browser_manager.websocket_url()
            if not ws_url:
                raise RuntimeError("Failed to obtain CDP WebSocket URL for WebRTC pipeline")

            self._integration = BrowserUseIntegration(
                cdp_url=ws_url,
            )

            if not await self._integration.initialize():
                raise RuntimeError("Failed to initialize BrowserUseIntegration for WebRTC pipeline")

            logger.info("WebRTC integration connected to Chromium at %s", ws_url)
            return self._integration

    async def cleanup(self) -> None:
        """Tear down the Chromium process and integration."""
        async with self._lock:
            if self._integration:
                try:
                    await self._integration.shutdown()
                except Exception:
                    logger.debug("Error shutting down WebRTC BrowserUseIntegration", exc_info=True)
                self._integration = None

            if self._browser_manager:
                try:
                    await self._browser_manager.stop()
                except Exception:
                    logger.debug("Error stopping Chromium for WebRTC pipeline", exc_info=True)
                self._browser_manager = None

