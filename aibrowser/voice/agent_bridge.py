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
		self._speech_tracker = None

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
				self.on_user_speech(text)
			except Exception as e:
				logger.warning('Error in on_user_speech callback: %s', e)

		if self._processing and self._current_task:
			logger.info('Cancelling previous agent task due to new user input')
			self._current_task.cancel()
			try:
				await self._current_task
			except asyncio.CancelledError:
				pass

		self._processing = True
		self._current_task = asyncio.create_task(self._run_agent(text))

	async def _run_agent(self, query: str) -> None:
		"""Run the agent and send responses to TTS."""
		try:
			def narration_callback(narration: str) -> None:
				pass

			_last_tts_message = ''
			_step_counter = 0
			
			async def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
				"""Handle step callbacks with detailed, conversational narration."""
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
							await self._send_to_tts(message)
							if self._speech_tracker:
								try:
									logger.debug(f'Waiting for speech to complete before executing action...')
									await self._speech_tracker.wait_for_speech_completion(timeout=30.0)
									logger.debug(f'Speech completed, action can now execute')
								except Exception as e:
									logger.warning(f'Error waiting for speech completion: {e}')
					
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
									await self._send_to_tts(message)
									logger.debug(f'TTS message sent successfully, waiting for speech...')
								except Exception as e:
									logger.error(f'Error sending TTS message: {e}', exc_info=True)
								if self._speech_tracker:
									try:
										logger.debug(f'Waiting for speech to complete after task completion...')
										await self._speech_tracker.wait_for_speech_completion(timeout=30.0)
										logger.debug(f'Speech completed after task completion')
									except Exception as e:
										logger.warning(f'Error waiting for speech completion: {e}')
							else:
								logger.debug(f'Message filtered out due to JSON/technical content: "{message}"')
						else:
							logger.debug(f'Message not sent: message={message is not None}, different={message != _last_tts_message if message else False}')
					else:
						logger.debug(f'Not a task completion step: tool="{tool}"')

			original_narration = self.integration.narration_callback
			original_step = self.integration.step_callback

			self.integration.update_callbacks(
				narration_callback=narration_callback,
				step_callback=step_callback,
			)

			try:
				result = await self.integration.run(query)

				if self.on_agent_response:
					try:
						self.on_agent_response(result.get('message', ''))
					except Exception as e:
						logger.warning('Error in on_agent_response callback: %s', e)

			finally:
				self.integration.update_callbacks(
					narration_callback=original_narration,
					step_callback=original_step,
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
		if not text or not text.strip():
			logger.debug('_send_to_tts: Empty text, skipping')
			return
		
		if not self._tts_processor:
			logger.warning('_send_to_tts: No TTS processor set')
			return
		
		logger.debug(f'_send_to_tts: Sending text to TTS processor: "{text[:100]}{"..." if len(text) > 100 else ""}"')
		
		try:
			if hasattr(self._tts_processor, 'send_text'):
				await self._tts_processor.send_text(text.strip())
			else:
				from pipecat.frames.frames import TextFrame
				await self._tts_processor.push_frame(TextFrame(text=text.strip()), FrameDirection.DOWNSTREAM)
		except Exception as e:
			logger.error('Error sending text to TTS: %s', e, exc_info=True)
			raise

	def is_processing(self) -> bool:
		return self._processing

