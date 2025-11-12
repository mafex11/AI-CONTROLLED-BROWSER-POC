"""Pipecat pipeline setup for voice interaction."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pipecat.frames.frames import TextFrame, StartFrame, AudioRawFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_response import LLMMessagesFrame
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

from .agent_bridge import AgentBridge

logger = logging.getLogger(__name__)


class TextToAgentProcessor(FrameProcessor):
	"""Processor that takes STT text frames and sends them to the agent bridge."""

	def __init__(self, agent_bridge: AgentBridge) -> None:
		super().__init__()
		self.agent_bridge = agent_bridge

	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process text frames from STT."""
		frame_type = type(frame).__name__
		
		# Handle StartFrame specially - need to update base class state first
		if isinstance(frame, StartFrame):
			logger.info('✅ TextToAgentProcessor received StartFrame - ready to process frames')
			# Manually set the internal state that _check_started uses
			# The base class uses _FrameProcessor__started to track if StartFrame was received
			if hasattr(self, '_FrameProcessor__started'):
				self._FrameProcessor__started = True
			# Also try other possible attribute names
			if hasattr(self, '_started'):
				self._started = True
			# Now push through normally - _check_started will pass because we set the state
			await self.push_frame(frame, direction)
			return
		
		# For all other frames, process content then push through
		if frame_type == 'InputAudioRawFrame' or frame_type == 'AudioRawFrame':
			# Audio frames should not pass through - they're consumed by STT
			# Only pass through if they're needed downstream (they shouldn't be)
			# Actually, in Pipecat, we should pass all frames through
			# But audio frames from input shouldn't reach TTS - they should be filtered
			# For now, pass them through but they'll be ignored by TTS
			pass
		elif frame_type == 'TextFrame':
			if frame.text:
				text = frame.text.strip()
				logger.info('🎤🎤🎤 STT transcribed text: "%s" 🎤🎤🎤', text)
				# Process user text in background
				asyncio.create_task(self.agent_bridge.process_user_text(text))
			else:
				logger.debug('TextToAgentProcessor: TextFrame has no text content')
		else:
			# Log other frame types (but not too frequently for common ones)
			if frame_type not in ['Frame', 'EndFrame', 'CancelFrame']:
				logger.debug('TextToAgentProcessor received frame type: %s', frame_type)
		
		# Pass frame through pipeline
		await self.push_frame(frame, direction)


class AgentToTTSProcessor(FrameProcessor):
	"""Processor that takes text from agent and sends it to TTS."""

	def __init__(self) -> None:
		super().__init__()

	async def send_text(self, text: str) -> None:
		"""Send text to TTS by pushing TextFrame directly to pipeline."""
		if text and text.strip():
			text_clean = text.strip()
			logger.info('🔊 AgentToTTSProcessor.send_text: Sending text to TTS: "%s"', text_clean)
			# Push TextFrame directly to the pipeline instead of using a queue
			# This ensures the frame goes through the normal pipeline flow
			try:
				await self.push_frame(TextFrame(text=text_clean), FrameDirection.DOWNSTREAM)
			except Exception as e:
				logger.error('Error pushing TextFrame to pipeline: %s', e, exc_info=True)
		else:
			logger.warning('AgentToTTSProcessor.send_text: empty text, ignoring')

	async def process_frame(self, frame, direction: FrameDirection) -> None:
		"""Process text frames for TTS."""
		# Handle StartFrame specially - need to update base class state first
		if isinstance(frame, StartFrame):
			logger.info('✅ AgentToTTSProcessor received StartFrame - ready to process frames')
			# Manually set the internal state that _check_started uses
			if hasattr(self, '_FrameProcessor__started'):
				self._FrameProcessor__started = True
			if hasattr(self, '_started'):
				self._started = True
			# Now push through normally - _check_started will pass because we set the state
			await self.push_frame(frame, direction)
			return
		
		# For all other frames, just pass them through
		# We only process TextFrames that come from the agent (via send_text)
		frame_type = type(frame).__name__
		
		# Log TextFrames for debugging
		if isinstance(frame, TextFrame) and frame.text:
			logger.info('🔊 AgentToTTSProcessor processing text frame: "%s"', frame.text)
		
		# Pass all frames through pipeline (audio frames, text frames, etc.)
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
		self._transport = None
		self._input_proc = None
		self._output_proc = None
		self._run_task = None  # Keep reference to run task

	async def initialize(self) -> bool:
		"""Initialize the pipeline components."""
		try:
			# Try to import local audio transport (may vary by Pipecat version)
			transport = None
			use_transport = False
			try:
				from pipecat.transports.local.audio import LocalAudioTransport
				
				# Must use LocalAudioTransportParams class, not a dict
				# Try importing params class - it's required
				params = None
				try:
					# Try different import paths for params
					try:
						from pipecat.transports.local.audio.params import LocalAudioTransportParams
					except ImportError:
						try:
							from pipecat.transports.local.params import LocalAudioTransportParams
						except ImportError:
							# Try importing from the audio module directly
							from pipecat.transports.local.audio import LocalAudioTransportParams
					
					# Create params object - try different initialization patterns
					try:
						# Try with audio_in/audio_out parameters (from web search)
						params = LocalAudioTransportParams(
							audio_in_enabled=True,
							audio_out_enabled=True,
							audio_in_sample_rate=self.sample_rate,
							audio_out_sample_rate=self.sample_rate,
							audio_in_channels=self.channels,
							audio_out_channels=self.channels,
						)
					except (TypeError, AttributeError):
						try:
							# Try with sample_rate and channels
							params = LocalAudioTransportParams(
								sample_rate=self.sample_rate,
								channels=self.channels,
							)
						except (TypeError, AttributeError):
							try:
								# Try with just sample_rate
								params = LocalAudioTransportParams(sample_rate=self.sample_rate)
							except (TypeError, AttributeError):
								# Try with default params
								params = LocalAudioTransportParams()
					
				except ImportError as e:
					# Params class is required - can't proceed without it
					logger.error('LocalAudioTransportParams class not found: %s', e)
					logger.error('This is required for LocalAudioTransport. Check pipecat-ai[local] installation.')
					params = None
				
				# Now create transport with params (required positional argument)
				if params is not None:
					try:
						# Log params before creating transport
						logger.info('📋 Creating LocalAudioTransport with params:')
						if hasattr(params, 'audio_in_enabled'):
							logger.info('   Audio Input: enabled=%s, sample_rate=%s, channels=%s', 
								getattr(params, 'audio_in_enabled', 'N/A'),
								getattr(params, 'audio_in_sample_rate', 'N/A'),
								getattr(params, 'audio_in_channels', 'N/A'))
						if hasattr(params, 'audio_out_enabled'):
							logger.info('   Audio Output: enabled=%s, sample_rate=%s, channels=%s',
								getattr(params, 'audio_out_enabled', 'N/A'),
								getattr(params, 'audio_out_sample_rate', 'N/A'),
								getattr(params, 'audio_out_channels', 'N/A'))
						
						transport = LocalAudioTransport(params)
						use_transport = True
						
						# Try to get device information
						try:
							import pyaudio
							pa = pyaudio.PyAudio()
							logger.info('🎤 Available audio devices:')
							for i in range(pa.get_device_count()):
								info = pa.get_device_info_by_index(i)
								if info['maxInputChannels'] > 0:
									logger.info('   Input Device %d: %s (channels: %d)', 
										i, info['name'], info['maxInputChannels'])
								if info['maxOutputChannels'] > 0:
									logger.info('   Output Device %d: %s (channels: %d)',
										i, info['name'], info['maxOutputChannels'])
							
							# Get default devices
							try:
								default_input = pa.get_default_input_device_info()
								logger.info('🎤 Default Input Device: %s (index: %d)', 
									default_input['name'], default_input['index'])
							except Exception as e:
								logger.warning('Could not get default input device: %s', e)
							
							try:
								default_output = pa.get_default_output_device_info()
								logger.info('🔊 Default Output Device: %s (index: %d)',
									default_output['name'], default_output['index'])
							except Exception as e:
								logger.warning('Could not get default output device: %s', e)
							
							pa.terminate()
						except Exception as e:
							logger.warning('Could not enumerate audio devices: %s', e)
						
					except Exception as e:
						logger.error('Failed to create LocalAudioTransport: %s', e, exc_info=True)
						transport = None
						use_transport = False
				else:
					logger.warning('Failed to create LocalAudioTransportParams')
					use_transport = False
					
			except ImportError as e:
				# Fallback if LocalAudioTransport not available
				logger.warning('LocalAudioTransport not available: %s', e)
				logger.warning('Install pipecat-ai[local] for local audio support')
				transport = None
				use_transport = False

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

			# Build pipeline components
			if use_transport and transport:
				logger.info('🔧 Setting up pipeline with audio transport...')
				# With transport: Transport handles both input and output
				# Check what methods/properties are available
				if hasattr(transport, 'input') and hasattr(transport, 'output'):
					# Try calling as methods
					try:
						input_proc = transport.input()
						output_proc = transport.output()
						logger.info('✅ Transport input/output processors created')
						
						# Log device info from transport if available
						if hasattr(input_proc, '_params'):
							params = input_proc._params
							if hasattr(params, 'audio_in_device_index'):
								logger.info('🎤 Using Input Device Index: %s', params.audio_in_device_index)
							if hasattr(params, 'audio_in_sample_rate'):
								logger.info('🎤 Input Sample Rate: %s Hz', params.audio_in_sample_rate)
							if hasattr(params, 'audio_in_enabled'):
								logger.info('🎤 Input Enabled: %s', params.audio_in_enabled)
						
						if hasattr(output_proc, '_params'):
							params = output_proc._params
							if hasattr(params, 'audio_out_device_index'):
								logger.info('🔊 Using Output Device Index: %s', params.audio_out_device_index)
							if hasattr(params, 'audio_out_sample_rate'):
								logger.info('🔊 Output Sample Rate: %s Hz', params.audio_out_sample_rate)
						
						# Store processors for later inspection
						self._input_proc = input_proc
						self._output_proc = output_proc
						components = [
							input_proc,
							stt_service,
							text_to_agent,
							self._agent_to_tts,
							tts_service,
							output_proc,
						]
					except TypeError:
						# Might be properties instead of methods
						input_proc = transport.input
						output_proc = transport.output
						logger.info('✅ Transport input/output accessed as properties')
						components = [
							input_proc,
							stt_service,
							text_to_agent,
							self._agent_to_tts,
							tts_service,
							output_proc,
						]
				elif hasattr(transport, 'input_processor') and hasattr(transport, 'output_processor'):
					# Alternative naming
					logger.info('✅ Transport input/output processors found (alternative naming)')
					components = [
						transport.input_processor,
						stt_service,
						text_to_agent,
						self._agent_to_tts,
						tts_service,
						transport.output_processor,
					]
				else:
					# Transport might be a single processor or used differently
					logger.warning('Transport does not have expected input/output methods. Using transport directly.')
					components = [
						transport,
						stt_service,
						text_to_agent,
						self._agent_to_tts,
						tts_service,
					]
				self._transport = transport
				logger.info('🎤 Audio transport configured: input and output enabled')
			else:
				# Without transport: STT -> TextToAgent -> AgentToTTS -> TTS
				# Note: This won't work for actual audio I/O, but allows testing
				logger.warning('No audio transport available. Audio I/O will not work.')
				components = [
					stt_service,
					text_to_agent,
					self._agent_to_tts,
					tts_service,
				]
				self._transport = None

			self.pipeline = Pipeline(components)
			# Create PipelineRunner - it's needed to run the task
			# PipelineRunner doesn't take arguments in constructor
			try:
				self.runner = PipelineRunner()
				logger.info('✅ PipelineRunner created')
			except Exception as e:
				logger.warning('Could not create PipelineRunner: %s', e)
				# Continue without runner - we'll try to use task directly
				self.runner = None

			logger.info('✅ Voice pipeline initialized successfully')
			logger.info('📋 Pipeline components: %s', [type(c).__name__ for c in components])
			return True

		except Exception as error:
			logger.error('Failed to initialize voice pipeline: %s', error, exc_info=True)
			return False

	async def start(self) -> None:
		"""Start the pipeline."""
		if not self.pipeline:
			raise RuntimeError('Pipeline not initialized. Call initialize() first.')

		# Create PipelineTask with the pipeline (not the runner)
		# PipelineTask wraps the pipeline and can be run by PipelineRunner
		logger.info('🔍 Creating PipelineTask...')
		try:
			# PipelineTask should be created with the pipeline
			self.task = PipelineTask(self.pipeline)
			logger.info('✅ PipelineTask created with pipeline')
		except Exception as e:
			logger.error('Failed to create PipelineTask: %s', e, exc_info=True)
			raise
		
		# PipelineTask needs to be running for audio to work
		# Use PipelineRunner.run(task) to actually run it
		logger.info('🔍 Setting up PipelineTask execution...')
		logger.info('   Task type: %s', type(self.task).__name__)
		logger.info('   Runner type: %s', type(self.runner).__name__ if self.runner else 'None')
		
		# Store the run task so it doesn't get garbage collected
		self._run_task = None
		
		# Use PipelineRunner.run() to actually run the task
		if self.runner:
			logger.info('📢 Starting PipelineRunner.run(task)...')
			try:
				# PipelineRunner.run() keeps the task running until cancelled
				# This is the correct way to run a PipelineTask
				# Don't await it - run it in background
				self._run_task = asyncio.create_task(self.runner.run(self.task))
				logger.info('✅ PipelineRunner.run(task) started in background')
				logger.info('   Run task: %s', self._run_task)
				logger.info('   Run task done: %s', self._run_task.done())
				
				# Give it a moment to start and initialize
				await asyncio.sleep(0.2)
				logger.info('   After 0.2s - Run task done: %s', self._run_task.done())
				if self._run_task.done():
					try:
						result = await self._run_task
						logger.warning('⚠️ Run task completed immediately with result: %s', result)
						# If task completed, don't continue - something went wrong
						raise RuntimeError('Pipeline run task completed immediately - pipeline may not be working correctly')
					except asyncio.CancelledError:
						logger.error('⚠️ Run task was cancelled immediately!')
						raise
					except Exception as e:
						logger.error('⚠️ Run task failed immediately: %s', e, exc_info=True)
						raise
			except Exception as e:
				logger.error('Failed to start PipelineRunner.run(): %s', e, exc_info=True)
				raise
		else:
			logger.error('No PipelineRunner available - cannot run task!')
			raise RuntimeError('PipelineRunner is required to run PipelineTask')
		
		# Check if transport is running
		if self._transport:
			logger.info('🔍 Checking audio transport status...')
			try:
				if hasattr(self._transport, '_input') and self._transport._input:
					logger.info('✅ Audio input transport is initialized')
					input_transport = self._transport._input
					if hasattr(input_transport, '_pyaudio'):
						logger.info('✅ PyAudio instance is available for input')
					if hasattr(input_transport, '_stream'):
						stream = input_transport._stream
						if stream:
							logger.info('✅ Audio input stream exists')
							if hasattr(stream, 'is_active'):
								logger.info('   Stream active: %s', stream.is_active())
							if hasattr(stream, 'is_stopped'):
								logger.info('   Stream stopped: %s', stream.is_stopped())
						else:
							logger.warning('⚠️ Audio input stream is None - audio capture may not be active!')
					else:
						logger.warning('⚠️ Input transport has no _stream attribute')
					
					# Check if transport needs to be started with a frame
					# In Pipecat, audio transports typically start when receiving StartFrame
					logger.info('📢 Attempting to start audio input stream...')
					try:
						from pipecat.frames.frames import StartFrame
						# Push a StartFrame to trigger audio capture
						await input_transport.push_frame(StartFrame(), FrameDirection.DOWNSTREAM)
						logger.info('✅ StartFrame pushed to input transport')
					except Exception as e:
						logger.warning('Could not push StartFrame to input transport: %s', e)
					
					# Also check for other start methods
					if hasattr(input_transport, 'start_stream'):
						logger.info('📢 Input transport has start_stream() method - calling it...')
						try:
							input_transport.start_stream()
							logger.info('✅ Input transport stream started')
						except Exception as e:
							logger.warning('Could not start_stream: %s', e)
					
				if hasattr(self._transport, '_output') and self._transport._output:
					logger.info('✅ Audio output transport is initialized')
			except Exception as e:
				logger.warning('Could not check transport status: %s', e, exc_info=True)
		
		# Check input processor more thoroughly
		if self._input_proc:
			logger.info('🔍 Inspecting input processor...')
			logger.info('   Type: %s', type(self._input_proc).__name__)
			logger.info('   Attributes: %s', [attr for attr in dir(self._input_proc) if not attr.startswith('__')][:10])
			
			# Try to check if stream is open
			if hasattr(self._input_proc, '_stream'):
				stream = self._input_proc._stream
				if stream:
					logger.info('   Stream type: %s', type(stream).__name__)
					if hasattr(stream, 'is_active'):
						logger.info('   Stream is_active(): %s', stream.is_active())
					if hasattr(stream, 'is_stopped'):
						logger.info('   Stream is_stopped(): %s', stream.is_stopped())
				else:
					logger.warning('   ⚠️ Stream is None!')
		
		# Push StartFrame to trigger audio capture
		logger.info('📢 Pushing StartFrame to pipeline to begin audio capture...')
		try:
			await self.task.queue_frames([StartFrame()])
			logger.info('✅ StartFrame queued')
		except Exception as e:
			logger.warning('Could not queue StartFrame: %s', e)
		
		await self.task.queue_frames([LLMMessagesFrame(messages=[])])
		
		logger.info('✅ Voice pipeline started and listening for audio input...')
		logger.info('🎤 Microphone should be active. Speak clearly into your microphone.')
		logger.info('💡 If you don\'t see audio frames in the logs, check:')
		logger.info('   1. Microphone permissions are granted')
		logger.info('   2. Microphone is not muted')
		logger.info('   3. Correct microphone is selected as default input device')
		logger.info('   4. Speak into: Line 1/2 (M-Audio AIR 192 4) - your default input device')

	async def stop(self) -> None:
		"""Stop the pipeline."""
		# Cancel the run task first
		if self._run_task:
			logger.info('Cancelling PipelineTask run task...')
			self._run_task.cancel()
			try:
				await self._run_task
			except asyncio.CancelledError:
				pass
			self._run_task = None
		
		if self.task:
			await self.task.cancel()
			self.task = None

		if self.runner:
			await self.runner.cancel()
			self.runner = None

		logger.info('Voice pipeline stopped')

