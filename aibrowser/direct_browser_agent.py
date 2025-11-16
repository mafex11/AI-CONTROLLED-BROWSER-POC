"""Custom agent loop that keeps direct control over browser-use tools."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Union

from browser_use.llm.messages import AssistantMessage, BaseMessage, SystemMessage, UserMessage

from .browser_controller import BrowserController
from .config import Config
from .structured_output import StructuredAgentResponse, extract_narrations, parse_structured_response
from .structured_prompt import AnswerPromptBuilder, ObservationPromptBuilder, StructuredPromptBuilder

logger = logging.getLogger(__name__)


@dataclass
class AgentRunConfig:
	max_steps: int = 25
	search_engine: str = 'google'
	max_missing_action_retries: int = 5
	step_timeout: float = 180.0  # Timeout in seconds for each step (prevents getting stuck)


@dataclass
class AgentRunResult:
	success: bool
	message: str
	structured_message: str
	final_state: Any | None
	context_log: List[str]
	awaiting_user_input: bool = False


class DirectBrowserAgent:
	"""Agent that chooses browser-use actions based on structured prompts."""

	def __init__(
		self,
		controller: BrowserController,
		llm,
		*,
		system_prompt_builder: StructuredPromptBuilder,
		observation_builder: ObservationPromptBuilder,
		answer_builder: AnswerPromptBuilder,
		config: AgentRunConfig,
		narration_callback: Callable[[str], None] | None = None,
		step_callback: Callable[[int, str, str, str, str], None] | Callable[[int, str, str, str, str], Coroutine[Any, Any, None]] | None = None,
	) -> None:
		self.controller = controller
		self.llm = llm
		self.system_prompt_builder = system_prompt_builder
		self.observation_builder = observation_builder
		self.answer_builder = answer_builder
		self.config = config
		self.narration_callback = narration_callback
		self.step_callback = step_callback

		self._conversation: List[BaseMessage] = []
		self._context_log: List[str] = []
		self._max_conversation_history: int = 50
		self._previous_step_element_error: bool = False  # Track if previous step had element error
		self._last_highlight_screenshot_base64: str | None = None  # Store last highlight screenshot for step callback

	def _check_step_timeout(self, step: int, step_start_time: float) -> bool:
		"""Check if step has exceeded timeout."""
		step_elapsed = time.time() - step_start_time
		if step_elapsed > self.config.step_timeout:
			logger.warning(
				'Step %d exceeded timeout of %.1fs (took %.1fs). Step may be stuck.',
				step,
				self.config.step_timeout,
				step_elapsed,
			)
			return True
		return False

	async def run(self, task: str, *, is_continuation: bool = False) -> AgentRunResult:
		logger.debug('Starting agent task: %s (continuation: %s)', task, is_continuation)
		system_prompt = self.system_prompt_builder.build()
		
		if is_continuation:
			# This is a continuation after await_user_input - don't clear conversation
			logger.debug('Continuing conversation after user input: %s', task[:100])
			# Add user's response to conversation
			user_response = UserMessage(content=task)
			self._conversation.append(user_response)
		else:
			# New task - clear context log and conversation
			self._context_log.clear()
			if self._conversation:
				last_message = self._conversation[-1]
				if isinstance(last_message, AssistantMessage):
					logger.debug('Previous conversation exists, clearing to start fresh with new task: %s', task[:100])
					self._conversation.clear()
				elif isinstance(last_message, UserMessage):
					logger.debug('Last message was user message, clearing conversation for new task')
					self._conversation.clear()
			else:
				logger.debug('Starting new conversation session')

		# Use cached state if available to avoid blocking DOM fetch on task start
		used_cached_state = False
		state = self.controller.last_state
		if state is None:
			try:
				logger.debug('No cached state, fetching initial browser state...')
				state = await self.controller.refresh_state(include_dom=True, include_screenshot=False)
				logger.debug('Initial browser state retrieved (URL: %s)', state.url if state else 'unknown')
			except Exception as error:  # noqa: BLE001
				logger.error('Failed to get initial browser state: %s', error, exc_info=True)
				return AgentRunResult(
					success=False,
					awaiting_user_input=False,
					message=f'Browser connection error: {error}',
					structured_message='',
					final_state=None,
					context_log=[],
				)
		else:
			used_cached_state = True
			logger.debug('Using cached browser state (URL: %s) - will refresh in first step if needed', state.url if state else 'unknown')

		for step in range(1, self.config.max_steps + 1):
			logger.debug('Agent step %d/%d', step, self.config.max_steps)
			step_start_time = time.time()
			
			try:
				# Refresh state at the start of each step to ensure state matches cached selector_map
				if step == 1 and used_cached_state:
					logger.debug('Refreshing browser state for first step (was using cached state)...')
					try:
						state = await self.controller.refresh_state(include_dom=True, include_screenshot=False)
					except Exception as error:  # noqa: BLE001
						logger.warning('Failed to refresh state in first step, using cached: %s', error)
						state = self.controller.last_state or state
				elif step > 1:
					# Refresh state at start of each step to ensure indices are current
					await asyncio.sleep(0.1)
					logger.debug('Refreshing browser state at start of step %d...', step)
					try:
						# Add timeout to prevent hanging on state refresh
						state = await asyncio.wait_for(
							self.controller.refresh_state(include_dom=True, include_screenshot=False),
							timeout=30.0
						)
					except asyncio.TimeoutError:
						logger.error('State refresh timed out at start of step %d', step)
						state = self.controller.last_state or state
					except Exception as error:  # noqa: BLE001
						logger.warning('Failed to refresh state at start of step %d: %s', step, error)
						state = self.controller.last_state or state
				
				tab_summary = self._format_tab_summary(state)
				context_lines = '\n'.join(self._context_log[-6:])
				
				# If previous step had element error, add explicit retry instruction
				if self._previous_step_element_error and step > 1:
					retry_instruction = '\n\nIMPORTANT: The previous action failed because an element was not found (page may have changed). The DOM has been refreshed with current element indices. You need to RETRY the failed action using the current page state. Do NOT claim the action succeeded - it failed and needs to be retried.'
					context_lines = retry_instruction + '\n\nRecent context:\n' + context_lines
					self._previous_step_element_error = False  # Reset after using
				
				if is_continuation and step == 1:
					# For continuation, provide context that user has responded
					# Include their response in the task context so agent understands what they said
					continuation_context = f'The user has responded: "{task}". Interpret their response in the context of your previous question. If they said they want to "review" or "check" something, acknowledge this and wait - do NOT repeat actions or fill forms again. If they said "yes" or "go ahead", proceed with the action you asked about.'
					observation = self.observation_builder.build(
						task=continuation_context,
						tab_summary=tab_summary,
						extra_context=context_lines,
					)
				else:
					observation = self.observation_builder.build(
						task=task,
						tab_summary=tab_summary,
						extra_context=context_lines,
					)

				messages: List[BaseMessage] = [SystemMessage(content=system_prompt), *self._conversation]
				# Add observation as user message (for continuation, user response is already in conversation)
				user_message = UserMessage(content=observation)
				messages.append(user_message)
				
				current_observation = tab_summary
			except Exception as error:  # noqa: BLE001
				logger.error('Error preparing step %d: %s', step, error, exc_info=True)
				return AgentRunResult(
					success=False,
					awaiting_user_input=False,
					message=f'Error preparing step: {error}',
					structured_message='',
					final_state=state,
					context_log=list(self._context_log),
				)

			# Check timeout before LLM call
			if self._check_step_timeout(step, step_start_time):
				self._context_log.append(f'Step {step} timeout detected before LLM call, refreshing state and continuing')
				self._context_log = self._context_log[-20:]
				# Refresh state and continue to next step
				try:
					state = await asyncio.wait_for(
						self.controller.refresh_state(include_dom=True, include_screenshot=True),
						timeout=30.0
					)
				except Exception:  # noqa: BLE001
					state = self.controller.last_state or state
				continue

			try:
				logger.debug('Invoking LLM at step %d (conversation length: %d messages)...', step, len(messages))
				response_text = None
				last_error = None
				max_retries = 3
				for retry_attempt in range(max_retries):
					# Check timeout during retries
					if self._check_step_timeout(step, step_start_time):
						logger.warning('Step %d timeout during LLM retries, breaking retry loop', step)
						# If timeout and no response, refresh state and continue to next step
						if response_text is None:
							self._context_log.append(f'Step {step} timeout during LLM call, refreshing state and continuing')
							self._context_log = self._context_log[-20:]
							try:
								state = await asyncio.wait_for(
									self.controller.refresh_state(include_dom=True, include_screenshot=True),
									timeout=30.0
								)
							except Exception:  # noqa: BLE001
								state = self.controller.last_state or state
							continue  # Continue to next step
						break  # We have a response, continue processing
					try:
						logger.debug('LLM call attempt %d/%d at step %d...', retry_attempt + 1, max_retries, step)
						response = await asyncio.wait_for(self.llm.ainvoke(messages), timeout=60.0)
						response_text = response.completion if hasattr(response, 'completion') else str(response)
						logger.debug('LLM response received at step %d (length: %d chars)', step, len(response_text) if response_text else 0)
						break
					except Exception as error:  # noqa: BLE001
						last_error = error
						error_str = str(error)
						if '503' in error_str or 'overloaded' in error_str.lower() or 'UNAVAILABLE' in error_str:
							if retry_attempt < max_retries - 1:
								wait_time = 2.0 * (2 ** retry_attempt)
								logger.warning(
									'Gemini API overloaded (503), waiting %ds before retry %d/%d',
									wait_time,
									retry_attempt + 1,
									max_retries,
								)
								await asyncio.sleep(wait_time)
								continue
						break
				
				if response_text is None:
					# Check if we should continue due to timeout
					if self._check_step_timeout(step, step_start_time):
						self._context_log.append(f'Step {step} timeout after LLM call failure, refreshing state and continuing')
						self._context_log = self._context_log[-20:]
						try:
							state = await asyncio.wait_for(
								self.controller.refresh_state(include_dom=True, include_screenshot=True),
								timeout=30.0
							)
						except Exception:  # noqa: BLE001
							state = self.controller.last_state or state
						continue  # Continue to next step
					raise last_error if last_error else Exception('LLM invocation failed')
					
			except asyncio.TimeoutError:
				logger.error('LLM invocation timed out after 60 seconds')
				return AgentRunResult(
					success=False,
					awaiting_user_input=False,
					message='The language model took too long to respond. Please try again.',
					structured_message='',
					final_state=state,
					context_log=list(self._context_log),
				)
			except Exception as error:  # noqa: BLE001
				logger.error('LLM invocation failed: %s', error, exc_info=True)
				error_msg = str(error)
				if '503' in error_msg or 'overloaded' in error_msg.lower():
					user_message = 'The Gemini API is currently overloaded. Please wait a moment and try again.'
				else:
					user_message = f'Failed to contact the language model: {error}'
				return AgentRunResult(
					success=False,
					awaiting_user_input=False,
					message=user_message,
					structured_message='',
					final_state=state,
					context_log=list(self._context_log),
				)

			if not response_text or not response_text.strip():
				logger.warning('Empty response from LLM at step %d', step)
				self._context_log.append('LLM returned empty response')
				self._context_log = self._context_log[-20:]
				continue

			logger.debug('LLM response (first 200 chars): %s', response_text[:200])
			assistant_message = AssistantMessage(content=response_text)
			
			if step == 1 and not is_continuation:
				# Only add initial task to conversation if it's a new task
				self._conversation.append(user_message)
				logger.debug('Added user task to conversation history: %s', task[:100])
			
			self._conversation.append(assistant_message)
			
			if len(self._conversation) > self._max_conversation_history:
				keep_recent = self._max_conversation_history // 2
				keep_early = self._max_conversation_history - keep_recent
				self._conversation = (
					self._conversation[:keep_early] + 
					self._conversation[-keep_recent:]
				)
				logger.debug(
					'Trimmed conversation history to %d messages (kept %d early + %d recent)',
					len(self._conversation),
					keep_early,
					keep_recent,
				)

			try:
				structured = parse_structured_response(response_text)
			except Exception as error:  # noqa: BLE001
				logger.warning('Could not parse structured response: %s', error)
				logger.debug('Full response text: %s', response_text)
				self._context_log.append(f'Parser error: {error}')
				self._context_log = self._context_log[-20:]
				continue

			self._publish_narrations(structured)

			action_payload = self._interpret_action(structured)
			if action_payload is None:
				self._context_log.append('Model reply lacked a valid Action JSON object.')
				self._context_log = self._context_log[-20:]
				continue

			action_type = action_payload.get('type', '').lower()
			
			reasoning_text = self._extract_reasoning_from_state(current_observation, structured, state=state)
			tool_info = self._format_tool_info(action_type, action_payload)
			
			if action_type in {'none', 'done', 'await_user_input', 'awaiting_user_input'}:
				# For await_user_input, prioritize narration (conversational response) over results
				if action_type in {'await_user_input', 'awaiting_user_input'}:
					agent_response_text = structured.narration[-1] if structured.narration else ''
					if not agent_response_text:
						agent_response_text = structured.results[-1] if structured.results else ''
					if not agent_response_text:
						agent_response_text = self._select_final_message(structured)
					if not agent_response_text:
						agent_response_text = 'I need your input to continue.'
				else:
					agent_response_text = self._select_final_message(structured)
					if not agent_response_text:
						agent_response_text = structured.results[-1] if structured.results else ''
						if not agent_response_text:
							agent_response_text = structured.narration[-1] if structured.narration else 'Task completed.'
			else:
				agent_response_text = structured.narration[-1] if structured.narration else ''
				if not agent_response_text:
					agent_response_text = structured.results[-1] if structured.results else ''
				if not agent_response_text:
					agent_response_text = 'Preparing to execute action...'
			
			# Take screenshot BEFORE step callback for click/input actions (if needed)
			# Note: Preview highlights are disabled - only action-time highlights will show
			# Action-time highlights are controlled by HIGHLIGHT_ELEMENTS in BrowserProfile
			# Skip if highlighting is disabled globally
			if action_type in {'click', 'input'} and Config.HIGHLIGHT_ELEMENTS:
				await self._preview_and_capture_highlight(action_type, action_payload, step)
			
			if self.step_callback and agent_response_text and tool_info:
				try:
					result = self.step_callback(step, reasoning_text, agent_response_text, tool_info, 'before')
					if asyncio.iscoroutine(result):
						await result
				except Exception as callback_error:  # noqa: BLE001
					logger.warning('Step callback error: %s', callback_error)
			
			# Clear the stored screenshot after step callback (so it's not reused)
			self._last_highlight_screenshot_base64 = None
			
			if action_type in {'none', 'done'}:
				final_text = self._select_final_message(structured)
				full = self._format_structured(structured)
				return AgentRunResult(
					success=True,
					awaiting_user_input=False,
					message=final_text,
					structured_message=full,
					final_state=state,
					context_log=list(self._context_log),
				)

			if action_type in {'await_user_input', 'awaiting_user_input'}:
				# Prioritize narration for conversational responses
				final_text = structured.narration[-1] if structured.narration else ''
				if not final_text:
					final_text = structured.results[-1] if structured.results else ''
				if not final_text:
					final_text = self._select_final_message(structured)
				if not final_text:
					final_text = 'I need your input to continue.'
				full = self._format_structured(structured)
				return AgentRunResult(
					success=False,
					awaiting_user_input=True,
					message=final_text,
					structured_message=full,
					final_state=state,
					context_log=list(self._context_log),
				)

			# Check timeout before action execution
			if self._check_step_timeout(step, step_start_time):
				self._context_log.append(f'Step {step} timeout detected before action execution, refreshing state and continuing')
				self._context_log = self._context_log[-20:]
				# Refresh state and continue to next step
				try:
					state = await asyncio.wait_for(
						self.controller.refresh_state(include_dom=True, include_screenshot=True),
						timeout=30.0
					)
				except Exception:  # noqa: BLE001
					state = self.controller.last_state or state
				continue

			# Execute action using the state that was shown to the LLM
			# browser-use Tools will use the cached selector_map which matches the indices
			# the LLM saw. Do NOT refresh state here as it would create new backend_node_ids
			# and break the element lookup.
			logger.debug('Executing action %s at step %d...', action_type, step)
			
			try:
				# Add timeout to prevent hanging on action execution
				result_str = await asyncio.wait_for(
					self._execute_action(action_type, action_payload),
					timeout=45.0
				)
				logger.debug('Action %s completed at step %d', action_type, step)
			except asyncio.TimeoutError:
				logger.error('Action %s timed out after 45 seconds at step %d', action_type, step)
				result_str = f'Action {action_type} timed out after 45 seconds'
			except Exception as error:  # noqa: BLE001
				logger.error('Action execution failed at step %d: %s', step, error, exc_info=True)
				result_str = f'Action {action_type} failed: {error}'
			
			if result_str is None:
				result_str = f'Action {action_type} failed: unknown error'
			
			# Check if this is an element error - if so, refresh DOM immediately before continuing
			element_changed = self._is_element_error(result_str)
			if element_changed:
				logger.warning('Element error detected at step %d, immediately refreshing DOM to get current element indices...', step)
				self._context_log.append('Element error detected - page structure changed, refreshing DOM immediately')
				self._context_log = self._context_log[-20:]
				
				# Set flag so next step knows to retry
				self._previous_step_element_error = True
				
				# Immediately refresh state with screenshot
				await asyncio.sleep(0.2)  # Brief wait for page to stabilize
				try:
					logger.info('Refreshing browser state after element error (step %d)...', step)
					state = await asyncio.wait_for(
						self.controller.refresh_state(include_dom=True, include_screenshot=True),
						timeout=30.0
					)
					if state:
						logger.info('DOM refreshed successfully after element error (step %d, URL: %s)', step, state.url if state else 'unknown')
					else:
						logger.warning('DOM refresh returned None state after element error (step %d)', step)
					await asyncio.sleep(0.1)  # Small delay after refresh
				except asyncio.TimeoutError:
					logger.error('DOM refresh timed out after element error at step %d', step)
					state = self.controller.last_state or state
				except Exception as refresh_error:  # noqa: BLE001
					logger.error('Failed to refresh state after element error at step %d: %s', step, refresh_error, exc_info=True)
					state = self.controller.last_state or state
			else:
				# Reset flag if no element error
				self._previous_step_element_error = False
			
			self._context_log.append(result_str)
			self._context_log = self._context_log[-20:]
			
			# Check if action failed - if so, refresh state immediately to get accurate page info
			action_failed = (
				'failed' in result_str.lower() or 
				'not available' in result_str.lower() or 
				'Error:' in result_str or 
				'timed out' in result_str.lower()
			)
			
			# Note: element_changed was already checked and DOM refreshed above if needed
			
			# Call step callback AFTER execution (shows result)
			# Use Result as agent response (what agent says happened)
			# But if action failed, don't use the optimistic narration - use the actual result
			action_succeeded = not (
				'failed' in result_str.lower() or 
				'not available' in result_str.lower() or 
				'Error:' in result_str or 
				'timed out' in result_str.lower()
			)
			
			if action_succeeded:
				after_response = structured.results[-1] if structured.results else agent_response_text
			else:
				# Action failed - use error message, don't claim success
				if element_changed:
					after_response = f'Element not found - page may have changed. Refreshed DOM to get current elements.'
				else:
					after_response = result_str if result_str else agent_response_text
			
			if not after_response:
				after_response = f'{tool_info} → {result_str[:50]}'
			if self.step_callback:
				try:
					result_summary = result_str[:100] + '...' if len(result_str) > 100 else result_str
					callback_result = self.step_callback(step, reasoning_text, after_response, f'{tool_info} → {result_summary}', 'after')
					if asyncio.iscoroutine(callback_result):
						await callback_result
				except Exception as callback_error:  # noqa: BLE001
					logger.warning('Step callback error (after): %s', callback_error)

			# If element error was already handled above, skip the normal refresh
			# Otherwise, refresh state normally
			if not element_changed:
				# If action failed, refresh state immediately with minimal wait
				# Otherwise, wait a bit for page to stabilize
				if action_failed:
					wait_time = 0.1
					logger.debug('Action failed, refreshing state immediately (waiting %.1fs)...', wait_time)
				else:
					wait_time = 0.2
					logger.debug('Waiting %.1fs for page to stabilize before refreshing state...', wait_time)
				await asyncio.sleep(wait_time)
				
				logger.debug('Refreshing browser state to get latest page content...')
				try:
					# Add timeout to prevent hanging on state refresh
					state = await asyncio.wait_for(
						self.controller.refresh_state(include_dom=True, include_screenshot=False),
						timeout=30.0
					)
					logger.debug('Browser state refreshed successfully (URL: %s)', state.url if state else 'unknown')
					# Add a small delay after state refresh to ensure DOM and selector_map are fully stable
					# This helps prevent "element not available" errors when indices should be valid
					await asyncio.sleep(0.1)
				except asyncio.TimeoutError:
					logger.error('State refresh timed out after action execution at step %d', step)
					state = self.controller.last_state or state
				except Exception as error:  # noqa: BLE001
					logger.warning('Failed to refresh browser state: %s', error)
					state = self.controller.last_state or state
			else:
				# Element error was already handled - DOM was refreshed above with screenshot
				logger.debug('Skipping normal state refresh - already refreshed after element error')
			
			# Check if step exceeded timeout (stuck detection)
			step_elapsed = time.time() - step_start_time
			if step_elapsed > self.config.step_timeout:
				logger.warning(
					'Step %d exceeded timeout of %.1fs (took %.1fs). Step may be stuck. Refreshing state and continuing to next step.',
					step,
					self.config.step_timeout,
					step_elapsed,
				)
				self._context_log.append(f'Step {step} exceeded timeout ({step_elapsed:.1f}s), refreshing state and continuing')
				self._context_log = self._context_log[-20:]
				
				# Refresh state with screenshot to help agent see current state
				try:
					state = await asyncio.wait_for(
						self.controller.refresh_state(include_dom=True, include_screenshot=True),
						timeout=30.0
					)
					logger.debug('State refreshed after step timeout (URL: %s)', state.url if state else 'unknown')
				except Exception as error:  # noqa: BLE001
					logger.warning('Failed to refresh state after timeout: %s', error)
					state = self.controller.last_state or state
				
				# Continue to next step instead of getting stuck
				continue

		return AgentRunResult(
			success=False,
			awaiting_user_input=False,
			message='Max step limit reached without finishing the task.',
			structured_message='',
			final_state=state,
			context_log=list(self._context_log),
		)

	def _is_element_error(self, result_str: str) -> bool:
		"""Check if error indicates element changed or not found."""
		if not result_str:
			return False
		
		result_lower = result_str.lower()
		element_error_indicators = [
			'not available',
			'not found',
			'may have changed',
			'page may have changed',
			'stale',
			'backendnodeid',
			'selector_map',
			'element index',
			'element with',
		]
		
		# Check for element-related error patterns
		for indicator in element_error_indicators:
			if indicator in result_lower:
				return True
		
		# Check for specific error messages about elements
		if 'element' in result_lower and ('not' in result_lower or 'changed' in result_lower):
			return True
		
		# Check for index-related errors
		if 'index' in result_lower and 'not' in result_lower:
			return True
		
		return False

	def _format_tab_summary(self, state) -> str:
		if state is None:
			return 'No state available.'
		details: List[str] = []
		details.append(f'URL: {state.url}')
		if state.title:
			details.append(f'Title: {state.title}')
		if state.tabs:
			tab_list = ', '.join(f'[{idx}] {tab.title or tab.url}' for idx, tab in enumerate(state.tabs, start=1))
			details.append(f'Tabs: {tab_list}')
		if state.dom_state is not None:
			try:
				details.append(state.dom_state.llm_representation())
			except Exception as error:  # noqa: BLE001
				logger.debug('Failed to build DOM representation: %s', error)
		return '\n'.join(details)

	def _publish_narrations(self, structured: StructuredAgentResponse) -> None:
		if not self.narration_callback:
			return
		for entry in extract_narrations(structured.raw_text):
			self.narration_callback(entry)

	def _interpret_action(self, structured: StructuredAgentResponse) -> Dict[str, Any] | None:
		if not structured.actions:
			return None
		for entry in reversed(structured.actions):
			try:
				return json.loads(entry)
			except json.JSONDecodeError:
				continue
		return None

	async def _execute_action(self, action_type: str, payload: Dict[str, Any]) -> str:
		try:
			result = None
			if action_type == 'search':
				query = payload.get('query') or ''
				engine = payload.get('engine') or self.config.search_engine
				result = await self.controller.search(query, engine)
			elif action_type == 'navigate':
				url = payload.get('url') or ''
				new_tab = bool(payload.get('new_tab', False))
				result = await self.controller.navigate(url, new_tab=new_tab)
			elif action_type == 'click':
				result = await self.controller.click(
					index=payload.get('index'),
					coordinate_x=payload.get('coordinate_x'),
					coordinate_y=payload.get('coordinate_y'),
				)
			elif action_type == 'input':
				# Add delay before typing to ensure field is ready and prevent missing initial characters
				await asyncio.sleep(0.5)
				result = await self.controller.input_text(
					index=payload.get('index'),
					text=payload.get('text', ''),
					clear=bool(payload.get('clear', True)),
				)
			elif action_type == 'scroll':
				direction = payload.get('direction', 'down')
				pages = float(payload.get('pages', 1))
				result = await self.controller.scroll(direction=direction, pages=pages, index=payload.get('index'))
			elif action_type == 'send_keys':
				keys = payload.get('keys', '')
				result = await self.controller.send_keys(keys)
			elif action_type == 'screenshot':
				result = await self.controller.screenshot()
			else:
				return f'Unsupported action type: {action_type}'
			
			# Check if action failed
			if result is not None:
				if result.error:
					error_msg = f'Action {action_type} failed: {result.error}'
					if result.extracted_content:
						error_msg = f'{result.extracted_content} (Error: {result.error})'
					logger.warning('Action failed: %s', error_msg)
					return error_msg
				if result.success is False:
					error_msg = f'Action {action_type} failed'
					if result.extracted_content:
						error_msg = result.extracted_content
					logger.warning('Action failed: %s', error_msg)
					return error_msg
				# Check if extracted_content contains error indicators
				# (browser-use sometimes puts errors in extracted_content instead of error field)
				if result.extracted_content:
					error_indicators = [
						'not available',
						'failed',
						'error',
						'not found',
						'could not',
						'unable to',
						'may have changed',
					]
					content_lower = result.extracted_content.lower()
					if any(indicator in content_lower for indicator in error_indicators):
						error_msg = f'Action {action_type} failed: {result.extracted_content}'
						logger.warning('Action failed (detected in extracted_content): %s', error_msg)
						return error_msg
			
			# Return success message
			if result and result.extracted_content:
				return result.extracted_content
			
			# Fallback success messages
			if action_type == 'click':
				return 'Clicked element.'
			if action_type == 'input':
				return 'Entered text.'
			if action_type == 'search':
				query = payload.get('query', '')
				engine = payload.get('engine', self.config.search_engine)
				return f"Searched {engine} for '{query}'."
			if action_type == 'navigate':
				url = payload.get('url', '')
				return f'Navigated to {url}.'
			if action_type == 'scroll':
				direction = payload.get('direction', 'down')
				return f'Scrolled {direction}.'
			if action_type == 'send_keys':
				keys = payload.get('keys', '')
				return f'Sent keys: {keys}.'
			if action_type == 'screenshot':
				return 'Captured screenshot.'
			
			return f'Action {action_type} completed.'
		except Exception as error:  # noqa: BLE001
			logger.error('Action execution failed: %s', error, exc_info=True)
			return f'Action {action_type} failed: {error}'

	async def _preview_and_capture_highlight(self, action_type: str, payload: Dict[str, Any], step: int) -> str | None:
		"""Show highlight indicator and capture screenshot before action execution."""
		
		# Skip preview highlights - user wants only action-time highlights, not preview highlights
		# The browser-use library will still show highlights during actual action execution
		# if HIGHLIGHT_ELEMENTS is enabled in BrowserProfile
		
		# Skip if highlighting is disabled globally
		if not Config.HIGHLIGHT_ELEMENTS:
			return None
		
		try:
			node = None
			
			# Get the element node (for screenshot purposes, not highlighting)
			if action_type == 'click' and payload.get('index') is not None:
				node = await self.controller.browser_session.get_element_by_index(payload['index'])
			elif action_type == 'input' and payload.get('index') is not None:
				node = await self.controller.browser_session.get_element_by_index(payload['index'])
			elif action_type == 'click' and payload.get('coordinate_x') is not None:
				# For coordinate clicks, we can't easily get the node, so skip preview
				logger.debug('Coordinate-based click - skipping highlight preview')
				return None
			
			if not node:
				logger.warning('Could not find element node for highlight preview')
				return None
			
			# Don't show preview highlight - user wants only action-time highlights
			# await self.controller.browser_session.highlight_interaction_element(node)
			
			# No need to wait for highlight since we're not showing it
			# await asyncio.sleep(Config.HIGHLIGHT_SCREENSHOT_DELAY)
			
			# Take screenshot with highlight visible
			# We'll capture it to a temporary location first, then read it for base64
			import tempfile
			import base64
			import os
			
			tmp_path = None
			try:
				with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
					tmp_path = tmp_file.name
				# Take screenshot to temporary file
				await self.controller.browser_session.take_screenshot(
					path=tmp_path,
					full_page=False,
					format='png',
				)
				
				# Read the screenshot and convert to base64 for frontend
				with open(tmp_path, 'rb') as f:
					screenshot_bytes = f.read()
					screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
					# Store for use in step callback
					self._last_highlight_screenshot_base64 = f'data:image/png;base64,{screenshot_base64}'
				
				# If saving to disk is enabled, also save to the configured directory
				if Config.SAVE_HIGHLIGHT_SCREENSHOTS:
					screenshot_dir = Path(Config.SCREENSHOT_DIR)
					screenshot_dir.mkdir(parents=True, exist_ok=True)
					
					# Generate filename with timestamp, step, and action details
					timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]  # Include milliseconds
					
					# Add action-specific info to filename
					action_info = action_type
					if action_type == 'click' and payload.get('index') is not None:
						action_info = f'click_idx{payload["index"]}'
					elif action_type == 'input' and payload.get('index') is not None:
						action_info = f'input_idx{payload["index"]}'
					
					filename = f'highlight_{action_info}_step{step}_{timestamp}.png'
					screenshot_path = screenshot_dir / filename
					
					# Copy the temporary file to the final location
					import shutil
					shutil.copy2(tmp_path, str(screenshot_path))
					logger.debug('Captured highlight screenshot (before action): %s', screenshot_path)
				
				return self._last_highlight_screenshot_base64
			except Exception as e:
				logger.warning('Failed to capture highlight screenshot: %s', e)
				return None
			finally:
				# Clean up temporary file
				if tmp_path:
					try:
						if os.path.exists(tmp_path):
							os.unlink(tmp_path)
					except Exception:
						pass
			
			# Note: Highlight will automatically fade after configured duration
			# The actual action will execute next, which may trigger another highlight
			# but this one captures the preview state
			
		except Exception as error:  # noqa: BLE001
			# Don't fail the action if screenshot capture fails
			logger.warning('Failed to preview and capture highlight screenshot: %s', error)
			return None

	def _select_final_message(self, structured: StructuredAgentResponse) -> str:
		best = structured.best_message()
		if best:
			return best
		return 'Task completed.'

	def _format_structured(self, structured: StructuredAgentResponse) -> str:
		narration = structured.narration[-1] if structured.narration else ''
		action = structured.actions[-1] if structured.actions else ''
		result = structured.results[-1] if structured.results else ''
		return self.answer_builder.build(narration=narration, action=action, result=result)

	def _extract_page_info(self, state) -> str:
		"""Extract page information from browser state for reasoning."""
		if state is None:
			return 'No page state available'
		
		info_parts = []
		
		info_parts.append(f"Page: {state.url}")
		if state.title and state.title != state.url:
			info_parts.append(f"Title: {state.title}")
		
		if state.dom_state and hasattr(state.dom_state, 'selector_map'):
			selector_map = state.dom_state.selector_map
			if selector_map:
				key_buttons = []
				key_links = []
				key_inputs = []
				
				for index, element in list(selector_map.items())[:20]:
					tag = element.tag_name.lower() if hasattr(element, 'tag_name') else ''
					text = ''
					if hasattr(element, 'get_all_children_text'):
						try:
							text = element.get_all_children_text(max_depth=1)[:40].strip()
						except Exception:
							pass
					
					attrs = getattr(element, 'attributes', {})
					aria_label = attrs.get('aria-label', '').strip()
					placeholder = attrs.get('placeholder', '').strip()
					href = attrs.get('href', '').strip()
					
					display_text = text or aria_label or placeholder
					if not display_text and href:
						display_text = href.split('/')[-1] or href[:30]
					if not display_text:
						continue
					
					if len(display_text) > 40:
						display_text = display_text[:37] + '...'
					
					if tag == 'button' or 'button' in str(attrs.get('role', '')).lower():
						if len(key_buttons) < 3:
							key_buttons.append(f"[{index}] {display_text}")
					elif tag == 'a' or href:
						if len(key_links) < 3:
							key_links.append(f"[{index}] {display_text}")
					elif tag in ('input', 'textarea'):
						if len(key_inputs) < 3:
							key_inputs.append(f"[{index}] {display_text}")
				
				total_interactive = len(selector_map)
				info_parts.append(f"{total_interactive} interactive elements visible")
				
				if key_buttons:
					info_parts.append(f"Key buttons: {', '.join(key_buttons)}")
				if key_links:
					info_parts.append(f"Key links: {', '.join(key_links)}")
				if key_inputs:
					info_parts.append(f"Key inputs: {', '.join(key_inputs)}")
		
		return ' | '.join(info_parts)

	def _extract_reasoning_from_state(self, tab_summary: str, structured: StructuredAgentResponse, state=None) -> str:
		"""Extract reasoning from browser state and agent thinking."""
		reasoning_parts = []
		
		if structured.thinking:
			thinking_text = ' '.join(structured.thinking[-2:])
			if thinking_text.strip():
				reasoning_parts.append(thinking_text)
		
		if structured.evaluate:
			eval_text = ' '.join(structured.evaluate[-1:])
			if eval_text.strip():
				reasoning_parts.append(f"Evaluation: {eval_text}")
		
		if state:
			page_info = self._extract_page_info(state)
			if page_info and page_info != 'No page state available':
				reasoning_parts.append(f"Page info: {page_info}")
		
		if not state and tab_summary:
			lines = tab_summary.split('\n')
			url_line = next((line for line in lines if line.startswith('URL:')), None)
			if url_line:
				url = url_line.replace('URL:', '').strip()
				reasoning_parts.append(f"On page: {url}")
			
			title_line = next((line for line in lines if line.startswith('Title:')), None)
			if title_line:
				title = title_line.replace('Title:', '').strip()
				reasoning_parts.append(f"Page title: {title}")
		
		if reasoning_parts:
			return ' | '.join(reasoning_parts)
		return 'Analyzing current browser state and planning next action...'

	def clear_conversation(self) -> None:
		"""Clear conversation history."""
		logger.info('Clearing conversation history for new session')
		self._conversation.clear()
		self._context_log.clear()

	def get_conversation_summary(self) -> str:
		"""Get summary of conversation history."""
		if not self._conversation:
			return 'No conversation history'
		summary_parts = []
		for i, msg in enumerate(self._conversation[-10:], start=1):  # Last 10 messages
			if isinstance(msg, UserMessage):
				content = str(msg.content)[:100]
				summary_parts.append(f'{i}. User: {content}...' if len(str(msg.content)) > 100 else f'{i}. User: {content}')
			elif isinstance(msg, AssistantMessage):
				content = str(msg.content)[:100]
				summary_parts.append(f'{i}. Assistant: {content}...' if len(str(msg.content)) > 100 else f'{i}. Assistant: {content}')
		return '\n'.join(summary_parts)

	def _format_tool_info(self, action_type: str, payload: Dict[str, Any]) -> str:
		"""Format tool execution information."""
		if action_type == 'search':
			query = payload.get('query', '')
			engine = payload.get('engine', 'google')
			return f"search(query='{query}', engine='{engine}')"
		elif action_type == 'navigate':
			url = payload.get('url', '')
			new_tab = payload.get('new_tab', False)
			tab_str = ', new_tab=True' if new_tab else ''
			return f"navigate(url='{url}'{tab_str})"
		elif action_type == 'click':
			if payload.get('index') is not None:
				return f"click(index={payload.get('index')})"
			elif payload.get('coordinate_x') is not None:
				return f"click(coordinate_x={payload.get('coordinate_x')}, coordinate_y={payload.get('coordinate_y')})"
			return "click()"
		elif action_type == 'input':
			text = payload.get('text', '')
			index = payload.get('index', '?')
			text_preview = text[:30] + '...' if len(text) > 30 else text
			return f"input(index={index}, text='{text_preview}')"
		elif action_type == 'scroll':
			direction = payload.get('direction', 'down')
			pages = payload.get('pages', 1.0)
			index = payload.get('index')
			index_str = f", index={index}" if index is not None else ""
			return f"scroll(direction='{direction}', pages={pages}{index_str})"
		elif action_type == 'send_keys':
			keys = payload.get('keys', '')
			return f"send_keys(keys='{keys}')"
		elif action_type == 'screenshot':
			return "screenshot()"
		elif action_type == 'await_user_input':
			return "await_user_input()"
		elif action_type == 'none':
			return "none (task complete)"
		else:
			return f"{action_type}({', '.join(f'{k}={v}' for k, v in payload.items())})"

