"""Pipecat pipeline setup for voice interaction."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pipecat.frames.frames import TextFrame, StartFrame, TranscriptionFrame, InterimTranscriptionFrame, BotStartedSpeakingFrame, BotStoppedSpeakingFrame, TTSAudioRawFrame, UserStartedSpeakingFrame, InterruptionFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.deepgram.stt import DeepgramSTTService
import base64

try:
	from deepgram import LiveOptions
except ImportError:
	LiveOptions = None
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from .agent_bridge import AgentBridge

logger = logging.getLogger(__name__)

USER_SILENCE_DELAY_SECONDS = 1.0
TEXT_TO_AGENT_BUFFER_DELAY_SECONDS = 0.05


class TextToAgentProcessor(FrameProcessor):
	"""Processor that sends STT text frames to agent bridge."""

	def __init__(self, agent_bridge: AgentBridge, silence_delay: float = TEXT_TO_AGENT_BUFFER_DELAY_SECONDS) -> None:
		super().__init__()
		self.agent_bridge = agent_bridge
		self.silence_delay = silence_delay
		self._accumulated_transcription: list[str] = []
		self._last_final_text: Optional[str] = None
		self._transcription_timer: Optional[asyncio.Task] = None
		self._last_interim_text: Optional[str] = None

	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process text frames from STT."""
		if isinstance(frame, StartFrame):
			await super().process_frame(frame, direction)
			await self.push_frame(frame, direction)
			return
		
		await super().process_frame(frame, direction)
		
		if isinstance(frame, InterimTranscriptionFrame) and frame.text:
			interim_text = frame.text.strip()
			if interim_text:
				self._last_interim_text = interim_text
				logger.debug('Interim transcription: "%s"', interim_text)
				if self._transcription_timer and not self._transcription_timer.done():
					self._transcription_timer.cancel()
					self._transcription_timer = None
					logger.debug('Cancelled pending transcription timer - user still speaking')
		
		elif isinstance(frame, TranscriptionFrame) and frame.text:
			text = frame.text.strip()
			if text:
				if self._last_final_text and text.startswith(self._last_final_text):
					logger.debug('Final transcription extended: "%s" -> "%s"', 
						self._last_final_text, text)
					if self._accumulated_transcription:
						self._accumulated_transcription[-1] = text
					else:
						self._accumulated_transcription.append(text)
				elif self._last_final_text and self._last_final_text in text:
					logger.debug('Final transcription superseded: "%s" -> "%s"', 
						self._last_final_text, text)
					if self._accumulated_transcription:
						self._accumulated_transcription[-1] = text
					else:
						self._accumulated_transcription.append(text)
				else:
					logger.debug('Received final transcription chunk: "%s" (accumulating)', text)
					self._accumulated_transcription.append(text)
				
				self._last_final_text = text
				
				if self._transcription_timer and not self._transcription_timer.done():
					self._transcription_timer.cancel()
				
				self._transcription_timer = asyncio.create_task(
					self._process_transcription_after_silence()
				)
		
		await self.push_frame(frame, direction)
	
	async def _process_transcription_after_silence(self) -> None:
		"""Wait for silence then process accumulated transcription."""
		try:
			await asyncio.sleep(self.silence_delay)
			
			if self._accumulated_transcription:
				if self._last_interim_text:
					accumulated_full = ' '.join(self._accumulated_transcription)
					if self._last_interim_text != accumulated_full and not accumulated_full in self._last_interim_text:
						logger.debug('Interim text detected after final, waiting more...')
						self._last_interim_text = None
						await asyncio.sleep(self.silence_delay)
						if self._accumulated_transcription:
							self._process_transcription()
					else:
						self._process_transcription()
				else:
					self._process_transcription()
		except asyncio.CancelledError:
			logger.debug('Transcription processing cancelled (new transcription received)')
		except Exception as e:
			logger.error('Error processing transcription after silence: %s', e, exc_info=True)
	
	def _process_transcription(self) -> None:
		"""Process accumulated transcription by sending to agent bridge."""
		if not self._accumulated_transcription:
			return
		
		full_text = self._merge_transcription_chunks(self._accumulated_transcription)
		
		if full_text and full_text.strip():
			logger.info('Heard: "%s"', full_text)
			self._accumulated_transcription.clear()
			self._last_final_text = None
			self._last_interim_text = None
			asyncio.create_task(self.agent_bridge.process_user_text(full_text))
	
	def _merge_transcription_chunks(self, chunks: list[str]) -> str:
		"""Merge transcription chunks handling overlaps."""
		if not chunks:
			return ''
		if len(chunks) == 1:
			return chunks[0]
		
		result = chunks[0]
		
		for chunk in chunks[1:]:
			chunk = chunk.strip()
			if not chunk:
				continue
			
			if chunk in result:
				continue
			
			if result in chunk:
				result = chunk
				continue
			
			overlap_found = False
			for overlap_len in range(min(len(result), len(chunk)), 0, -1):
				if result[-overlap_len:].lower() == chunk[:overlap_len].lower():
					result = result + chunk[overlap_len:]
					overlap_found = True
					break
			
			if not overlap_found:
				result = result + ' ' + chunk
		
		return result.strip()


class SpeechCompletionTracker(FrameProcessor):
	"""Tracks speech state and provides completion."""
	
	def __init__(self) -> None:
		super().__init__()
		self._is_speaking = False
		self._speech_chunk_count = 0
		self._speech_completion_futures: list[asyncio.Future] = []
		self._speech_start_futures: list[asyncio.Future] = []
		self._speech_silence_timer: Optional[asyncio.Task] = None
	
	async def wait_for_speech_completion(self, timeout: float = 60.0) -> None:
		"""Wait for speech to start then complete."""
		logger.debug('Waiting for speech to start and complete...')
		
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
		"""Check if speech has been silent long enough."""
		await asyncio.sleep(0.3)
		
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
			if self._speech_silence_timer and not self._speech_silence_timer.done():
				self._speech_silence_timer.cancel()
			self._speech_silence_timer = None
			if self._speech_chunk_count == 1:
				for future in self._speech_start_futures[:]:
					if not future.done():
						future.set_result(None)
				self._speech_start_futures.clear()
		elif isinstance(frame, BotStoppedSpeakingFrame):
			self._speech_chunk_count = max(0, self._speech_chunk_count - 1)
			logger.debug(f'Bot stopped speaking (remaining chunks: {self._speech_chunk_count})')
			
			if self._speech_chunk_count == 0:
				self._is_speaking = False
				if not self._speech_silence_timer or self._speech_silence_timer.done():
					self._speech_silence_timer = asyncio.create_task(self._check_speech_complete())
		
		await self.push_frame(frame, direction)


class AudioStreamProcessor(FrameProcessor):
	"""Processor that streams TTS audio to frontend via WebSocket."""

	def __init__(self, websocket_sender=None) -> None:
		super().__init__()
		self._websocket_sender = websocket_sender

	def set_websocket_sender(self, sender) -> None:
		"""Set WebSocket sender function."""
		self._websocket_sender = sender

	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process frames and stream TTS audio to frontend."""
		await super().process_frame(frame, direction)
		
		# Handle interruptions - notify frontend to stop audio
		if isinstance(frame, (UserStartedSpeakingFrame, InterruptionFrame)) and self._websocket_sender:
			try:
				if asyncio.iscoroutinefunction(self._websocket_sender):
					await self._websocket_sender({
						"type": "interruption",
					})
				else:
					self._websocket_sender({
						"type": "interruption",
					})
			except Exception as e:
				logger.warning('Error sending interruption signal: %s', e)
		
		# Intercept TTS audio frames and stream to frontend
		if isinstance(frame, TTSAudioRawFrame) and self._websocket_sender:
			try:
				# Convert audio bytes to base64
				audio_base64 = base64.b64encode(frame.audio).decode('utf-8')
				
				# Send to frontend via WebSocket
				if asyncio.iscoroutinefunction(self._websocket_sender):
					await self._websocket_sender({
						"type": "audio_chunk",
						"audio": audio_base64,
						"sample_rate": frame.sample_rate,
						"num_channels": frame.num_channels,
					})
				else:
					self._websocket_sender({
						"type": "audio_chunk",
						"audio": audio_base64,
						"sample_rate": frame.sample_rate,
						"num_channels": frame.num_channels,
					})
			except Exception as e:
				logger.warning('Error streaming audio to frontend: %s', e)
		
		# Always pass frames through to the next processor
		await self.push_frame(frame, direction)


class AgentToTTSProcessor(FrameProcessor):
	"""Sends text from agent to TTS."""

	def __init__(self) -> None:
		super().__init__()

	async def send_text(self, text: str) -> None:
		"""Send text to TTS by pushing TextFrame into pipeline."""
		if not text or not text.strip():
			logger.debug('AgentToTTSProcessor.send_text: Empty text, skipping')
			return
		
		text_clean = text.strip()
		logger.debug('AgentToTTSProcessor.send_text: Sending text to TTS: "%s"', text_clean)
		
		try:
			logger.debug(f'AgentToTTSProcessor.send_text: Pushing TextFrame with text="{text_clean[:100]}{"..." if len(text_clean) > 100 else ""}"')
			text_frame = TextFrame(text=text_clean)
			await self.push_frame(text_frame, FrameDirection.DOWNSTREAM)
			logger.debug(f'AgentToTTSProcessor.send_text: TextFrame pushed successfully')
		except Exception as e:
			logger.error('Error pushing TextFrame to pipeline: %s', e, exc_info=True)
			raise  # Re-raise to see the full error

	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process frames and pass everything through."""
		if isinstance(frame, StartFrame):
			await super().process_frame(frame, direction)
			await self.push_frame(frame, direction)
			return
		
		await super().process_frame(frame, direction)
		await self.push_frame(frame, direction)


class VoicePipeline:
	"""Manages Pipecat voice pipeline for STT and TTS."""

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
		self._audio_stream_processor: Optional[AudioStreamProcessor] = None

	async def initialize(self) -> bool:
		"""Initialize pipeline components."""
		try:
			logger.debug(
				'Creating LocalAudioTransport with VAD (%.1fs pause threshold)...',
				USER_SILENCE_DELAY_SECONDS,
			)
			try:
				vad_params = VADParams(stop_secs=USER_SILENCE_DELAY_SECONDS)
				vad_analyzer = SileroVADAnalyzer(params=vad_params)
				
				transport_params = LocalAudioTransportParams(
					audio_in_enabled=True,
					audio_out_enabled=True,  # Enable local audio output for voice mode
					vad_analyzer=vad_analyzer,
				)
				transport = LocalAudioTransport(transport_params)
				logger.debug(
					'LocalAudioTransport created with VAD (stop_secs=%.1f)',
					USER_SILENCE_DELAY_SECONDS,
				)
			except OSError as e:
				error_msg = str(e)
				logger.error('Audio device error: %s', error_msg)
				
				try:
					import pyaudio
					pa = pyaudio.PyAudio()
					logger.debug('Available audio input devices:')
					has_input = False
					for i in range(pa.get_device_count()):
						info = pa.get_device_info_by_index(i)
						if info['maxInputChannels'] > 0:
							has_input = True
							default_str = ' (DEFAULT)' if i == pa.get_default_input_device_info()['index'] else ''
							logger.info(
								'  Device %d: %s (channels: %d)%s',
								i,
								info['name'],
								info['maxInputChannels'],
								default_str,
							)
					pa.terminate()
					
					if not has_input:
						logger.error('No audio input devices found!')
						logger.error('Solutions:')
						logger.error('  1. Check that a microphone is connected')
						logger.error('  2. Check Windows microphone permissions (Settings > Privacy > Microphone)')
						logger.error('  3. Ensure no other application is using the microphone')
						logger.error('  4. Update audio drivers')
					else:
						logger.error('Solutions:')
						logger.error('  1. Check Windows microphone permissions (Settings > Privacy > Microphone)')
						logger.error('  2. Ensure no other application is using the microphone')
						logger.error('  3. Try closing and reopening the application')
						logger.error('  4. Restart your computer if the issue persists')
				except Exception as list_error:
					logger.warning('Could not list audio devices: %s', list_error)
				
				logger.error('Full error details:', exc_info=True)
				return False
			except Exception as e:
				logger.error('Failed to create LocalAudioTransport: %s', e, exc_info=True)
				return False

			self._transport = transport

			logger.debug('Initializing Deepgram STT service...')
			
			deepgram_options = None
			if LiveOptions:
				utterance_end_ms = str(int(USER_SILENCE_DELAY_SECONDS * 1000))
				deepgram_options = LiveOptions(
					vad_events=True,  # Required for utterance_end_ms to work
					utterance_end_ms=utterance_end_ms,
					interim_results=True,  # Keep interim results enabled for real-time feedback
				)
				logger.debug(
					'Deepgram LiveOptions configured (utterance_end_ms="%sms", vad_events=True)',
					utterance_end_ms,
				)
			else:
				logger.warning('LiveOptions not available - Deepgram endpointing delay not configured')
			
			stt_service = DeepgramSTTService(
				api_key=self.deepgram_api_key,
				language=self.deepgram_language,
				live_options=deepgram_options,
			)
			logger.debug('Deepgram STT service initialized (language: %s)', self.deepgram_language)

			logger.debug('Initializing ElevenLabs TTS service...')
			tts_service = ElevenLabsTTSService(
				api_key=self.elevenlabs_api_key,
				voice_id=self.elevenlabs_voice_id,
			)
			logger.debug('ElevenLabs TTS service initialized (voice_id: %s)', self.elevenlabs_voice_id)

			text_to_agent = TextToAgentProcessor(self.agent_bridge)
			self._agent_to_tts = AgentToTTSProcessor()
			self._speech_tracker = SpeechCompletionTracker()
			self._audio_stream_processor = AudioStreamProcessor()
			
			self.agent_bridge.set_tts_processor(self._agent_to_tts)
			self.agent_bridge.set_speech_tracker(self._speech_tracker)

			logger.debug('Building pipeline...')
			pipeline = Pipeline(
				[
					transport.input(),
					stt_service,
					text_to_agent,
					self._agent_to_tts,
					tts_service,
					self._audio_stream_processor,  # Stream audio to frontend (optional)
					self._speech_tracker,
					transport.output(),  # Local audio output for voice mode
				]
			)
			
			self.pipeline = pipeline
			logger.debug('Pipeline created')

			logger.debug('Creating PipelineTask...')
			task = PipelineTask(pipeline)
			self.task = task
			logger.debug('PipelineTask created')

			logger.debug('Creating PipelineRunner...')
			runner = PipelineRunner()
			self.runner = runner
			logger.debug('PipelineRunner created')

			logger.info('Voice pipeline ready')
			return True

		except Exception as error:
			logger.error('Failed to initialize voice pipeline: %s', error, exc_info=True)
			return False

	async def run(self) -> None:
		"""Run pipeline until cancelled."""
		if not self.pipeline or not self.task or not self.runner:
			raise RuntimeError('Pipeline not initialized. Call initialize() first.')

		logger.debug('Starting voice pipeline...')
		logger.debug('Microphone should be active. Speak clearly into your microphone.')
		
		try:
			await self.runner.run(self.task)
		except asyncio.CancelledError:
			logger.info('Pipeline task was cancelled')
		except Exception as e:
			logger.error('Error running pipeline: %s', e, exc_info=True)
			raise

	def set_websocket_sender(self, sender) -> None:
		"""Set WebSocket sender for audio streaming."""
		if self._audio_stream_processor:
			self._audio_stream_processor.set_websocket_sender(sender)

	async def stop(self) -> None:
		"""Stop pipeline and clean up resources."""
		logger.info('Stopping voice pipeline completely...')
		
		# Cancel the runner task if it exists
		if self.runner:
			try:
				await self.runner.cancel()
				logger.debug('Pipeline runner cancelled')
			except Exception as e:
				logger.warning('Error cancelling runner: %s', e)
		
		# Cancel the pipeline task
		if self.task:
			try:
				await self.task.cancel()
				logger.debug('Pipeline task cancelled')
			except Exception as e:
				logger.warning('Error cancelling task: %s', e)
		
		# Clean up transport (this stops audio input)
		if self._transport:
			try:
				# Send EndFrame to stop the transport
				from pipecat.frames.frames import EndFrame
				await self._transport.stop(EndFrame())
				await self._transport.cleanup()
				logger.debug('Transport cleaned up - audio input stopped')
			except Exception as e:
				logger.warning('Error cleaning up transport: %s', e)
		
		logger.info('Voice pipeline stopped completely - no longer listening')
