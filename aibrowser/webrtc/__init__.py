"""WebRTC-focused extensions for AI Browser.

This package houses the experimental Pipecat SmallWebRTC integration that we
are building in parallel to the existing voice stack.  Keeping the code here
lets us iterate without touching the current production pipeline.
"""

__all__ = [
    "config",
    "browser_session",
    "pipeline",
    "session_manager",
    "router",
]

