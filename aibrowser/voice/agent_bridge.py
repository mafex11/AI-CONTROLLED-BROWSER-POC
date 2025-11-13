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
			# Disable narration callback - we'll use step_callback only to avoid duplicates
			def narration_callback(narration: str) -> None:
				# Don't send narrations to TTS here - step_callback handles it
				pass

			# Track what we've already sent to avoid duplicates
			_last_tts_message = ''
			
			# Create step callback for step-by-step narration
			async def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
				"""Handle step callbacks with detailed, conversational narration."""
				nonlocal _last_tts_message
				
				# Helper function to convert tool to natural language
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
				# step_callback receives: (step, reasoning, agent_response, action, phase)
				# - reasoning: WHY (from thinking + state)
				# - agent_response: WHAT agent says (narration before, result after)
				# - action: WHAT tool is executing
				if phase == 'before':
					# Print step information to terminal
					print(f'\n{"="*60}')
					print(f'Step {step}')
					print(f'{"="*60}')
					if reasoning:
						print(f'Reasoning: {reasoning[:200]}{"..." if len(reasoning) > 200 else ""}')
					if narration:
						print(f'Agent Response: {narration}')
					if tool:
						print(f'Action: {tool}')
					print(f'{"="*60}')
					
					# Send TTS BEFORE executing action - use agent_response (what agent SAYS it will do)
					# This is the narration from LLM, which should be action-oriented, not reasoning
					message = None
					
					if narration and narration.strip():
						# Remove periods/full stops to prevent ElevenLabs from stopping mid-sentence
						# ElevenLabs may pause at periods, breaking the flow
						message = narration.strip().replace('.', '').replace('?', '').replace('!', '')
						# Add single period at the end if the original had punctuation
						if narration.strip()[-1] in '.?!':
							message += '.'
					
					# Only send if we have a meaningful message and it's different from last
					if message and message != _last_tts_message:
						# Filter out JSON/technical content
						if not (message.startswith('{') or message.startswith('[') or 'index=' in message.lower()):
							logger.info(f'🔊 Step {step} (before): {message}')
							_last_tts_message = message
							# Send TTS and wait for speech completion before allowing action to execute
							await self._send_to_tts(message)
							# Wait for speech to finish before executing the action
							if self._speech_tracker:
								try:
									logger.debug(f'Waiting for speech to complete before executing action...')
									await self._speech_tracker.wait_for_speech_completion(timeout=30.0)
									logger.debug(f'Speech completed, action can now execute')
								except Exception as e:
									logger.warning(f'Error waiting for speech completion: {e}')
									# Continue anyway - don't block if there's an error
					
					return
				elif phase == 'after':
					# Print result to terminal
					if narration:
						print(f'Agent Response: {narration}')
					if tool:
						print(f'Action Result: {tool[:200]}{"..." if len(tool) > 200 else ""}')
					print(f'{"="*60}\n')
					
					# Send TTS AFTER executing action ONLY for task completion steps
					# Regular action steps don't need after-action TTS (already spoke in "before" phase)
					if tool and 'Task completed' in tool:
						# This is a task completion step - send TTS for the final result
						logger.debug(f'🔍 Task completed detected, tool="{tool}", narration="{narration}"')
						message = None
						
						if narration and narration.strip():
							# Clean up the message for TTS
							message = narration.strip().replace('.', '').replace('?', '').replace('!', '')
							# Add single period at the end if the original had punctuation
							if narration.strip()[-1] in '.?!':
								message += '.'
						
						logger.debug(f'🔍 Processed message="{message}", last_message="{_last_tts_message}", are_equal={message == _last_tts_message if message else False}')
						
						# Only send if we have a meaningful message and it's different from last
						if message and message != _last_tts_message:
							# Filter out JSON/technical content
							if not (message.startswith('{') or message.startswith('[') or 'index=' in message.lower()):
								logger.info(f'🔊 Step {step} (after - task completed): {message}')
								# Reset last message to allow this one through (it's different content)
								# But first check if it's really different from what we said in "before" phase
								old_last_message = _last_tts_message
								_last_tts_message = message
								# Send TTS for the final result (task completion message)
								logger.debug(f'🔊 About to send TTS message: "{message}"')
								try:
									await self._send_to_tts(message)
									logger.debug(f'🔊 TTS message sent successfully, waiting for speech...')
								except Exception as e:
									logger.error(f'❌ Error sending TTS message: {e}', exc_info=True)
									# Don't re-raise - we don't want to break the agent flow
								# Wait for speech to complete so user hears the completion message
								if self._speech_tracker:
									try:
										logger.debug(f'Waiting for speech to complete after task completion...')
										await self._speech_tracker.wait_for_speech_completion(timeout=30.0)
										logger.debug(f'Speech completed after task completion')
									except Exception as e:
										logger.warning(f'Error waiting for speech completion: {e}')
										# Continue anyway - don't block if there's an error
							else:
								logger.debug(f'🔍 Message filtered out due to JSON/technical content: "{message}"')
						else:
							logger.debug(f'🔍 Message not sent: message={message is not None}, different={message != _last_tts_message if message else False}')
					else:
						logger.debug(f'🔍 Not a task completion step: tool="{tool}"')

			# Temporarily set callbacks
			original_narration = self.integration.narration_callback
			original_step = self.integration.step_callback

			self.integration.update_callbacks(
				narration_callback=narration_callback,
				step_callback=step_callback,
			)

			try:
				# Run the agent
				result = await self.integration.run(query)

				# DO NOT send final message to TTS - step_callback already handles all TTS
				# The "before" phase of step_callback already narrates for task completion
				# Final message is for terminal display only, not TTS

				if self.on_agent_response:
					try:
						self.on_agent_response(result.get('message', ''))
					except Exception as e:
						logger.warning('Error in on_agent_response callback: %s', e)

			finally:
				# Restore original callbacks
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
		"""Set the TTS processor for sending text to speech."""
		self._tts_processor = processor
	
	def set_speech_tracker(self, tracker) -> None:
		"""Set the speech tracker for waiting for speech completion."""
		self._speech_tracker = tracker

	async def _send_to_tts(self, text: str) -> None:
		"""Send text to TTS processor."""
		if not text or not text.strip():
			logger.debug('🔍 _send_to_tts: Empty text, skipping')
			return
		
		if not self._tts_processor:
			logger.warning('🔍 _send_to_tts: No TTS processor set')
			return
		
		logger.debug(f'🔍 _send_to_tts: Sending text to TTS processor: "{text[:100]}{"..." if len(text) > 100 else ""}"')
		
		try:
			# Use the send_text method if available
			if hasattr(self._tts_processor, 'send_text'):
				logger.debug(f'🔍 _send_to_tts: Using send_text method')
				await self._tts_processor.send_text(text.strip())
				logger.debug(f'🔍 _send_to_tts: send_text completed successfully')
			else:
				logger.debug(f'🔍 _send_to_tts: Using push_frame fallback')
				# Fallback: push TextFrame directly to processor
				from pipecat.frames.frames import TextFrame
				await self._tts_processor.push_frame(TextFrame(text=text.strip()), FrameDirection.DOWNSTREAM)
				logger.debug(f'🔍 _send_to_tts: push_frame completed successfully')
		except Exception as e:
			logger.error('❌ Error sending text to TTS: %s', e, exc_info=True)
			raise  # Re-raise to see the full error

	def is_processing(self) -> bool:
		"""Check if agent is currently processing."""
		return self._processing

