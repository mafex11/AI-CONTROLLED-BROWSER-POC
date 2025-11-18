"""Browser session helpers for the WebRTC prototype.

This module provides access to the shared global browser instance
that is used by both text mode and voice mode.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..browser_use_integration import BrowserUseIntegration

logger = logging.getLogger(__name__)


class BrowserSessionPool:
    """Provides access to the shared global browser for WebRTC voice mode.
    
    Instead of creating a separate browser, this reuses the global browser
    from api_server.py that is shared between text and voice modes.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> BrowserUseIntegration:
        """Get the global browser integration (shared with text mode)."""
        async with self._lock:
            # Import here to avoid circular dependency
            from .. import api_server
            
            # Ensure the global browser is initialized
            if not await api_server.ensure_browser_initialized():
                raise RuntimeError("Failed to initialize shared browser for WebRTC")
            
            if not api_server._text_integration:
                raise RuntimeError("Browser integration not available")
            
            logger.debug("WebRTC voice mode using shared browser integration")
            return api_server._text_integration

    async def cleanup(self) -> None:
        """Cleanup is handled by api_server, not by individual session pools."""
        # The shared browser is managed by api_server.py, not by WebRTC sessions
        logger.debug("WebRTC session cleanup (shared browser remains running)")

