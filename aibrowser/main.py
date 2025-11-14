"""Interactive entry point for the AI-controlled browser."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import aiohttp

from .browser_use_integration import BrowserUseIntegration
from .cdp_browser_manager import CDPBrowserManager
from .config import Config

LOGGER = logging.getLogger(__name__)


def setup_logging() -> None:
	log_format = '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
	# Use INFO level, but enable DEBUG for aibrowser package
	logging.basicConfig(level=logging.INFO, format=log_format)
	logging.getLogger('aibrowser').setLevel(logging.DEBUG)
	logging.getLogger('asyncio').setLevel(logging.WARNING)


async def interactive_loop(integration: BrowserUseIntegration) -> None:
	print('\n' + '=' * 60)
	print('AI Browser Interactive Mode')
	print('=' * 60)
	print("Type a browser task, or 'exit' to stop.")
	print('=' * 60 + '\n')

	while True:
		try:
			command = input('You: ').strip()
		except (EOFError, KeyboardInterrupt):
			print('\nExiting...')
			break

		if not command:
			continue
		if command.lower() in {'exit', 'quit', 'q'}:
			print('Exiting...')
			break

		print('Processing...')
		try:
			result = await integration.run(command)
			message = result.get('message', '')
			if message:
				print(f'Agent: {message}\n')
			else:
				print('Agent: (no message)\n')
		except Exception as e:
			print(f'Error: {e}\n')
			LOGGER.exception('Error processing command')


async def main() -> None:
	setup_logging()

	if not Config.validate():
		sys.exit(1)
	Config.log_config()

	port = int(os.getenv('CHROME_DEBUG_PORT', '9222'))
	headless = os.getenv('CHROMIUM_HEADLESS', 'false').lower() in {'1', 'true', 'yes', 'on'}

	manager = CDPBrowserManager(port=port, headless=headless)
	try:
		started = await manager.start()
		if not started or manager.endpoint is None:
			print('Failed to start Chromium. Ensure Chrome is installed or Playwright is available.')
			sys.exit(1)

		version_url = f'{manager.endpoint}/json/version'
		ws_url = None
		try:
			async with aiohttp.ClientSession() as session:
				async with session.get(version_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
					if resp.status != 200:
						print(f'CDP endpoint returned status {resp.status}. Chrome may not be ready.')
						sys.exit(1)
					data = await resp.json()
					ws_url = data.get('webSocketDebuggerUrl')
					if not ws_url:
						print('CDP endpoint did not provide WebSocket URL.')
						sys.exit(1)
					print(f'CDP endpoint ready: {manager.endpoint}')
					print(f'WebSocket URL: {ws_url}')
		except Exception as e:
			print(f'Failed to verify CDP endpoint: {e}')
			sys.exit(1)

		def step_callback(step: int, reasoning: str, narration: str, tool: str, phase: str) -> None:
			if phase == 'before':
				print(f'\n{"="*60}')
				print(f'Step {step}')
				print(f'{"="*60}')
				print(f'1) Reasoning (based on browser screen):')
				print(f'   {reasoning}')
				print(f'\n2) Agent narration:')
				print(f'   {narration}')
				print(f'\n3) Tool executed:')
				print(f'   {tool}')
			elif phase == 'after':
				print(f'\n   Result: {tool}')
				print(f'{"="*60}')
		
		integration = BrowserUseIntegration(
			cdp_url=ws_url,
			default_search_engine=Config.DEFAULT_SEARCH_ENGINE,
			step_callback=step_callback,
		)
		if not await integration.initialize():
			print('Failed to initialize browser-use integration.')
			sys.exit(1)

		await interactive_loop(integration)
	finally:
		await manager.stop()


if __name__ == '__main__':
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print('\nInterrupted.')

