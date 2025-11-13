"""Pipecat pipeline setup for voice interaction."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pipecat.frames.frames import TextFrame, StartFrame, TranscriptionFrame, InterimTranscriptionFrame, BotStartedSpeakingFrame, BotStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.deepgram.stt import DeepgramSTTService

try:
	from deepgram import LiveOptions
except ImportError:
	LiveOptions = None
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from .agent_bridge import AgentBridge

logger = logging.getLogger(__name__)


class TextToAgentProcessor(FrameProcessor):
	"""Processor that takes STT text frames and sends them to the agent bridge.
	
	Buffers transcription frames to wait for 2 seconds of silence before processing.
	This prevents premature task execution when user is still speaking mid-sentence.
	"""

	def __init__(self, agent_bridge: AgentBridge, silence_delay: float = 2.0) -> None:
		super().__init__()
		self.agent_bridge = agent_bridge
		self.silence_delay = silence_delay  # Wait 2 seconds of silence before processing
		self._accumulated_transcription: list[str] = []  # Accumulate multiple final transcriptions
		self._last_final_text: Optional[str] = None  # Track last final transcription text
		self._transcription_timer: Optional[asyncio.Task] = None
		self._last_interim_text: Optional[str] = None

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
		
		# Handle interim transcriptions - track the latest interim text
		if isinstance(frame, InterimTranscriptionFrame) and frame.text:
			interim_text = frame.text.strip()
			if interim_text:
				self._last_interim_text = interim_text
				logger.debug('🎤 Interim transcription: "%s"', interim_text)
				# Cancel any pending timer - user is still speaking
				if self._transcription_timer and not self._transcription_timer.done():
					self._transcription_timer.cancel()
					self._transcription_timer = None
					logger.debug('🔄 Cancelled pending transcription timer - user still speaking')
		
		# Handle FINAL TranscriptionFrames - accumulate chunks and wait for silence
		elif isinstance(frame, TranscriptionFrame) and frame.text:
			text = frame.text.strip()
			if text:
				# Deepgram may chunk long sentences into multiple final transcriptions
				# We need to accumulate all chunks until we get silence
				
				# Check if this is a continuation of the previous final transcription
				# (Deepgram may send overlapping chunks, so we check if the new text extends the previous)
				if self._last_final_text and text.startswith(self._last_final_text):
					# This is an extension - replace the last chunk with the longer version
					logger.debug('🎤 Final transcription extended: "%s" -> "%s"', 
						self._last_final_text, text)
					if self._accumulated_transcription:
						self._accumulated_transcription[-1] = text
					else:
						self._accumulated_transcription.append(text)
				elif self._last_final_text and self._last_final_text in text:
					# Previous text is contained in new text (new text is longer/supersedes)
					logger.debug('🎤 Final transcription superseded: "%s" -> "%s"', 
						self._last_final_text, text)
					if self._accumulated_transcription:
						self._accumulated_transcription[-1] = text
					else:
						self._accumulated_transcription.append(text)
				else:
					# New chunk - add it to accumulation
					logger.debug('🎤 Received final transcription chunk: "%s" (accumulating)', text)
					self._accumulated_transcription.append(text)
				
				self._last_final_text = text
				
				# Cancel any existing timer - we got a new transcription chunk
				if self._transcription_timer and not self._transcription_timer.done():
					self._transcription_timer.cancel()
				
				# Start timer to wait for silence period
				# If no new transcription (interim or final) arrives within silence_delay, process accumulated text
				self._transcription_timer = asyncio.create_task(
					self._process_transcription_after_silence()
				)
		
		# ALWAYS push all frames - super().process_frame() doesn't push, it just processes
		await self.push_frame(frame, direction)
	
	async def _process_transcription_after_silence(self) -> None:
		"""Wait for silence_delay seconds, then process accumulated transcription if no new one arrived."""
		try:
			await asyncio.sleep(self.silence_delay)
			
			# Check if we still have accumulated transcription and no interim text arrived
			if self._accumulated_transcription:
				# Double-check: if we got interim text recently, don't process yet
				# Wait a bit more to see if user continues speaking
				if self._last_interim_text:
					# Check if interim text is different from our accumulated text
					accumulated_full = ' '.join(self._accumulated_transcription)
					if self._last_interim_text != accumulated_full and not accumulated_full in self._last_interim_text:
						logger.debug('🔄 Interim text detected after final, waiting more...')
						# Reset interim text and wait again
						self._last_interim_text = None
						await asyncio.sleep(self.silence_delay)
						# Check again - if we still have accumulated text, process it
						if self._accumulated_transcription:
							self._process_transcription()
					else:
						# Interim text matches or extends our accumulated text - process it
						self._process_transcription()
				else:
					# No interim text - process accumulated transcription
					self._process_transcription()
		except asyncio.CancelledError:
			logger.debug('🎤 Transcription processing cancelled (new transcription received)')
		except Exception as e:
			logger.error('Error processing transcription after silence: %s', e, exc_info=True)
	
	def _process_transcription(self) -> None:
		"""Process the accumulated transcription by sending it to the agent bridge."""
		if not self._accumulated_transcription:
			return
		
		# Concatenate all accumulated chunks into full text
		# Remove duplicates and overlaps intelligently
		full_text = self._merge_transcription_chunks(self._accumulated_transcription)
		
		if full_text and full_text.strip():
			logger.info('🎤🎤🎤 STT final transcription (after %fs silence): "%s" 🎤🎤🎤', 
				self.silence_delay, full_text)
			# Clear accumulated transcription
			self._accumulated_transcription.clear()
			self._last_final_text = None
			self._last_interim_text = None
			# Process user text in background
			asyncio.create_task(self.agent_bridge.process_user_text(full_text))
	
	def _merge_transcription_chunks(self, chunks: list[str]) -> str:
		"""Merge transcription chunks, handling overlaps and duplicates."""
		if not chunks:
			return ''
		if len(chunks) == 1:
			return chunks[0]
		
		# Start with first chunk
		result = chunks[0]
		
		for chunk in chunks[1:]:
			chunk = chunk.strip()
			if not chunk:
				continue
			
			# Check if chunk is already contained in result
			if chunk in result:
				continue
			
			# Check if result is contained in chunk (chunk is longer/supersedes)
			if result in chunk:
				result = chunk
				continue
			
			# Try to find overlap - check if end of result matches start of chunk
			overlap_found = False
			for overlap_len in range(min(len(result), len(chunk)), 0, -1):
				if result[-overlap_len:].lower() == chunk[:overlap_len].lower():
					# Found overlap - append only the non-overlapping part
					result = result + chunk[overlap_len:]
					overlap_found = True
					break
			
			if not overlap_found:
				# No overlap - just append with space
				result = result + ' ' + chunk
		
		return result.strip()


class SpeechCompletionTracker(FrameProcessor):
	"""Processor that tracks speech state and provides awaitable completion.
	
	Handles multiple speech chunks from TTS services that split long text.
	Waits for all chunks to complete before resolving completion futures.
	"""
	
	def __init__(self) -> None:
		super().__init__()
		self._is_speaking = False
		self._speech_chunk_count = 0  # Track number of active speech chunks
		self._speech_completion_futures: list[asyncio.Future] = []
		self._speech_start_futures: list[asyncio.Future] = []
		self._speech_silence_timer: Optional[asyncio.Task] = None
	
	async def wait_for_speech_completion(self, timeout: float = 60.0) -> None:
		"""Wait for speech to start, then complete. Handles multiple chunks from TTS.
		
		Some TTS services (like ElevenLabs) split long text into multiple chunks.
		We wait for ALL chunks to finish before allowing actions to execute.
		"""
		logger.debug('Waiting for speech to start and complete...')
		
		# First, wait for speech to start (if not already speaking)
		if not self._is_speaking:
			logger.debug('Speech not started yet, waiting for BotStartedSpeakingFrame...')
			start_future = asyncio.get_event_loop().create_future()
			self._speech_start_futures.append(start_future)
			try:
				await asyncio.wait_for(start_future, timeout=timeout)
				logger.debug('Speech started')
			except asyncio.TimeoutError:
				logger.warning('Timeout waiting for speech to start')
				if start_future in self._speech_start_futures:
					self._speech_start_futures.remove(start_future)
				if not start_future.done():
					start_future.cancel()
				return
			except Exception as e:
				logger.error('Error waiting for speech to start: %s', e)
				if start_future in self._speech_start_futures:
					self._speech_start_futures.remove(start_future)
				if not start_future.done():
					start_future.cancel()
				return
		
		# Now wait for ALL speech chunks to complete
		# We wait for a period of silence after speech stops to ensure no more chunks
		completion_future = asyncio.get_event_loop().create_future()
		self._speech_completion_futures.append(completion_future)
		logger.debug('Waiting for all speech chunks to complete...')
		try:
			await asyncio.wait_for(completion_future, timeout=timeout)
			logger.debug('All speech chunks completed')
		except asyncio.TimeoutError:
			logger.warning('Timeout waiting for speech completion')
			if completion_future in self._speech_completion_futures:
				self._speech_completion_futures.remove(completion_future)
			if not completion_future.done():
				completion_future.cancel()
		except Exception as e:
			logger.error('Error waiting for speech completion: %s', e)
			if completion_future in self._speech_completion_futures:
				self._speech_completion_futures.remove(completion_future)
			if not completion_future.done():
				completion_future.cancel()
	
	async def _check_speech_complete(self) -> None:
		"""Check if speech has been silent long enough to consider it complete."""
		# Wait for a short silence period to ensure no more chunks are coming
		await asyncio.sleep(0.3)  # 300ms silence threshold - reduced for faster response
		
		# If still not speaking, resolve all completion futures
		if not self._is_speaking and self._speech_chunk_count == 0:
			logger.debug('Speech silence confirmed - resolving completion futures')
			for future in self._speech_completion_futures[:]:
				if not future.done():
					future.set_result(None)
			self._speech_completion_futures.clear()
	
	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process frames and track speech state."""
		await super().process_frame(frame, direction)
		
		if isinstance(frame, BotStartedSpeakingFrame):
			self._is_speaking = True
			self._speech_chunk_count += 1
			logger.debug(f'Bot started speaking (chunk {self._speech_chunk_count})')
			# Cancel any pending silence timer
			if self._speech_silence_timer and not self._speech_silence_timer.done():
				self._speech_silence_timer.cancel()
			self._speech_silence_timer = None
			# Resolve all waiting start futures (only need to do this once)
			if self._speech_chunk_count == 1:
				for future in self._speech_start_futures[:]:
					if not future.done():
						future.set_result(None)
				self._speech_start_futures.clear()
		elif isinstance(frame, BotStoppedSpeakingFrame):
			self._speech_chunk_count = max(0, self._speech_chunk_count - 1)
			logger.debug(f'Bot stopped speaking (remaining chunks: {self._speech_chunk_count})')
			
			# If this was the last chunk, start silence timer
			if self._speech_chunk_count == 0:
				self._is_speaking = False
				# Wait for a short period of silence to ensure no more chunks
				if not self._speech_silence_timer or self._speech_silence_timer.done():
					self._speech_silence_timer = asyncio.create_task(self._check_speech_complete())
		
		# Always push frames through
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
			logger.debug('🔍 AgentToTTSProcessor.send_text: Empty text, skipping')
			return
		
		text_clean = text.strip()
		logger.info('🔊 AgentToTTSProcessor.send_text: Sending text to TTS: "%s"', text_clean)
		
		try:
			# Push TextFrame into the pipeline - it will flow to TTS service
			# The processor is already started as part of pipeline initialization
			logger.debug(f'🔍 AgentToTTSProcessor.send_text: Pushing TextFrame with text="{text_clean[:100]}{"..." if len(text_clean) > 100 else ""}"')
			text_frame = TextFrame(text=text_clean)
			await self.push_frame(text_frame, FrameDirection.DOWNSTREAM)
			logger.debug(f'🔍 AgentToTTSProcessor.send_text: TextFrame pushed successfully')
		except Exception as e:
			logger.error('❌ Error pushing TextFrame to pipeline: %s', e, exc_info=True)
			raise  # Re-raise to see the full error

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
		self._speech_tracker: Optional[SpeechCompletionTracker] = None
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

			# Initialize Deepgram STT with endpointing delay
			logger.info('Initializing Deepgram STT service...')
			
			# Configure endpointing delay to wait longer before finalizing utterances
			# This prevents half-sentences from being registered as complete queries
			# utterance_end_ms: milliseconds of silence before Deepgram finalizes the utterance
			# Note: utterance_end_ms requires vad_events=True, but SileroVAD handles transport-level VAD
			# Deepgram VAD events are only used for utterance finalization timing
			deepgram_options = None
			if LiveOptions:
				# Set utterance_end_ms to 2000ms (2 seconds) to add delay before finalizing
				# This allows for natural speech pauses without splitting into separate tasks
				# Higher value = longer wait before finalizing, preventing premature sentence splitting
				deepgram_options = LiveOptions(
					vad_events=True,  # Required for utterance_end_ms to work
					utterance_end_ms="2000",  # Wait 2s of silence before finalizing (string format)
					interim_results=True,  # Keep interim results enabled for real-time feedback
				)
				logger.info('✅ Deepgram LiveOptions configured (utterance_end_ms="2000"ms, vad_events=True)')
			else:
				logger.warning('LiveOptions not available - Deepgram endpointing delay not configured')
			
			stt_service = DeepgramSTTService(
				api_key=self.deepgram_api_key,
				language=self.deepgram_language,
				live_options=deepgram_options,
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
			self._speech_tracker = SpeechCompletionTracker()
			
			# Connect agent bridge to TTS processor and speech tracker
			self.agent_bridge.set_tts_processor(self._agent_to_tts)
			self.agent_bridge.set_speech_tracker(self._speech_tracker)

			# Build pipeline: Input -> STT -> TextToAgent -> AgentToTTS -> TTS -> SpeechTracker -> Output
			logger.info('🔧 Building pipeline...')
			pipeline = Pipeline(
				[
					transport.input(),  # Transport user input
					stt_service,  # STT
					text_to_agent,  # Process text and send to agent
					self._agent_to_tts,  # Pass through, can inject TextFrames from agent
					tts_service,  # TTS
					self._speech_tracker,  # Track speech completion
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
