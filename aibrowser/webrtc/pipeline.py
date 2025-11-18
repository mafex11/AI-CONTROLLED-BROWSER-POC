"""Pipecat pipeline that uses SmallWebRTCTransport."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from ..config import Config
from ..voice.agent_bridge import AgentBridge
from ..voice.pipecat_pipeline import (
    AgentToTTSProcessor,
    AudioStreamProcessor,
    SpeechCompletionTracker,
    TextToAgentProcessor,
)

logger = logging.getLogger(__name__)


class WebRTCPipeline:
    """Pipeline variant that streams audio via Pipecat's SmallWebRTCTransport."""

    def __init__(
        self,
        *,
        connection: SmallWebRTCConnection,
        agent_bridge: AgentBridge,
        deepgram_language: str = Config.DEEPGRAM_LANGUAGE,
        elevenlabs_voice_id: str = Config.ELEVENLABS_VOICE_ID,
        audio_in_sample_rate: int = 16000,  # Silero VAD only supports 16000 or 8000
        audio_out_sample_rate: int = 24000,  # Match ElevenLabs default to avoid playback speed issues
        channels: int = 1,
    ) -> None:
        if not Config.validate_voice():
            raise RuntimeError("Voice API configuration invalid; cannot start WebRTC pipeline")

        self.connection = connection
        self.agent_bridge = agent_bridge
        self.audio_in_sample_rate = audio_in_sample_rate
        self.audio_out_sample_rate = audio_out_sample_rate
        self.channels = channels
        self.deepgram_language = deepgram_language
        self.elevenlabs_voice_id = elevenlabs_voice_id

        self.pipeline: Optional[Pipeline] = None
        self.runner: Optional[PipelineRunner] = None
        self.task: Optional[PipelineTask] = None
        self._transport: Optional[SmallWebRTCTransport] = None
        self._audio_stream_processor: Optional[AudioStreamProcessor] = None
        self._speech_tracker: Optional[SpeechCompletionTracker] = None
        self._agent_to_tts: Optional[AgentToTTSProcessor] = None

    async def initialize(self) -> None:
        """Build Pipecat pipeline around the provided SmallWebRTC connection."""
        import time
        start_time = time.time()
        
        from pipecat.services.deepgram.stt import DeepgramSTTService
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        logger.info("Initializing WebRTC Pipecat pipeline for connection %s", self.connection.pc_id)

        vad_params = VADParams(stop_secs=1.0)
        vad_analyzer = SileroVADAnalyzer(params=vad_params)

        transport_params = TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=self.audio_in_sample_rate,
            audio_in_channels=self.channels,
            audio_out_enabled=True,
            audio_out_sample_rate=self.audio_out_sample_rate,
            audio_out_channels=self.channels,
            vad_analyzer=vad_analyzer,
        )

        transport = SmallWebRTCTransport(
            webrtc_connection=self.connection,
            params=transport_params,
            input_name="webrtc-input",
            output_name="webrtc-output",
        )

        self._transport = transport
        text_to_agent = TextToAgentProcessor(self.agent_bridge)
        self._agent_to_tts = AgentToTTSProcessor()
        self._speech_tracker = SpeechCompletionTracker()
        self._audio_stream_processor = AudioStreamProcessor()

        self.agent_bridge.set_tts_processor(self._agent_to_tts)
        self.agent_bridge.set_speech_tracker(self._speech_tracker)

        stt_service = DeepgramSTTService(
            api_key=Config.DEEPGRAM_API_KEY,
            language=self.deepgram_language,
        )
        tts_service = ElevenLabsTTSService(
            api_key=Config.ELEVENLABS_API_KEY,
            voice_id=self.elevenlabs_voice_id,
            sample_rate=self.audio_out_sample_rate,
        )

        self.pipeline = Pipeline(
            [
                transport.input(),
                stt_service,
                text_to_agent,
                self._agent_to_tts,
                tts_service,
                self._audio_stream_processor,
                self._speech_tracker,
                transport.output(),
            ]
        )

        self.task = PipelineTask(self.pipeline)
        self.runner = PipelineRunner()
        
        elapsed = time.time() - start_time
        logger.info("WebRTC pipeline initialized in %.2fs", elapsed)

    async def run(self) -> None:
        """Start the Pipecat runner."""
        if not self.pipeline or not self.runner or not self.task:
            raise RuntimeError("WebRTCPipeline not initialized.")

        try:
            await self.runner.run(self.task)
        except asyncio.CancelledError:
            logger.debug("WebRTCPipeline runner cancelled for %s", self.connection.pc_id)
            raise
        except Exception:
            logger.exception("Error running WebRTC pipeline")
            raise

    async def stop(self) -> None:
        """Stop pipeline and cleanup."""
        logger.info("Stopping WebRTC pipeline for %s", self.connection.pc_id)
        if self.runner:
            try:
                await self.runner.cancel()
            except Exception:
                logger.debug("Error cancelling runner", exc_info=True)

        if self.task:
            try:
                await self.task.cancel()
            except Exception:
                logger.debug("Error cancelling pipeline task", exc_info=True)

        if self._transport:
            try:
                await self._transport.output().stop()
                await self._transport.input().stop()
            except Exception:
                logger.debug("Error stopping WebRTC transport", exc_info=True)

