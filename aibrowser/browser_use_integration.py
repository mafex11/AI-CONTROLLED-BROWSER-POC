"""Integration layer connecting the direct agent to browser-use tooling."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.google.chat import ChatGoogle
from browser_use.llm.openai.chat import ChatOpenAI
from browser_use.tools.service import Tools

from .browser_controller import BrowserController
from .config import Config
from .direct_browser_agent import AgentRunConfig, DirectBrowserAgent
from .structured_prompt import AnswerPromptBuilder, ObservationPromptBuilder, StructuredPromptBuilder

logger = logging.getLogger(__name__)


_CALLBACK_SENTINEL = object()


def _quiet_browser_use_logs() -> None:
	for name, level in {
		'httpx': logging.WARNING,
		'cdp_use': logging.WARNING,
		'cdp_use.client': logging.WARNING,
		'browser_use.telemetry': logging.WARNING,
		'browser_use.observability': logging.WARNING,
		'browser_use.tools.service': logging.WARNING,
		'browser_use.BrowserSession': logging.INFO,
		'browser_use.browser.watchdogs.aboutblank_watchdog': logging.WARNING,  
		'bubus': logging.WARNING,  
	}.items():
		logging.getLogger(name).setLevel(level)
	
	class EventBusCapacityFilter(logging.Filter):
		"""Filter out EventBus capacity errors."""
		def filter(self, record: logging.LogRecord) -> bool:
			if 'EventBus at capacity' in record.getMessage():
				return False
			if 'Error injecting DVD screensaver' in record.getMessage():
				return False
			return True
	
	for logger_name in ['browser_use.browser.watchdogs.aboutblank_watchdog', 'bubus']:
		logger = logging.getLogger(logger_name)
		logger.addFilter(EventBusCapacityFilter())


@dataclass
class _State:
	browser_session: BrowserSession
	controller: BrowserController
	agent: DirectBrowserAgent


class BrowserUseIntegration:
	"""Owns BrowserSession and exposes run(command) interface."""

	def __init__(
		self,
		*,
		cdp_url: Optional[str] = None,
		default_search_engine: str | None = None,
		narration_callback=None,
		step_callback=None,
	) -> None:
		self.cdp_url = cdp_url
		self.default_search_engine = (default_search_engine or Config.DEFAULT_SEARCH_ENGINE).lower()
		self.narration_callback = narration_callback
		self.step_callback = step_callback

		self._state: _State | None = None
		self._initialized = False

	async def initialize(self) -> bool:
		if self._initialized:
			return True

		_quiet_browser_use_logs()

		try:
			llm = self._build_llm()
			browser_session = await self._build_browser_session()
			tools = Tools()
			controller = BrowserController(browser_session=browser_session, tools=tools)

			prompt_builder = StructuredPromptBuilder(
				base_prompt=Config.system_prompt(),
				search_engine=self.default_search_engine,
			)
			observation_builder = ObservationPromptBuilder(search_engine=self.default_search_engine)
			answer_builder = AnswerPromptBuilder()

			max_output_tokens = (
				Config.GEMINI_MAX_OUTPUT_TOKENS
				if Config.LLM_PROVIDER == 'gemini'
				else Config.CLAUDE_MAX_TOKENS
				if Config.LLM_PROVIDER == 'claude'
				else Config.OPENAI_MAX_TOKENS
			)
			max_missing_action_retries = max_output_tokens // 400 if max_output_tokens else 5

			agent = DirectBrowserAgent(
				controller=controller,
				llm=llm,
				system_prompt_builder=prompt_builder,
				observation_builder=observation_builder,
				answer_builder=answer_builder,
				config=AgentRunConfig(
					max_steps=50,
					search_engine=self.default_search_engine,
					max_missing_action_retries=max_missing_action_retries,
				),
				narration_callback=self.narration_callback,
				step_callback=self.step_callback,
			)

			self._state = _State(
				browser_session=browser_session,
				controller=controller,
				agent=agent,
			)
			self._initialized = True
			return True
		except Exception as error:
			logger.error('Failed to initialize browser integration: %s', error, exc_info=True)
			self._initialized = False
			return False

	async def run(self, command: str, *, is_continuation: bool = False) -> Dict[str, Any]:
		if not self._initialized or not self._state:
			raise RuntimeError('BrowserUseIntegration is not initialized.')

		result = await self._state.agent.run(command, is_continuation=is_continuation)
		payload: Dict[str, Any] = {
			'success': result.success,
			'awaiting_user_input': result.awaiting_user_input,
			'message': result.message,
			'structured_message': result.structured_message,
			'context': result.context_log,
		}
		if result.final_state is not None:
			payload['final_url'] = getattr(result.final_state, 'url', None)
			payload['final_title'] = getattr(result.final_state, 'title', None)
		return payload

	def update_callbacks(
		self,
		*,
		narration_callback=_CALLBACK_SENTINEL,
		step_callback=_CALLBACK_SENTINEL,
	) -> None:
		if not self._initialized or not self._state:
			return
		if narration_callback is not _CALLBACK_SENTINEL:
			self.narration_callback = narration_callback
			self._state.agent.narration_callback = narration_callback
		if step_callback is not _CALLBACK_SENTINEL:
			self.step_callback = step_callback
			self._state.agent.step_callback = step_callback

	def clear_conversation(self) -> None:
		if not self._initialized or not self._state:
			return
		self._state.agent.clear_conversation()

	def get_conversation_summary(self) -> str:
		if not self._initialized or not self._state:
			return 'Agent not initialized'
		return self._state.agent.get_conversation_summary()

	async def shutdown(self) -> None:
		if self._state:
			try:
				await self._state.browser_session.stop()
			except Exception:
				logger.debug('Error while stopping browser session', exc_info=True)
		self._state = None
		self._initialized = False

	def _build_llm(self):
		if Config.LLM_PROVIDER == 'gemini':
			return ChatGoogle(
				model=Config.GEMINI_MODEL,
				api_key=Config.GEMINI_API_KEY,
				temperature=Config.GEMINI_TEMPERATURE,
				max_output_tokens=Config.GEMINI_MAX_OUTPUT_TOKENS,
				top_p=Config.GEMINI_TOP_P,
				max_retries=5,
				retryable_status_codes=[403, 503, 429],
				retry_delay=2.0,
			)
		elif Config.LLM_PROVIDER == 'claude':
			from httpx import Timeout
			return ChatAnthropic(
				model=Config.CLAUDE_MODEL,
				api_key=Config.CLAUDE_API_KEY,
				temperature=Config.CLAUDE_TEMPERATURE,
				max_tokens=Config.CLAUDE_MAX_TOKENS,
				top_p=Config.CLAUDE_TOP_P,
				timeout=Timeout(Config.CLAUDE_TIMEOUT, connect=10.0), 
				max_retries=Config.CLAUDE_MAX_RETRIES,  
		elif Config.LLM_PROVIDER == 'openai':
			openai_kwargs = {
				'model': Config.OPENAI_MODEL,
				'api_key': Config.OPENAI_API_KEY,
				'temperature': Config.OPENAI_TEMPERATURE,
				'max_completion_tokens': Config.OPENAI_MAX_TOKENS,
				'frequency_penalty': Config.OPENAI_FREQUENCY_PENALTY,
				'max_retries': 5,
			}
			if Config.OPENAI_TOP_P is not None:
				openai_kwargs['top_p'] = Config.OPENAI_TOP_P
			return ChatOpenAI(**openai_kwargs)
		else:
			raise RuntimeError(f'Unsupported LLM_PROVIDER: {Config.LLM_PROVIDER}. Supported providers are "gemini", "claude", and "openai".')

	async def _build_browser_session(self) -> BrowserSession:
		if not self.cdp_url:
			raise RuntimeError('cdp_url is required to connect to Chromium.')

		browser_profile = BrowserProfile(
			highlight_elements=Config.HIGHLIGHT_ELEMENTS,
			dom_highlight_elements=Config.DOM_HIGHLIGHT_ELEMENTS,
			interaction_highlight_color=Config.INTERACTION_HIGHLIGHT_COLOR,
			interaction_highlight_duration=Config.INTERACTION_HIGHLIGHT_DURATION,
		)

		session = BrowserSession(
			cdp_url=self.cdp_url,
			is_local=False,
			browser_profile=browser_profile,
		)
		await session.start()
		await asyncio.sleep(0.1)
		
		# Inject script to prevent new tabs from opening
		prevent_new_tabs_script = """
		(function() {
			function removeTargetBlank() {
				const links = document.querySelectorAll('a[target="_blank"], a[target="blank"]');
				links.forEach(link => {
					link.removeAttribute('target');
				});
			}
			
			const originalWindowOpen = window.open;
			window.open = function(url, target, features) {
				if (target === '_blank' || target === 'blank') {
					if (url) {
						window.location.href = url;
					}
					return window;
				}
				return originalWindowOpen.call(window, url, target, features);
			};
			
			function setupHandlers() {
				document.addEventListener('click', function(e) {
					let element = e.target;
					while (element && element.tagName !== 'A') {
						element = element.parentElement;
					}
					if (element && element.tagName === 'A') {
						const target = element.getAttribute('target');
						if (target === '_blank' || target === 'blank') {
							element.removeAttribute('target');
						}
					}
				}, true); 
				
				removeTargetBlank();
				
				if (document.body || document.documentElement) {
					const observer = new MutationObserver(function(mutations) {
						removeTargetBlank();
					});
					observer.observe(document.body || document.documentElement, {
						childList: true,
						subtree: true
					});
				}
			}
			
			if (document.readyState === 'loading') {
				document.addEventListener('DOMContentLoaded', setupHandlers);
			} else {
				setupHandlers();
			}
		})();
		"""
		
		try:
			await session._cdp_add_init_script(prevent_new_tabs_script)
			logger.debug('Injected script to prevent new tabs from opening')
		except Exception as error:
			logger.warning('Failed to inject prevent-new-tabs script: %s', error)
		
		return session

