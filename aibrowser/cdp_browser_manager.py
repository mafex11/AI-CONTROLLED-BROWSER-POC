"""Manage a shared Chromium instance exposed over the Chrome DevTools Protocol."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class CDPBrowserManager:
	"""Launch or connect to Chromium instance with CDP enabled."""

	def __init__(self, *, port: int = 9222, headless: bool = False) -> None:
		self.port = port
		self.headless = headless
		self._chrome_process: subprocess.Popen[str] | None = None
		self._playwright = None
		self._playwright_browser = None
		self._endpoint: str | None = None
		self._running = False

	def _candidate_paths(self) -> list[Path]:
		system = platform.system()
		if system == 'Windows':
			return [
				Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
				Path(r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'),
				Path.home() / r'AppData\Local\Google\Chrome\Application\chrome.exe',
			]
		if system == 'Darwin':
			return [Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')]
		return [
			Path('/usr/bin/google-chrome'),
			Path('/usr/bin/google-chrome-stable'),
			Path('/usr/bin/chromium'),
			Path('/usr/bin/chromium-browser'),
		]

	def _find_chrome(self) -> Optional[Path]:
		for path in self._candidate_paths():
			if path.exists():
				return path
		command = ['where', 'chrome.exe'] if platform.system() == 'Windows' else ['which', 'google-chrome']
		try:
			result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
			if result.returncode == 0:
				first = result.stdout.strip().splitlines()[0]
				if first and Path(first).exists():
					return Path(first)
		except Exception as error:
			logger.debug('Unable to locate Chrome in PATH: %s', error)
		return None

	async def start(self) -> bool:
		"""Start Chromium with remote debugging enabled."""
		if self._running:
			return True

		chrome_path = self._find_chrome()
		if chrome_path is None:
			return await self._start_with_playwright()

		user_data_dir = Path.home() / '.cache' / 'aibrowser' / 'chromium_profile'
		user_data_dir.mkdir(parents=True, exist_ok=True)

		args: list[str] = [
			str(chrome_path),
			f'--remote-debugging-port={self.port}',
			f'--user-data-dir={user_data_dir}',
			'--no-first-run',
			'--no-default-browser-check',
			'--disable-background-timer-throttling',
			'--disable-renderer-backgrounding',
			'--disable-backgrounding-occluded-windows',
			'--disable-features=TranslateUI',
			'--disable-extensions',
			'--disable-infobars',
			'--disable-notifications',
			'--disable-popup-blocking',
			'--disable-dev-shm-usage',
			'--force-device-scale-factor=1',
			'--window-size=1920,1080',
		]
		if self.headless:
			args.append('--headless=new')

		self._chrome_process = subprocess.Popen(
			args,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		)

		await self._wait_for_endpoint()
		self._endpoint = f'http://localhost:{self.port}'
		self._running = True
		return True

	async def _start_with_playwright(self) -> bool:
		"""Launch Playwright's bundled Chromium."""
		try:
			from playwright.async_api import async_playwright

			self._playwright = await async_playwright().start()
			self._playwright_browser = await self._playwright.chromium.launch(
				headless=self.headless,
				args=[f'--remote-debugging-port={self.port}'],
			)
			await self._wait_for_endpoint()
			self._endpoint = f'http://localhost:{self.port}'
			self._running = True
			return True
		except Exception as error:  # noqa: BLE001
			logger.error('Failed to start Chromium via Playwright: %s', error, exc_info=True)
			return False

	async def _wait_for_endpoint(self, retries: int = 30, delay: float = 1.0) -> None:
		version_url = f'http://localhost:{self.port}/json/version'
		for attempt in range(retries):
			try:
				async with aiohttp.ClientSession() as session:
					async with session.get(version_url, timeout=aiohttp.ClientTimeout(total=2)) as response:
						if response.status == 200:
							return
			except Exception:
				pass
			await asyncio.sleep(delay)
		raise TimeoutError(f'CDP endpoint not reachable after {retries * delay:.0f} seconds')

	@property
	def endpoint(self) -> Optional[str]:
		return self._endpoint

	async def websocket_url(self) -> Optional[str]:
		if not self._running or self._endpoint is None:
			return None
		version_url = f'{self._endpoint}/json/version'
		try:
			async with aiohttp.ClientSession() as session:
				async with session.get(version_url) as response:
					if response.status == 200:
						data = await response.json()
						return data.get('webSocketDebuggerUrl')
		except Exception as error: 
			logger.error('Failed to fetch websocket debugger URL: %s', error, exc_info=True)
		return None

	async def stop(self) -> None:
		if not self._running:
			return
		if self._playwright_browser is not None:
			try:
				await self._playwright_browser.close()
			except Exception:
				pass
			self._playwright_browser = None
		if self._playwright is not None:
			try:
				await self._playwright.stop()
			except Exception:
				pass
			self._playwright = None
		if self._chrome_process is not None:
			try:
				self._chrome_process.terminate()
				self._chrome_process.wait(timeout=5)
			except subprocess.TimeoutExpired:
				self._chrome_process.kill()
				self._chrome_process.wait(timeout=5)
			except Exception:
				pass
			self._chrome_process = None
		self._endpoint = None
		self._running = False

	async def is_running(self) -> bool:
		if not self._running:
			return False
		if self._playwright_browser is not None:
			return self._playwright_browser.is_connected()
		if self._chrome_process is not None:
			return self._chrome_process.poll() is None
		return False

