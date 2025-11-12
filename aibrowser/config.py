"""Configuration management for the AI-controlled browser."""

from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env if present
load_dotenv()


def _parse_float(name: str) -> Optional[float]:
	"""Return environment variable as float when possible."""
	value = os.getenv(name)
	if value is None or value.strip() == '':
		return None
	try:
		return float(value)
	except ValueError:
		logger.warning('Ignoring invalid float for %s: %s', name, value)
		return None


def _parse_int(name: str) -> Optional[int]:
	"""Return environment variable as int when possible."""
	value = os.getenv(name)
	if value is None or value.strip() == '':
		return None
	try:
		return int(value)
	except ValueError:
		logger.warning('Ignoring invalid integer for %s: %s', name, value)
		return None


class Config:
	"""Centralized configuration for LLM and browser settings."""

	LLM_PROVIDER: str = os.getenv('LLM_PROVIDER', 'gemini').strip().lower() or 'gemini'
	if LLM_PROVIDER not in {'gemini', 'none'}:
		logger.warning("Unsupported LLM_PROVIDER '%s', falling back to 'gemini'", LLM_PROVIDER)
		LLM_PROVIDER = 'gemini'

	GEMINI_API_KEY: str = os.getenv('GEMINI_API_KEY', '')
	GEMINI_MODEL: str = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
	GEMINI_TEMPERATURE: float = float(os.getenv('GEMINI_TEMPERATURE', '0.3'))
	GEMINI_MAX_OUTPUT_TOKENS: int = int(os.getenv('GEMINI_MAX_OUTPUT_TOKENS', '8000'))
	GEMINI_TOP_P: Optional[float] = _parse_float('GEMINI_TOP_P')
	GEMINI_TOP_K: Optional[int] = _parse_int('GEMINI_TOP_K')

	DEFAULT_SEARCH_ENGINE: str = os.getenv('DEFAULT_SEARCH_ENGINE', 'google').strip().lower() or 'google'

	SYSTEM_PROMPT_FILE: str | None = os.getenv('SYSTEM_PROMPT_FILE') or None
	SYSTEM_PROMPT_TEXT: str | None = os.getenv('SYSTEM_PROMPT') or None

	DEFAULT_SYSTEM_PROMPT: str = (
		'You are a focused AI that automates a Chromium browser to help the user.\n'
		'Always narrate what you are doing, use natural first-person language, and keep responses brief.\n'
		'If the task completes without further browser actions, say so explicitly.'
	)

	@classmethod
	def validate(cls) -> bool:
		"""Ensure required keys exist before running."""
		if cls.LLM_PROVIDER == 'gemini' and not cls.GEMINI_API_KEY:
			logger.error('Missing GEMINI_API_KEY. Set it in your environment.')
			return False
		return True

	@classmethod
	def system_prompt(cls) -> str:
		"""Return the configured system prompt."""
		if cls.SYSTEM_PROMPT_FILE:
			try:
				with open(cls.SYSTEM_PROMPT_FILE, 'r', encoding='utf-8') as handle:
					return handle.read()
			except OSError as error:
				logger.warning('Failed to load system prompt from %s: %s', cls.SYSTEM_PROMPT_FILE, error)
		if cls.SYSTEM_PROMPT_TEXT:
			return cls.SYSTEM_PROMPT_TEXT
		return cls.DEFAULT_SYSTEM_PROMPT

	@classmethod
	def log_config(cls) -> None:
		"""Print non-sensitive settings to stdout."""
		print('Configuration:')
		print(f'  LLM Provider: {cls.LLM_PROVIDER}')
		if cls.LLM_PROVIDER == 'gemini':
			print(f'  Gemini Model: {cls.GEMINI_MODEL}')
			print(f'  Gemini API Key: {"set" if bool(cls.GEMINI_API_KEY) else "missing"}')
		print(f'  Default Search Engine: {cls.DEFAULT_SEARCH_ENGINE}')
		print(
			'  System Prompt: '
			+ (
				'file'
				if cls.SYSTEM_PROMPT_FILE
				else 'env'
				if cls.SYSTEM_PROMPT_TEXT
				else 'default'
			)
		)

