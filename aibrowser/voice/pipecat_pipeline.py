"""Pipecat pipeline setup for voice interaction."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pipecat.frames.frames import TextFrame, StartFrame, TranscriptionFrame, InterimTranscriptionFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from .agent_bridge import AgentBridge

logger = logging.getLogger(__name__)


class TextToAgentProcessor(FrameProcessor):
	"""Processor that takes STT text frames and sends them to the agent bridge."""

	def __init__(self, agent_bridge: AgentBridge) -> None:
		super().__init__()
		self.agent_bridge = agent_bridge

	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process text frames from STT."""
		# Handle StartFrame specially - let base class initialize first, then push
		if isinstance(frame, StartFrame):
			# Call super().process_frame() first - this calls __start() which sets __started = True
			await super().process_frame(frame, direction)
			# Now push the frame - base class has initialized, so _check_started will pass
			await self.push_frame(frame, direction)
			return
		
		# For all other frames, call super().process_frame() first
		await super().process_frame(frame, direction)
		
		# Only process FINAL TranscriptionFrames from STT (ignore interim transcriptions)
		# InterimTranscriptionFrame is for partial results while user is still speaking
		# TranscriptionFrame is the final transcription when user stops speaking
		if isinstance(frame, TranscriptionFrame) and frame.text:
			text = frame.text.strip()
			if text:
				logger.info('🎤🎤🎤 STT final transcription: "%s" 🎤🎤🎤', text)
				# Process user text in background
				asyncio.create_task(self.agent_bridge.process_user_text(text))
		elif isinstance(frame, InterimTranscriptionFrame):
			# Log interim transcriptions for debugging but don't process them
			logger.debug('🎤 Interim transcription (ignored): "%s"', frame.text.strip() if frame.text else '')
		
		# ALWAYS push all frames - super().process_frame() doesn't push, it just processes
		await self.push_frame(frame, direction)


class AgentToTTSProcessor(FrameProcessor):
	"""Processor that takes text from agent and sends it to TTS.
	
	This processor is in the pipeline but only processes TextFrames from the agent.
	All other frames (audio, StartFrame, etc.) are passed through unchanged.
	"""

	def __init__(self) -> None:
		super().__init__()

	async def send_text(self, text: str) -> None:
		"""Send text to TTS by pushing TextFrame into the pipeline."""
		if not text or not text.strip():
			return
		
		text_clean = text.strip()
		logger.info('🔊 AgentToTTSProcessor.send_text: Sending text to TTS: "%s"', text_clean)
		
		try:
			# Push TextFrame into the pipeline - it will flow to TTS service
			await self.push_frame(TextFrame(text=text_clean), FrameDirection.DOWNSTREAM)
		except Exception as e:
			logger.error('Error pushing TextFrame to pipeline: %s', e, exc_info=True)

	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process frames - pass everything through."""
		# Handle StartFrame specially - push it first, then let base class initialize
		if isinstance(frame, StartFrame):
			# Push StartFrame before initialization so it propagates through pipeline
			await super().process_frame(frame, direction)
			# Now push the frame - base class has initialized
			await self.push_frame(frame, direction)
			return
		
		# For all other frames, call super().process_frame() first
		await super().process_frame(frame, direction)
		
		# ALWAYS push all frames - super().process_frame() doesn't push, it just processes
		await self.push_frame(frame, direction)


class VoicePipeline:
	"""Manages the Pipecat voice pipeline for STT and TTS."""

	def __init__(
		self,
		agent_bridge: AgentBridge,
		*,
		deepgram_api_key: str,
		elevenlabs_api_key: str,
		elevenlabs_voice_id: str = '21m00Tcm4TlvDq8ikWAM',
		deepgram_language: str = 'en-US',
		sample_rate: int = 16000,
		channels: int = 1,
	) -> None:
		self.agent_bridge = agent_bridge
		self.deepgram_api_key = deepgram_api_key
		self.elevenlabs_api_key = elevenlabs_api_key
		self.elevenlabs_voice_id = elevenlabs_voice_id
		self.deepgram_language = deepgram_language
		self.sample_rate = sample_rate
		self.channels = channels

		self.pipeline: Optional[Pipeline] = None
		self.runner: Optional[PipelineRunner] = None
		self.task: Optional[PipelineTask] = None
		self._agent_to_tts: Optional[AgentToTTSProcessor] = None
		self._transport: Optional[LocalAudioTransport] = None

	async def initialize(self) -> bool:
		"""Initialize the pipeline components."""
		try:
			# Create local audio transport with VAD configured for 2-second pause
			logger.info('📋 Creating LocalAudioTransport with VAD (2s pause threshold)...')
			try:
				# Configure VAD to wait 2 seconds of silence before finalizing transcription
				vad_params = VADParams(stop_secs=2.0)
				vad_analyzer = SileroVADAnalyzer(params=vad_params)
				
				transport_params = LocalAudioTransportParams(
					audio_in_enabled=True,
					audio_out_enabled=True,
					vad_analyzer=vad_analyzer,
				)
				transport = LocalAudioTransport(transport_params)
				logger.info('✅ LocalAudioTransport created with VAD (stop_secs=2.0)')
			except Exception as e:
				logger.error('Failed to create LocalAudioTransport: %s', e, exc_info=True)
				return False

			self._transport = transport

			# Initialize Deepgram STT
			logger.info('Initializing Deepgram STT service...')
			stt_service = DeepgramSTTService(
				api_key=self.deepgram_api_key,
				language=self.deepgram_language,
			)
			logger.info('✅ Deepgram STT service initialized (language: %s)', self.deepgram_language)

			# Initialize ElevenLabs TTS
			logger.info('Initializing ElevenLabs TTS service...')
			tts_service = ElevenLabsTTSService(
				api_key=self.elevenlabs_api_key,
				voice_id=self.elevenlabs_voice_id,
			)
			logger.info('✅ ElevenLabs TTS service initialized (voice_id: %s)', self.elevenlabs_voice_id)

			# Create processors
			text_to_agent = TextToAgentProcessor(self.agent_bridge)
			self._agent_to_tts = AgentToTTSProcessor()
			
			# Connect agent bridge to TTS processor
			self.agent_bridge.set_tts_processor(self._agent_to_tts)

			# Build pipeline: Input -> STT -> TextToAgent -> AgentToTTS -> TTS -> Output
			logger.info('🔧 Building pipeline...')
			pipeline = Pipeline(
				[
					transport.input(),  # Transport user input
					stt_service,  # STT
					text_to_agent,  # Process text and send to agent
					self._agent_to_tts,  # Pass through, can inject TextFrames from agent
					tts_service,  # TTS
					transport.output(),  # Transport bot output
				]
			)
			
			self.pipeline = pipeline
			logger.info('✅ Pipeline created')

			# Create PipelineTask
			logger.info('🔍 Creating PipelineTask...')
			task = PipelineTask(pipeline)
			self.task = task
			logger.info('✅ PipelineTask created')

			# Create PipelineRunner
			logger.info('🔍 Creating PipelineRunner...')
			runner = PipelineRunner()
			self.runner = runner
			logger.info('✅ PipelineRunner created')

			logger.info('✅ Voice pipeline initialized successfully')
			return True

		except Exception as error:
			logger.error('Failed to initialize voice pipeline: %s', error, exc_info=True)
			return False

	async def run(self) -> None:
		"""Run the pipeline (blocks until cancelled)."""
		if not self.pipeline or not self.task or not self.runner:
			raise RuntimeError('Pipeline not initialized. Call initialize() first.')

		logger.info('📢 Starting voice pipeline...')
		logger.info('🎤 Microphone should be active. Speak clearly into your microphone.')
		
		# Run the pipeline task - this will block until cancelled
		# The runner handles StartFrame automatically
		try:
			await self.runner.run(self.task)
		except asyncio.CancelledError:
			logger.info('Pipeline task was cancelled')
		except Exception as e:
			logger.error('Error running pipeline: %s', e, exc_info=True)
			raise

	async def stop(self) -> None:
		"""Stop the pipeline."""
		logger.info('🛑 Stopping voice pipeline...')
		
		if self.task:
			try:
				await self.task.cancel()
			except Exception as e:
				logger.warning('Error cancelling task: %s', e)
		
		if self._transport:
			try:
				await self._transport.cleanup()
			except Exception as e:
				logger.warning('Error cleaning up transport: %s', e)
		
		logger.info('✅ Voice pipeline stopped')
