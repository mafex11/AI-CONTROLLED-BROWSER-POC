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
	"""Bridges Pipecat text frames to browser agent."""

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
		self._speech_tracker = None
		self._awaiting_user_input = False
		self._tts_queue: asyncio.Queue = asyncio.Queue()
		self._tts_processing = False
		self._tts_task: Optional[asyncio.Task] = None

	async def process_user_text(self, text: str) -> None:
		"""Process user speech transcript and run agent."""
		logger.debug('AgentBridge.process_user_text called with: "%s"', text)
		if not text or not text.strip():
			logger.warning('AgentBridge: Empty text received, ignoring')
			return

		text = text.strip()
		text_lower = text.lower()
		logger.debug('Processing user speech: "%s"', text)
		
		if text_lower in {'exit', 'quit', 'stop', 'goodbye'}:
			logger.info('User requested exit')
			if self.on_user_speech:
				try:
					self.on_user_speech('exit')
				except Exception as e:
					logger.warning('Error in on_user_speech callback: %s', e)
			return

		logger.debug('User speech received: "%s"', text)

		if self.on_user_speech:
			try:
				if asyncio.iscoroutinefunction(self.on_user_speech):
					await self.on_user_speech(text)
				else:
					self.on_user_speech(text)
			except Exception as e:
				logger.warning('Error in on_user_speech callback: %s', e)

		# Check if we're continuing a conversation after await_user_input
		is_continuation = self._awaiting_user_input
		
		if self._processing and self._current_task:
			if is_continuation:
				logger.info('Continuing conversation after user input: %s', text)
				# Wait for current task to finish (it should have already returned with awaiting_user_input=True)
				try:
					await self._current_task
				except Exception as e:
					logger.warning('Error waiting for previous task: %s', e)
			else:
				logger.info('Cancelling previous agent task due to new user input')
				self._current_task.cancel()
				try:
					await self._current_task
				except asyncio.CancelledError:
					pass

		self._processing = True
		self._awaiting_user_input = False  # Reset flag when processing new input
		self._current_task = asyncio.create_task(self._run_agent(text, is_continuation=is_continuation))

	async def _run_agent(self, query: str, *, is_continuation: bool = False) -> None:
		"""Run agent and send responses to TTS."""
		try:
			def narration_callback(narration: str) -> None:
				pass

			_last_tts_message = ''
			_step_counter = 0
			
			async def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
				"""Handle step callbacks with conversational narration."""
				nonlocal _last_tts_message, _step_counter, query
				
				def tool_to_natural(t: str) -> str:
					if not t or not t.strip():
						return ''
					t_lower = t.lower().strip()
					if t_lower.startswith('search('):
						try:
							query_start = t_lower.find("query='") + 7
							query_end = t_lower.find("'", query_start)
							if query_end > query_start:
								query = t_lower[query_start:query_end]
								return f"searching for '{query}'"
						except:
							pass
						return "searching"
					elif t_lower.startswith('navigate('):
						try:
							url_start = t_lower.find("url='") + 5
							url_end = t_lower.find("'", url_start)
							if url_end > url_start:
								url = t_lower[url_start:url_end]
								if '//' in url:
									domain = url.split('//')[-1].split('/')[0]
									return f"navigating to {domain}"
						except:
							pass
						return "navigating to a webpage"
					elif t_lower.startswith('click('):
						if 'index=' in t_lower:
							try:
								idx_start = t_lower.find("index=") + 6
								idx_end = t_lower.find(',', idx_start)
								if idx_end == -1:
									idx_end = t_lower.find(')', idx_start)
								if idx_end > idx_start:
									idx = t_lower[idx_start:idx_end].strip()
									return f"clicking on element {idx}"
							except:
								pass
						return "clicking on the page"
					elif t_lower.startswith('input('):
						try:
							text_start = t_lower.find("text='") + 6
							text_end = t_lower.find("'", text_start)
							if text_end > text_start:
								text = t_lower[text_start:text_end]
								text_preview = text[:20] + '...' if len(text) > 20 else text
								return f"typing '{text_preview}'"
						except:
							pass
						return "typing text"
					elif t_lower.startswith('scroll('):
						if 'direction=' in t_lower:
							try:
								dir_start = t_lower.find("direction='") + 11
								dir_end = t_lower.find("'", dir_start)
								if dir_end > dir_start:
									direction = t_lower[dir_start:dir_end]
									return f"scrolling {direction}"
							except:
								pass
						return "scrolling the page"
					elif t_lower.startswith('send_keys('):
						return "pressing keys"
					elif t_lower.startswith('screenshot('):
						return "taking a screenshot"
					return t_lower.replace('_', ' ').replace('()', '')
				
				# Print step information to terminal
				if phase == 'before':
					_step_counter += 1
					print(f'\n{"-"*70}')
					print(f'Step {step}')
					print(f'{"-"*70}')
					
					if reasoning and reasoning.strip():
						reasoning_display = reasoning[:300] + '...' if len(reasoning) > 300 else reasoning
						print(f'Reasoning: {reasoning_display}')
					else:
						print('Reasoning: (analyzing current state)')
					
					if narration and narration.strip():
						print(f'Response (before action): {narration}')
					else:
						print('Response (before action): (preparing to act)')
					
					if tool and tool.strip():
						print(f'Action/Tool: {tool}')
					else:
						print('Action/Tool: (none)')
					
					message = None
					
					if narration and narration.strip():
						message = narration.strip().replace('.', '').replace('?', '').replace('!', '')
						if narration.strip()[-1] in '.?!':
							message += '.'
					
					if message and message != _last_tts_message:
						if not (message.startswith('{') or message.startswith('[') or 'index=' in message.lower()):
							logger.debug(f'Step {step} (before): {message}')
							_last_tts_message = message
							# Send TTS async - don't wait for it to complete
							# Audio is streamed to frontend, so we can't track local completion
							# Execute action immediately instead of waiting
							asyncio.create_task(self._send_to_tts(message))
					
					return
				elif phase == 'after':
					if narration and narration.strip():
						print(f'Response (after action): {narration}')
					
					if tool and tool.strip():
						if ' → ' in tool:
							result_part = tool.split(' → ', 1)[1]
							result_display = result_part[:200] + '...' if len(result_part) > 200 else result_part
							print(f'Action Result: {result_display}')
						else:
							result_display = tool[:200] + '...' if len(tool) > 200 else tool
							print(f'Action Result: {result_display}')
					
					print(f'{"-"*70}')
					
					if tool and 'Task completed' in tool:
						if _step_counter <= 1:
							logger.debug(
								'Task completed but only one step detected; skipping after-phase TTS to keep single-step responses brief'
							)
							return
						logger.debug(f'Task completed detected, tool="{tool}", narration="{narration}"')
						message = None
						
						if narration and narration.strip():
							message = narration.strip().replace('.', '').replace('?', '').replace('!', '')
							if narration.strip()[-1] in '.?!':
								message += '.'
						
						logger.debug(f'Processed message="{message}", last_message="{_last_tts_message}", are_equal={message == _last_tts_message if message else False}')
						
						if message and message != _last_tts_message:
							if not (message.startswith('{') or message.startswith('[') or 'index=' in message.lower()):
								logger.debug(f'Step {step} (after - task completed): {message}')
								old_last_message = _last_tts_message
								_last_tts_message = message
								logger.debug(f'About to send TTS message: "{message}"')
								try:
									# Send TTS async - don't wait for it to complete
									# Audio is streamed to frontend, so we can't track local completion
									asyncio.create_task(self._send_to_tts(message))
									logger.debug(f'TTS message sent successfully')
								except Exception as e:
									logger.error(f'Error sending TTS message: {e}', exc_info=True)
							else:
								logger.debug(f'Message filtered out due to JSON/technical content: "{message}"')
						else:
							logger.debug(f'Message not sent: message={message is not None}, different={message != _last_tts_message if message else False}')
					else:
						logger.debug(f'Not a task completion step: tool="{tool}"')

			original_narration = self.integration.narration_callback
			original_step = self.integration.step_callback

			# Create a chained step callback that calls both original and TTS callback
			async def chained_step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
				# First call the original step callback (for WebSocket updates)
				if original_step:
					try:
						if asyncio.iscoroutinefunction(original_step):
							await original_step(step, reasoning, narration, tool, phase)
						else:
							original_step(step, reasoning, narration, tool, phase)
					except Exception as e:
						logger.debug('Error in original step callback: %s', e)
				
				# Then call the TTS step callback
				await step_callback(step, reasoning, narration, tool, phase)

			self.integration.update_callbacks(
				narration_callback=narration_callback,
				step_callback=chained_step_callback,
			)

			try:
				result = await self.integration.run(query, is_continuation=is_continuation)
				
				# Track if agent is awaiting user input
				self._awaiting_user_input = result.get('awaiting_user_input', False)

				if self.on_agent_response:
					try:
						if asyncio.iscoroutinefunction(self.on_agent_response):
							await self.on_agent_response(result.get('message', ''))
						else:
							self.on_agent_response(result.get('message', ''))
					except Exception as e:
						logger.warning('Error in on_agent_response callback: %s', e)

			finally:
				# Restore both callbacks to their original state
				# This ensures we don't create a chain of chains (which causes duplicate TTS)
				# The step_callback will be re-chained on the next agent run
				self.integration.update_callbacks(
					narration_callback=original_narration,
					step_callback=original_step,  # Restore original (WebSocket) callback
				)

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
		self._tts_processor = processor
	
	def set_speech_tracker(self, tracker) -> None:
		self._speech_tracker = tracker

	async def _send_to_tts(self, text: str) -> None:
		"""Queue text for TTS processing."""
		if not text or not text.strip():
			logger.debug('_send_to_tts: Empty text, skipping')
			return
		
		if not self._tts_processor:
			logger.warning('_send_to_tts: No TTS processor set')
			return
		
		logger.debug(f'_send_to_tts: Queuing text for TTS: "{text[:100]}{"..." if len(text) > 100 else ""}"')
		
		# Queue the text for sequential processing
		await self._tts_queue.put(text.strip())
		
		# Start TTS processing task if not already running
		if not self._tts_processing:
			self._tts_processing = True
			if self._tts_task and not self._tts_task.done():
				self._tts_task.cancel()
			self._tts_task = asyncio.create_task(self._process_tts_queue())
	
	async def _process_tts_queue(self) -> None:
		"""Process TTS queue sequentially."""
		try:
			while True:
				try:
					# Get next text from queue (with timeout to allow cancellation)
					text = await asyncio.wait_for(self._tts_queue.get(), timeout=1.0)
					
					logger.debug(f'_process_tts_queue: Processing TTS: "{text[:100]}{"..." if len(text) > 100 else ""}"')
					
					try:
						if hasattr(self._tts_processor, 'send_text'):
							await self._tts_processor.send_text(text)
						else:
							from pipecat.frames.frames import TextFrame
							await self._tts_processor.push_frame(TextFrame(text=text), FrameDirection.DOWNSTREAM)
						
						logger.debug(f'_process_tts_queue: TTS sent successfully')
					except Exception as e:
						logger.error('Error sending text to TTS: %s', e, exc_info=True)
					
					# Mark task as done
					self._tts_queue.task_done()
					
				except asyncio.TimeoutError:
					# Check if queue is empty and we should stop
					if self._tts_queue.empty():
						break
					continue
		except asyncio.CancelledError:
			logger.debug('TTS queue processing cancelled')
			raise
		finally:
			self._tts_processing = False

	def is_processing(self) -> bool:
		return self._processing

