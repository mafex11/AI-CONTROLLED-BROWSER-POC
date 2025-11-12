"""Bridge between Pipecat pipeline and the browser agent."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from pipecat.frames.frames import TextFrame
from pipecat.processors.frame_processor import FrameDirection

from ..browser_use_integration import BrowserUseIntegration

logger = logging.getLogger(__name__)


class AgentBridge:
	"""Bridges Pipecat text frames to the browser agent and back."""

	def __init__(
		self,
		integration: BrowserUseIntegration,
		*,
		on_user_speech: Optional[Callable[[str], None]] = None,
		on_agent_response: Optional[Callable[[str], None]] = None,
	) -> None:
		self.integration = integration
		self.on_user_speech = on_user_speech
		self.on_agent_response = on_agent_response
		self._processing = False
		self._current_task: Optional[asyncio.Task] = None
		self._tts_processor = None

	async def process_user_text(self, text: str) -> None:
		"""Process user speech transcript and run agent."""
		logger.info('📝 AgentBridge.process_user_text called with: "%s"', text)
		if not text or not text.strip():
			logger.warning('AgentBridge: Empty text received, ignoring')
			return

		text = text.strip()
		text_lower = text.lower()
		logger.info('🎯 Processing user speech: "%s"', text)
		
		# Handle exit commands
		if text_lower in {'exit', 'quit', 'stop', 'goodbye'}:
			logger.info('User requested exit')
			if self.on_user_speech:
				try:
					self.on_user_speech('exit')
				except Exception as e:
					logger.warning('Error in on_user_speech callback: %s', e)
			# Signal exit (this will be handled by the main loop)
			return

		logger.info('✅ User speech received: "%s"', text)

		if self.on_user_speech:
			try:
				self.on_user_speech(text)
			except Exception as e:
				logger.warning('Error in on_user_speech callback: %s', e)

		# Cancel any ongoing task if user interrupts
		if self._processing and self._current_task:
			logger.info('Cancelling previous agent task due to new user input')
			self._current_task.cancel()
			try:
				await self._current_task
			except asyncio.CancelledError:
				pass

		self._processing = True

		# Run agent in background task
		self._current_task = asyncio.create_task(self._run_agent(text))

	async def _run_agent(self, query: str) -> None:
		"""Run the agent and send responses to TTS."""
		try:
			# Create narration callback that sends to TTS
			def narration_callback(narration: str) -> None:
				if narration and narration.strip():
					asyncio.create_task(self._send_to_tts(narration.strip()))

			# Create step callback for step summaries (optional)
			def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
				# Only speak narration on 'after' phase to avoid too much chatter
				if phase == 'after' and narration and narration.strip():
					# Send narration to TTS
					asyncio.create_task(self._send_to_tts(narration.strip()))

			# Temporarily set callbacks
			original_narration = self.integration.narration_callback
			original_step = self.integration.step_callback

			self.integration.narration_callback = narration_callback
			self.integration.step_callback = step_callback

			try:
				# Run the agent
				result = await self.integration.run(query)

				# Send final message to TTS
				if result.get('message'):
					await self._send_to_tts(result['message'])

				if self.on_agent_response:
					try:
						self.on_agent_response(result.get('message', ''))
					except Exception as e:
						logger.warning('Error in on_agent_response callback: %s', e)

			finally:
				# Restore original callbacks
				self.integration.narration_callback = original_narration
				self.integration.step_callback = original_step

		except asyncio.CancelledError:
			logger.info('Agent task cancelled')
			raise
		except Exception as error:
			logger.error('Error running agent: %s', error, exc_info=True)
			error_msg = f'Sorry, I encountered an error: {str(error)}'
			await self._send_to_tts(error_msg)
		finally:
			self._processing = False

	def set_tts_processor(self, processor) -> None:
		"""Set the TTS processor for sending text to speech."""
		self._tts_processor = processor

	async def _send_to_tts(self, text: str) -> None:
		"""Send text to TTS processor."""
		if not text or not text.strip():
			return
		if self._tts_processor:
			try:
				# Use the send_text method if available
				if hasattr(self._tts_processor, 'send_text'):
					await self._tts_processor.send_text(text.strip())
				else:
					# Fallback: push TextFrame directly to processor
					from pipecat.frames.frames import TextFrame
					await self._tts_processor.push_frame(TextFrame(text=text.strip()), FrameDirection.DOWNSTREAM)
			except Exception as e:
				logger.error('Error sending text to TTS: %s', e, exc_info=True)

	def is_processing(self) -> bool:
		"""Check if agent is currently processing."""
		return self._processing

