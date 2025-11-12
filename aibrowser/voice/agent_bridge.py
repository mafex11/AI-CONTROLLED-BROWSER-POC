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

			# Create step callback for step-by-step narration
			def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
				"""Handle step callbacks with detailed, conversational narration."""
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
				
				# Helper function to join parts naturally
				def join_naturally(parts_list: list[str]) -> str:
					parts_list = [p.strip() for p in parts_list if p and p.strip()]
					if not parts_list:
						return ''
					if len(parts_list) == 1:
						return parts_list[0]
					if len(parts_list) == 2:
						return f"{parts_list[0]}. {parts_list[1]}"
					all_but_last = ', '.join(parts_list[:-1])
					return f"{all_but_last}, and {parts_list[-1]}"
				
				if phase == 'before':
					# Before executing: explain what we're about to do in natural language
					# Prioritize narration (agent's natural explanation) over technical reasoning
					message = None
					
					# First, use narration if available (this is the agent's natural explanation)
					if narration and narration.strip():
						narration_clean = narration.strip()
						# Make it sound more conversational if it doesn't already
						if not narration_clean.lower().startswith(('let me', 'i\'ll', 'i will', 'i\'m going to', 'i need to')):
							message = f"Let me {narration_clean.lower()}"
						else:
							message = narration_clean
					
					# If no narration, convert tool to natural high-level action
					if not message and tool and tool.strip() and tool not in {'Task completed', 'Awaiting user input'}:
						tool_natural = tool_to_natural(tool)
						if tool_natural:
							# Make it conversational
							if tool_natural.startswith('searching'):
								# Extract query for better narration
								try:
									if 'for \'' in tool_natural:
										query = tool_natural.split('for \'')[1].split('\'')[0]
										message = f"Let me search for {query}"
									else:
										message = "Let me search"
								except:
									message = "Let me search"
							elif tool_natural.startswith('navigating'):
								# Extract domain for better narration
								if 'to ' in tool_natural:
									domain = tool_natural.split('to ')[1]
									message = f"Let me navigate to {domain}"
								else:
									message = "Let me navigate to the page"
							elif tool_natural.startswith('clicking'):
								message = "Let me click on that"
							elif tool_natural.startswith('typing'):
								message = "Let me type that in"
							elif tool_natural.startswith('scrolling'):
								message = "Let me scroll down"
							else:
								message = f"Let me {tool_natural}"
					
					# Fallback to reasoning if nothing else available (but make it less technical)
					if not message and reasoning and reasoning.strip():
						reasoning_clean = reasoning.strip()
						# Remove technical details
						if 'element' in reasoning_clean.lower() or 'index=' in reasoning_clean.lower():
							# Skip overly technical reasoning
							message = "Let me proceed with the next step"
						else:
							reasoning_clean = reasoning_clean.replace('Step ', '').strip()
							message = f"Let me {reasoning_clean.lower()}"
					
					if message:
						logger.info(f'🔊 Step {step} (before): {message}')
						asyncio.create_task(self._send_to_tts(message))
				
				elif phase == 'after':
					# After executing: explain what happened
					parts = []
					
					# Add narration about what happened
					if narration and narration.strip():
						parts.append(narration.strip())
					
					# Add tool result if available and meaningful
					if tool and tool.strip():
						if '→' in tool:
							# Tool result format: "tool → result"
							tool_part, result_part = tool.split('→', 1)
							tool_part = tool_part.strip()
							result_part = result_part.strip()
							
							tool_natural = tool_to_natural(tool_part)
							if tool_natural and result_part:
								if len(result_part) > 100:
									result_part = result_part[:100] + '...'
								parts.append(f"{tool_natural}, and {result_part}")
						elif tool not in {'Task completed', 'Awaiting user input'}:
							tool_natural = tool_to_natural(tool)
							if tool_natural:
								parts.append(f"Finished {tool_natural}")
					
					if parts:
						message = join_naturally(parts)
						logger.info(f'🔊 Step {step} (after): {message}')
						asyncio.create_task(self._send_to_tts(message))

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

