"""Integration layer connecting the direct agent to browser-use tooling."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from browser_use.browser.session import BrowserSession
from browser_use.llm.google.chat import ChatGoogle
from browser_use.tools.service import Tools

from .browser_controller import BrowserController
from .config import Config
from .direct_browser_agent import AgentRunConfig, DirectBrowserAgent
from .structured_prompt import AnswerPromptBuilder, ObservationPromptBuilder, StructuredPromptBuilder

logger = logging.getLogger(__name__)


_CALLBACK_SENTINEL = object()


def _quiet_browser_use_logs() -> None:
	"""Suppress noisy browser-use logs, including EventBus capacity errors."""
	for name, level in {
		'httpx': logging.WARNING,
		'cdp_use': logging.WARNING,
		'cdp_use.client': logging.WARNING,
		'browser_use.telemetry': logging.WARNING,
		'browser_use.observability': logging.WARNING,
		'browser_use.tools.service': logging.WARNING,
		'browser_use.BrowserSession': logging.INFO,
		'browser_use.browser.watchdogs.aboutblank_watchdog': logging.WARNING,  # Suppress EventBus capacity errors
		'bubus': logging.WARNING,  # Suppress EventBus capacity warnings
	}.items():
		logging.getLogger(name).setLevel(level)
	
	class EventBusCapacityFilter(logging.Filter):
		"""Filter out EventBus capacity errors from AboutBlankWatchdog."""
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
	"""Owns the BrowserSession and exposes a simple run(command) interface."""

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

			agent = DirectBrowserAgent(
				controller=controller,
				llm=llm,
				system_prompt_builder=prompt_builder,
				observation_builder=observation_builder,
				answer_builder=answer_builder,
				config=AgentRunConfig(
					max_steps=25,
					search_engine=self.default_search_engine,
					max_missing_action_retries=Config.GEMINI_MAX_OUTPUT_TOKENS // 400 if Config.GEMINI_MAX_OUTPUT_TOKENS else 5,
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
		except Exception as error:  # noqa: BLE001
			logger.error('Failed to initialize browser integration: %s', error, exc_info=True)
			self._initialized = False
			return False

	async def run(self, command: str) -> Dict[str, Any]:
		if not self._initialized or not self._state:
			raise RuntimeError('BrowserUseIntegration is not initialized.')

		result = await self._state.agent.run(command)
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
		"""Update integration and agent callbacks at runtime."""
		if not self._initialized or not self._state:
			return
		if narration_callback is not _CALLBACK_SENTINEL:
			self.narration_callback = narration_callback
			self._state.agent.narration_callback = narration_callback
		if step_callback is not _CALLBACK_SENTINEL:
			self.step_callback = step_callback
			self._state.agent.step_callback = step_callback

	def clear_conversation(self) -> None:
		"""Clear conversation history to start a new session."""
		if not self._initialized or not self._state:
			return
		self._state.agent.clear_conversation()

	def get_conversation_summary(self) -> str:
		"""Get a summary of the conversation history."""
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
		if Config.LLM_PROVIDER != 'gemini':
			raise RuntimeError('Only Gemini provider is supported in this build.')
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

	async def _build_browser_session(self) -> BrowserSession:
		if not self.cdp_url:
			raise RuntimeError('cdp_url is required to connect to Chromium.')

		# is_local=False because CDPBrowserManager handles Chrome lifecycle
		session = BrowserSession(
			cdp_url=self.cdp_url,
			is_local=False,
		)
		await session.start()
		# Reduced delay - session.start() already handles initialization
		await asyncio.sleep(0.1)
		return session

