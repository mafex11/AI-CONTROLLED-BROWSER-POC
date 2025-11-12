"""Thin wrapper around browser-use Tools to simplify action execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from browser_use.agent.views import ActionResult
from browser_use.browser.events import BrowserStateRequestEvent, ScreenshotEvent
from browser_use.browser.session import BrowserSession
from browser_use.browser.views import BrowserStateSummary
from browser_use.tools.service import Tools


@dataclass
class BrowserController:
	"""Expose high-level helpers for executing browser-use actions."""

	browser_session: BrowserSession
	tools: Tools

	_last_state: BrowserStateSummary | None = None

	async def refresh_state(
		self,
		*,
		include_dom: bool = True,
		include_screenshot: bool = False,
		include_recent_events: bool = False,
	) -> BrowserStateSummary:
		event = self.browser_session.event_bus.dispatch(
			BrowserStateRequestEvent(
				include_dom=include_dom,
				include_screenshot=include_screenshot,
				include_recent_events=include_recent_events,
			)
		)
		await event
		state = await event.event_result(raise_if_any=True, raise_if_none=False)
		self._last_state = state
		return state

	@property
	def last_state(self) -> BrowserStateSummary | None:
		return self._last_state

	async def execute_action(self, action_name: str, params: Dict[str, Any]) -> ActionResult:
		action = getattr(self.tools, action_name, None)
		if action is None:
			raise ValueError(f'Unknown action: {action_name}')

		result = await action(browser_session=self.browser_session, **params)

		if isinstance(result, ActionResult):
			return result
		if isinstance(result, dict):
			return ActionResult(**result)
		return ActionResult(extracted_content=str(result))

	async def search(self, query: str, engine: str) -> ActionResult:
		return await self.execute_action('search', {'query': query, 'engine': engine})

	async def navigate(self, url: str, *, new_tab: bool = False) -> ActionResult:
		return await self.execute_action('navigate', {'url': url, 'new_tab': new_tab})

	async def click(
		self,
		*,
		index: Optional[int] = None,
		coordinate_x: Optional[int] = None,
		coordinate_y: Optional[int] = None,
	) -> ActionResult:
		payload: Dict[str, Any] = {}
		if index is not None:
			payload['index'] = index
		if coordinate_x is not None:
			payload['coordinate_x'] = coordinate_x
		if coordinate_y is not None:
			payload['coordinate_y'] = coordinate_y
		return await self.execute_action('click', payload)

	async def input_text(self, *, index: int, text: str, clear: bool = True) -> ActionResult:
		return await self.execute_action(
			'input',
			{
				'index': index,
				'text': text,
				'clear': clear,
			},
		)

	async def scroll(self, *, direction: str, pages: float = 1.0, index: Optional[int] = None) -> ActionResult:
		# Convert direction string to boolean 'down' field required by ScrollAction
		down = direction.lower() in ('down', 'd')
		payload: Dict[str, Any] = {'down': down, 'pages': pages}
		if index is not None:
			payload['index'] = index
		return await self.execute_action('scroll', payload)

	async def send_keys(self, keys: str) -> ActionResult:
		return await self.execute_action('send_keys', {'keys': keys})

	async def screenshot(self) -> ActionResult:
		result = await self.execute_action('screenshot', {})
		event = self.browser_session.event_bus.dispatch(ScreenshotEvent())
		await event
		return result

