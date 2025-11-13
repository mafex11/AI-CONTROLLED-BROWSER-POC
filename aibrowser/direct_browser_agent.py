"""Custom agent loop that keeps direct control over browser-use tools."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Union

from browser_use.llm.messages import AssistantMessage, BaseMessage, SystemMessage, UserMessage

from .browser_controller import BrowserController
from .structured_output import StructuredAgentResponse, extract_narrations, parse_structured_response
from .structured_prompt import AnswerPromptBuilder, ObservationPromptBuilder, StructuredPromptBuilder

logger = logging.getLogger(__name__)


@dataclass
class AgentRunConfig:
	max_steps: int = 25
	search_engine: str = 'google'
	max_missing_action_retries: int = 5


@dataclass
class AgentRunResult:
	success: bool
	message: str
	structured_message: str
	final_state: Any | None
	context_log: List[str]
	awaiting_user_input: bool = False


class DirectBrowserAgent:
	"""Gemini-driven loop that chooses browser-use actions based on structured prompts."""

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

	async def run(self, task: str) -> AgentRunResult:
		logger.info('Starting agent task: %s', task)
		system_prompt = self.system_prompt_builder.build()
		self._conversation.clear()
		self._context_log.clear()

		try:
			logger.debug('Fetching initial browser state for new query...')
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

		for step in range(1, self.config.max_steps + 1):
			logger.debug('Agent step %d/%d', step, self.config.max_steps)
			try:
				tab_summary = self._format_tab_summary(state)
				context_lines = '\n'.join(self._context_log[-6:])
				observation = self.observation_builder.build(
					task=task,
					tab_summary=tab_summary,
					extra_context=context_lines,
				)

				messages: List[BaseMessage] = [SystemMessage(content=system_prompt), *self._conversation]
				user_message = UserMessage(content=observation)
				messages.append(user_message)
				
				# Store observation for step callback (reasoning)
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

			try:
				# Add timeout to prevent hanging, with exponential backoff for 503 errors
				response_text = None
				last_error = None
				max_retries = 3
				for retry_attempt in range(max_retries):
					try:
						response = await asyncio.wait_for(self.llm.ainvoke(messages), timeout=60.0)
						response_text = response.completion if hasattr(response, 'completion') else str(response)
						break  # Success, exit retry loop
					except Exception as error:  # noqa: BLE001
						last_error = error
						error_str = str(error)
						# Check if it's a 503 overload error
						if '503' in error_str or 'overloaded' in error_str.lower() or 'UNAVAILABLE' in error_str:
							if retry_attempt < max_retries - 1:
								# Exponential backoff: 2s, 4s, 8s
								wait_time = 2.0 * (2 ** retry_attempt)
								logger.warning(
									'Gemini API overloaded (503), waiting %ds before retry %d/%d',
									wait_time,
									retry_attempt + 1,
									max_retries,
								)
								await asyncio.sleep(wait_time)
								continue
						# For other errors or final retry, break and handle below
						break
				
				if response_text is None:
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
			self._conversation.extend([user_message, assistant_message])

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
			
			# Only call step callback after we have valid structured response
			# Extract agent response: Narration is what the agent SAYS it's doing (before action)
			# Result is what the agent SAYS happened (after action)
			agent_response_text = structured.narration[-1] if structured.narration else ''
			if not agent_response_text:
				# Fallback: use Result if Narration is empty
				agent_response_text = structured.results[-1] if structured.results else ''
			if not agent_response_text:
				agent_response_text = 'Preparing to execute action...'
			
			# Extract reasoning: from Thinking section + state analysis (separate from narration)
			reasoning_text = self._extract_reasoning_from_state(current_observation, structured, state=state)
			
			# Format tool execution info
			tool_info = self._format_tool_info(action_type, action_payload)
			
			# Call step callback BEFORE execution (shows what we're about to do)
			# Pass: step, reasoning, agent_response (narration), action (tool)
			if self.step_callback and agent_response_text and tool_info:
				try:
					result = self.step_callback(step, reasoning_text, agent_response_text, tool_info, 'before')
					# Await if callback is async
					if asyncio.iscoroutine(result):
						await result
				except Exception as callback_error:  # noqa: BLE001
					logger.warning('Step callback error: %s', callback_error)
			
			if action_type in {'none', 'done'}:
				final_text = self._select_final_message(structured)
				full = self._format_structured(structured)
				# For completion, use Result as agent response (what agent says happened)
				completion_response = structured.results[-1] if structured.results else agent_response_text
				if not completion_response:
					completion_response = 'Task completed.'
				if self.step_callback:
					try:
						result = self.step_callback(step, reasoning_text, completion_response, 'Task completed', 'after')
						if asyncio.iscoroutine(result):
							await result
					except Exception as callback_error:  # noqa: BLE001
						logger.warning('Step callback error: %s', callback_error)
				return AgentRunResult(
					success=True,
					awaiting_user_input=False,
					message=final_text,
					structured_message=full,
					final_state=state,
					context_log=list(self._context_log),
				)

			if action_type in {'await_user_input', 'awaiting_user_input'}:
				final_text = self._select_final_message(structured)
				full = self._format_structured(structured)
				# For user input requests, use Result as agent response
				await_response = structured.results[-1] if structured.results else agent_response_text
				if not await_response:
					await_response = 'I need your input to continue.'
				if self.step_callback:
					try:
						result = self.step_callback(step, reasoning_text, await_response, 'Awaiting user input', 'after')
						if asyncio.iscoroutine(result):
							await result
					except Exception as callback_error:  # noqa: BLE001
						logger.warning('Step callback error: %s', callback_error)
				return AgentRunResult(
					success=False,
					awaiting_user_input=True,
					message=final_text,
					structured_message=full,
					final_state=state,
					context_log=list(self._context_log),
				)

			# Execute action using the state that was shown to the LLM
			# browser-use Tools will use the cached selector_map which matches the indices
			# the LLM saw. Do NOT refresh state here as it would create new backend_node_ids
			# and break the element lookup.
			result_str = await self._execute_action(action_type, action_payload)
			self._context_log.append(result_str)
			self._context_log = self._context_log[-20:]
			
			# Call step callback AFTER execution (shows result)
			# Use Result as agent response (what agent says happened)
			after_response = structured.results[-1] if structured.results else agent_response_text
			if not after_response:
				after_response = f'{tool_info} → {result_str[:50]}'
			if self.step_callback:
				try:
					result_summary = result_str[:100] + '...' if len(result_str) > 100 else result_str
					callback_result = self.step_callback(step, reasoning_text, after_response, f'{tool_info} → {result_summary}', 'after')
					# Await if callback is async
					if asyncio.iscoroutine(callback_result):
						await callback_result
				except Exception as callback_error:  # noqa: BLE001
					logger.warning('Step callback error (after): %s', callback_error)

			# Wait for page to stabilize after action (dynamic content may need time to load)
			# Reduced delay for faster response - browser-use Tools handle their own timing
			wait_time = 0.5
			logger.debug('Waiting %.1fs for page to stabilize before refreshing state...', wait_time)
			await asyncio.sleep(wait_time)
			
			# Refresh state for next loop - this fetches the latest DOM from the browser
			# browser-use Tools handle their own state refresh internally, but we refresh here
			# to ensure the LLM sees the current state at the start of the next step
			logger.debug('Refreshing browser state to get latest page content...')
			try:
				state = await self.controller.refresh_state(include_dom=True, include_screenshot=False)
				logger.debug('Browser state refreshed successfully (URL: %s)', state.url if state else 'unknown')
			except Exception as error:  # noqa: BLE001
				logger.warning('Failed to refresh browser state: %s', error)
				# Try once more after a short delay
			await asyncio.sleep(0.5)
			state = await self.controller.refresh_state(include_dom=True, include_screenshot=False)

		return AgentRunResult(
			success=False,
			awaiting_user_input=False,
			message='Max step limit reached without finishing the task.',
			structured_message='',
			final_state=state,
			context_log=list(self._context_log),
		)

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
			if action_type == 'search':
				query = payload.get('query') or ''
				engine = payload.get('engine') or self.config.search_engine
				result = await self.controller.search(query, engine)
				return result.extracted_content or f"Searched {engine} for '{query}'."
			if action_type == 'navigate':
				url = payload.get('url') or ''
				new_tab = bool(payload.get('new_tab', False))
				result = await self.controller.navigate(url, new_tab=new_tab)
				return result.extracted_content or f'Navigated to {url}.'
			if action_type == 'click':
				result = await self.controller.click(
					index=payload.get('index'),
					coordinate_x=payload.get('coordinate_x'),
					coordinate_y=payload.get('coordinate_y'),
				)
				return result.extracted_content or 'Clicked element.'
			if action_type == 'input':
				result = await self.controller.input_text(
					index=payload.get('index'),
					text=payload.get('text', ''),
					clear=bool(payload.get('clear', True)),
				)
				return result.extracted_content or 'Entered text.'
			if action_type == 'scroll':
				direction = payload.get('direction', 'down')
				pages = float(payload.get('pages', 1))
				result = await self.controller.scroll(direction=direction, pages=pages, index=payload.get('index'))
				return result.extracted_content or f'Scrolled {direction}.'
			if action_type == 'send_keys':
				keys = payload.get('keys', '')
				result = await self.controller.send_keys(keys)
				return result.extracted_content or f'Sent keys: {keys}.'
			if action_type == 'screenshot':
				result = await self.controller.screenshot()
				return result.extracted_content or 'Captured screenshot.'
			return f'Unsupported action type: {action_type}'
		except Exception as error:  # noqa: BLE001
			logger.error('Action execution failed: %s', error, exc_info=True)
			return f'Action {action_type} failed: {error}'

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
		"""Extract detailed page information from browser state for reasoning."""
		if state is None:
			return 'No page state available'
		
		info_parts = []
		
		# Basic page info
		info_parts.append(f"Page: {state.url}")
		if state.title and state.title != state.url:
			info_parts.append(f"Title: {state.title}")
		
		# Extract interactive elements information
		if state.dom_state and hasattr(state.dom_state, 'selector_map'):
			selector_map = state.dom_state.selector_map
			if selector_map:
				# Count different types of elements
				key_buttons = []
				key_links = []
				key_inputs = []
				
				for index, element in list(selector_map.items())[:20]:  # Limit to first 20 for performance
					tag = element.tag_name.lower() if hasattr(element, 'tag_name') else ''
					text = ''
					if hasattr(element, 'get_all_children_text'):
						try:
							text = element.get_all_children_text(max_depth=1)[:40].strip()
						except Exception:
							pass
					
					# Get attributes for better identification
					attrs = getattr(element, 'attributes', {})
					aria_label = attrs.get('aria-label', '').strip()
					placeholder = attrs.get('placeholder', '').strip()
					href = attrs.get('href', '').strip()
					
					# Identify element type and get display text
					display_text = text or aria_label or placeholder
					if not display_text and href:
						display_text = href.split('/')[-1] or href[:30]
					if not display_text:
						continue  # Skip elements with no identifiable text
					
					# Limit display text length
					if len(display_text) > 40:
						display_text = display_text[:37] + '...'
					
					if tag == 'button' or 'button' in str(attrs.get('role', '')).lower():
						if len(key_buttons) < 3:  # Only keep top 3 buttons
							key_buttons.append(f"[{index}] {display_text}")
					elif tag == 'a' or href:
						if len(key_links) < 3:  # Only keep top 3 links
							key_links.append(f"[{index}] {display_text}")
					elif tag in ('input', 'textarea'):
						if len(key_inputs) < 3:  # Only keep top 3 inputs
							key_inputs.append(f"[{index}] {display_text}")
				
				# Add summary with key visible elements
				total_interactive = len(selector_map)
				info_parts.append(f"{total_interactive} interactive elements visible")
				
				# Add key visible elements (most important ones)
				if key_buttons:
					info_parts.append(f"Key buttons: {', '.join(key_buttons)}")
				if key_links:
					info_parts.append(f"Key links: {', '.join(key_links)}")
				if key_inputs:
					info_parts.append(f"Key inputs: {', '.join(key_inputs)}")
		
		return ' | '.join(info_parts)

	def _extract_reasoning_from_state(self, tab_summary: str, structured: StructuredAgentResponse, state=None) -> str:
		"""Extract reasoning based on browser state and agent thinking."""
		reasoning_parts = []
		
		# Add thinking/thought sections if available (these come from the LLM)
		if structured.thinking:
			thinking_text = ' '.join(structured.thinking[-2:])  # Last 2 thinking entries
			if thinking_text.strip():
				reasoning_parts.append(thinking_text)
		
		# Add evaluate sections if available
		if structured.evaluate:
			eval_text = ' '.join(structured.evaluate[-1:])  # Last evaluation
			if eval_text.strip():
				reasoning_parts.append(f"Evaluation: {eval_text}")
		
		# Extract detailed page information from state
		if state:
			page_info = self._extract_page_info(state)
			if page_info and page_info != 'No page state available':
				reasoning_parts.append(f"Page info: {page_info}")
		
		# Fallback to tab_summary if state not available
		if not state and tab_summary:
			lines = tab_summary.split('\n')
			# Get URL
			url_line = next((line for line in lines if line.startswith('URL:')), None)
			if url_line:
				url = url_line.replace('URL:', '').strip()
				reasoning_parts.append(f"On page: {url}")
			
			# Get title if available
			title_line = next((line for line in lines if line.startswith('Title:')), None)
			if title_line:
				title = title_line.replace('Title:', '').strip()
				reasoning_parts.append(f"Page title: {title}")
		
		if reasoning_parts:
			return ' | '.join(reasoning_parts)
		return 'Analyzing current browser state and planning next action...'

	def _format_tool_info(self, action_type: str, payload: Dict[str, Any]) -> str:
		"""Format tool execution information in a readable way."""
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

