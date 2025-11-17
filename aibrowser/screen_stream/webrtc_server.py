"""WebRTC server for streaming browser screen."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaBlackhole
from av import VideoFrame
import numpy as np

from .capture import CDPScreenCapture

logger = logging.getLogger(__name__)


class BrowserVideoTrack(VideoStreamTrack):
    """Video track that streams frames from CDP screen capture."""

    kind = "video"

    def __init__(self, capture: CDPScreenCapture) -> None:
        super().__init__()  # This initializes _start and other base attributes
        self.capture = capture

    async def recv(self) -> VideoFrame:
        """Receive the next video frame."""
        pts, time_base = await self.next_timestamp()

        # Get frame from capture
        frame_bytes = await self.capture.get_frame()
        
        if frame_bytes is None:
            # Return a blank frame if no data available
            width, height = self.capture.frame_size
            frame = VideoFrame(width=width, height=height, format="yuv420p")
            frame.pts = pts
            frame.time_base = time_base
            return frame

        # Convert RGB bytes to VideoFrame
        width, height = self.capture.frame_size
        
        try:
            # Create numpy array from RGB bytes
            rgb_array = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((height, width, 3))
            
            # Create VideoFrame from numpy array
            frame = VideoFrame.from_ndarray(rgb_array, format="rgb24")
            frame.pts = pts
            frame.time_base = time_base
            
            return frame

        except Exception as error:
            logger.error("Error creating video frame: %s", error)
            # Return blank frame on error
            frame = VideoFrame(width=width, height=height, format="yuv420p")
            frame.pts = pts
            frame.time_base = time_base
            return frame


class ScreenStreamSession:
    """Manages a WebRTC peer connection for screen streaming."""

    def __init__(
        self,
        session_id: str,
        cdp_url: str,
        ice_servers: list[dict] = None,
    ) -> None:
        self.session_id = session_id
        self.cdp_url = cdp_url
        self.ice_servers = ice_servers or []
        
        self.pc: Optional[RTCPeerConnection] = None
        self.capture: Optional[CDPScreenCapture] = None
        self.video_track: Optional[BrowserVideoTrack] = None
        self._closed = False

    async def create_answer(self, offer_sdp: str, offer_type: str) -> dict:
        """
        Process the offer and create an answer.

        Args:
            offer_sdp: SDP offer from client
            offer_type: Type of offer (should be "offer")

        Returns:
            Dictionary with answer SDP and type
        """
        # Create peer connection with proper RTCConfiguration
        configuration = None
        if self.ice_servers:
            ice_servers = []
            for server in self.ice_servers:
                ice_server = RTCIceServer(
                    urls=server["urls"],
                    username=server.get("username"),
                    credential=server.get("credential"),
                )
                ice_servers.append(ice_server)
            configuration = RTCConfiguration(iceServers=ice_servers)
        
        self.pc = RTCPeerConnection(configuration=configuration)

        # Set up event handlers
        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Connection state: %s", self.pc.connectionState)
            if self.pc.connectionState == "failed":
                await self.close()

        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info("ICE connection state: %s", self.pc.iceConnectionState)

        # Initialize screen capture
        self.capture = CDPScreenCapture(cdp_url=self.cdp_url, fps=60, quality=100)
        await self.capture.start()

        # Create and add video track
        self.video_track = BrowserVideoTrack(self.capture)
        self.pc.addTrack(self.video_track)

        # Set remote description (offer)
        offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
        await self.pc.setRemoteDescription(offer)

        # Create answer
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)

        logger.info("Created answer for session %s", self.session_id)

        return {
            "sdp": self.pc.localDescription.sdp,
            "type": self.pc.localDescription.type,
        }

    async def add_ice_candidate(self, candidate: dict) -> None:
        """Add an ICE candidate to the peer connection."""
        if self.pc:
            from aiortc import RTCIceCandidate
            
            ice_candidate = RTCIceCandidate(
                candidate=candidate.get("candidate", ""),
                sdpMid=candidate.get("sdpMid"),
                sdpMLineIndex=candidate.get("sdpMLineIndex"),
            )
            await self.pc.addIceCandidate(ice_candidate)
            logger.debug("Added ICE candidate for session %s", self.session_id)

    async def close(self) -> None:
        """Close the session and cleanup resources."""
        if self._closed:
            return

        self._closed = True
        logger.info("Closing screen stream session %s", self.session_id)

        if self.capture:
            await self.capture.stop()

        if self.pc:
            await self.pc.close()


class ScreenStreamManager:
    """Manages multiple screen stream sessions."""

    def __init__(self, ice_servers: list[dict] = None) -> None:
        self.ice_servers = ice_servers or []
        self._sessions: dict[str, ScreenStreamSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        session_id: str,
        cdp_url: str,
        offer_sdp: str,
        offer_type: str,
    ) -> dict:
        """Create a new screen stream session and return the answer."""
        async with self._lock:
            # Close existing session if any
            if session_id in self._sessions:
                await self._sessions[session_id].close()
                del self._sessions[session_id]

            # Create new session
            session = ScreenStreamSession(
                session_id=session_id,
                cdp_url=cdp_url,
                ice_servers=self.ice_servers,
            )

            answer = await session.create_answer(offer_sdp, offer_type)
            self._sessions[session_id] = session

            return answer

    async def add_ice_candidate(self, session_id: str, candidate: dict) -> None:
        """Add ICE candidate to a session."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session:
                await session.add_ice_candidate(candidate)

    async def close_session(self, session_id: str) -> None:
        """Close a specific session."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                await session.close()

    async def close_all(self) -> None:
        """Close all sessions."""
        async with self._lock:
            for session in self._sessions.values():
                await session.close()
            self._sessions.clear()

