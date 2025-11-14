"""Voice entry point for the AI-controlled browser with Pipecat."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import aiohttp

from .browser_use_integration import BrowserUseIntegration
from .cdp_browser_manager import CDPBrowserManager
from .config import Config
from .voice.agent_bridge import AgentBridge
from .voice.pipecat_pipeline import VoicePipeline

LOGGER = logging.getLogger(__name__)


def setup_logging() -> None:
	"""Configure clean, consistent logging for the application."""
	log_format = '%(levelname)-8s | %(message)s'
	logging.basicConfig(level=logging.INFO, format=log_format, datefmt='')
	
	logging.getLogger('aibrowser').setLevel(logging.INFO)
	logging.getLogger('asyncio').setLevel(logging.WARNING)
	logging.getLogger('pipecat').setLevel(logging.WARNING)
	logging.getLogger('pipecat.services.deepgram').setLevel(logging.WARNING)
	logging.getLogger('pipecat.transports.local').setLevel(logging.WARNING)
	logging.getLogger('pipecat.audio.vad').setLevel(logging.WARNING)
	logging.getLogger('pipecat.pipeline').setLevel(logging.WARNING)
	logging.getLogger('pipecat.processors').setLevel(logging.WARNING)
	logging.getLogger('httpx').setLevel(logging.WARNING)
	logging.getLogger('httpcore').setLevel(logging.WARNING)


async def voice_loop(integration: BrowserUseIntegration) -> None:
	"""Main voice interaction loop."""
	print('\n' + '=' * 60)
	print('AI Browser Voice Mode')
	print('=' * 60)
	print('Speak your browser task. Say "exit" or "quit" to stop.')
	print('=' * 60 + '\n')

	def on_user_speech(text: str) -> None:
		print(f'\n[You] {text}')

	def on_agent_response(text: str) -> None:
		if text:
			print(f'[Agent] {text}')

	agent_bridge = AgentBridge(
		integration=integration,
		on_user_speech=on_user_speech,
		on_agent_response=on_agent_response,
	)

	pipeline = VoicePipeline(
		agent_bridge=agent_bridge,
		deepgram_api_key=Config.DEEPGRAM_API_KEY,
		elevenlabs_api_key=Config.ELEVENLABS_API_KEY,
		elevenlabs_voice_id=Config.ELEVENLABS_VOICE_ID,
		deepgram_language=Config.DEEPGRAM_LANGUAGE,
	)

	if not await pipeline.initialize():
		print('Failed to initialize voice pipeline.')
		sys.exit(1)

	try:
		print('Voice pipeline started. Listening...\n')
		await pipeline.run()
	except KeyboardInterrupt:
		print('\nStopping voice pipeline...')
	except Exception as e:
		LOGGER.error('Error in voice loop: %s', e, exc_info=True)
		raise
	finally:
		await pipeline.stop()
		print('Voice pipeline stopped.')


async def main() -> None:
	setup_logging()

	if not Config.validate():
		sys.exit(1)

	if not Config.validate_voice():
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

		integration = BrowserUseIntegration(
			cdp_url=ws_url,
			default_search_engine=Config.DEFAULT_SEARCH_ENGINE,
		)
		if not await integration.initialize():
			print('Failed to initialize browser-use integration.')
			sys.exit(1)

		await voice_loop(integration)

	finally:
		await manager.stop()


if __name__ == '__main__':
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print('\nInterrupted.')

