"""Configuration management for the AI-controlled browser."""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()


def _parse_float(name: str, default: float) -> Tuple[float, bool]:
	"""Return environment variable as float when possible, falling back to default.	"""
	value = os.getenv(name)
	if value is None or value.strip() == '':
		return default, True
	try:
		return float(value), False
	except ValueError:
		logger.warning('Ignoring invalid float for %s: %s', name, value)
		return default, True


def _parse_int(name: str, default: int) -> Tuple[int, bool]:
	"""Return environment variable as int when possible, falling back to default."""
	value = os.getenv(name)
	if value is None or value.strip() == '':
		return default, True
	try:
		return int(value), False
	except ValueError:
		logger.warning('Ignoring invalid integer for %s: %s', name, value)
		return default, True


class Config:
	"""Centralized configuration for LLM and browser settings."""

	LLM_PROVIDER: str = os.getenv('LLM_PROVIDER', 'gemini').strip().lower() or 'gemini'
	if LLM_PROVIDER not in {'gemini', 'claude', 'openai', 'none'}:
		logger.warning("Unsupported LLM_PROVIDER '%s', falling back to 'gemini'", LLM_PROVIDER)
		LLM_PROVIDER = 'gemini'

	GEMINI_API_KEY: str = os.getenv('GEMINI_API_KEY', '')
	GEMINI_MODEL: str = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')

	# Robust parsing with sane defaults so misconfigured env vars do not crash startup.
	_GEMINI_TEMPERATURE, _ = _parse_float('GEMINI_TEMPERATURE', 0.3)
	GEMINI_TEMPERATURE: float = _GEMINI_TEMPERATURE

	_GEMINI_MAX_OUTPUT_TOKENS, _ = _parse_int('GEMINI_MAX_OUTPUT_TOKENS', 8000)
	GEMINI_MAX_OUTPUT_TOKENS: int = _GEMINI_MAX_OUTPUT_TOKENS

	_GEMINI_TOP_P, used_default_top_p = _parse_float('GEMINI_TOP_P', 0.0)
	GEMINI_TOP_P: Optional[float] = None if used_default_top_p else _GEMINI_TOP_P

	_GEMINI_TOP_K, used_default_top_k = _parse_int('GEMINI_TOP_K', 0)
	GEMINI_TOP_K: Optional[int] = None if used_default_top_k else _GEMINI_TOP_K

	CLAUDE_API_KEY: str = os.getenv('ANTHROPIC_API_KEY', '') or os.getenv('CLAUDE_API_KEY', '')
	CLAUDE_MODEL: str = os.getenv('CLAUDE_MODEL', 'claude-sonnet-4-1')  # Valid models: claude-sonnet-4-0, claude-3-5-sonnet-20241022, claude-3-5-sonnet-latest

	# Robust parsing with sane defaults so misconfigured env vars do not crash startup.
	_CLAUDE_TEMPERATURE, _ = _parse_float('CLAUDE_TEMPERATURE', 0.3)
	CLAUDE_TEMPERATURE: float = _CLAUDE_TEMPERATURE

	_CLAUDE_MAX_TOKENS, _ = _parse_int('CLAUDE_MAX_TOKENS', 4096)  # Reduced from 8192 for faster responses
	CLAUDE_MAX_TOKENS: int = _CLAUDE_MAX_TOKENS

	_CLAUDE_TOP_P, used_default_claude_top_p = _parse_float('CLAUDE_TOP_P', 0.0)
	CLAUDE_TOP_P: Optional[float] = None if used_default_claude_top_p else _CLAUDE_TOP_P

	_CLAUDE_TIMEOUT, _ = _parse_float('CLAUDE_TIMEOUT', 60.0)  # Timeout in seconds
	CLAUDE_TIMEOUT: float = _CLAUDE_TIMEOUT

	_CLAUDE_MAX_RETRIES, _ = _parse_int('CLAUDE_MAX_RETRIES', 2)  # Reduced from 5 for faster failure recovery
	CLAUDE_MAX_RETRIES: int = _CLAUDE_MAX_RETRIES

	OPENAI_API_KEY: str = os.getenv('OPENAI_API_KEY', '')
	OPENAI_MODEL: str = os.getenv('OPENAI_MODEL', 'gpt-5-nano')  # Valid models: gpt-4o, gpt-4-turbo, gpt-4o-mini, o1-preview, o3-mini

	# Robust parsing with sane defaults so misconfigured env vars do not crash startup.
	_OPENAI_TEMPERATURE, _ = _parse_float('OPENAI_TEMPERATURE', 0.2)
	OPENAI_TEMPERATURE: float = _OPENAI_TEMPERATURE

	_OPENAI_MAX_TOKENS, _ = _parse_int('OPENAI_MAX_TOKENS', 4096)
	OPENAI_MAX_TOKENS: int = _OPENAI_MAX_TOKENS

	_OPENAI_TOP_P, used_default_openai_top_p = _parse_float('OPENAI_TOP_P', 0.0)
	OPENAI_TOP_P: Optional[float] = None if used_default_openai_top_p else _OPENAI_TOP_P

	_OPENAI_FREQUENCY_PENALTY, _ = _parse_float('OPENAI_FREQUENCY_PENALTY', 0.3)
	OPENAI_FREQUENCY_PENALTY: float = _OPENAI_FREQUENCY_PENALTY  # Default 0.3 (ChatOpenAI default)

	DEFAULT_SEARCH_ENGINE: str = os.getenv('DEFAULT_SEARCH_ENGINE', 'google').strip().lower() or 'google'

	SYSTEM_PROMPT_FILE: str | None = os.getenv('SYSTEM_PROMPT_FILE') or None
	SYSTEM_PROMPT_TEXT: str | None = os.getenv('SYSTEM_PROMPT') or None

	DEFAULT_SYSTEM_PROMPT: str = (
		'You control a real Chromium browser to help the user with web tasks only.\n'
		"Your job is to understand the user's browser task, decide the next best browser action, and describe it clearly.\n"
		'You never answer questions that are unrelated to using the browser or the content of the current web pages.\n'
		'Short greetings are fine, but always steer the conversation back to what to do in the browser.'
	)

	ELEVENLABS_API_KEY: str = os.getenv('ELEVENLABS_API_KEY', '')
	DEEPGRAM_API_KEY: str = os.getenv('DEEPGRAM_API_KEY', '')
	ELEVENLABS_VOICE_ID: str = os.getenv('ELEVENLABS_VOICE_ID', '21m00Tcm4TlvDq8ikWAM')  # Default: Rachel
	DEEPGRAM_LANGUAGE: str = os.getenv('DEEPGRAM_LANGUAGE', 'en-US')

	# Browser highlighting customization
	HIGHLIGHT_ELEMENTS: bool = os.getenv('HIGHLIGHT_ELEMENTS', 'true').lower() in {'true', '1', 'yes'}
	DOM_HIGHLIGHT_ELEMENTS: bool = os.getenv('DOM_HIGHLIGHT_ELEMENTS', 'false').lower() in {'true', '1', 'yes'}
	INTERACTION_HIGHLIGHT_COLOR: str = os.getenv('INTERACTION_HIGHLIGHT_COLOR', 'rgb(255, 127, 39)').strip()  # Orange default
	_INTERACTION_HIGHLIGHT_DURATION, _ = _parse_float('INTERACTION_HIGHLIGHT_DURATION', 1.0)
	INTERACTION_HIGHLIGHT_DURATION: float = _INTERACTION_HIGHLIGHT_DURATION

	# Screenshot saving configuration
	SAVE_HIGHLIGHT_SCREENSHOTS: bool = os.getenv('SAVE_HIGHLIGHT_SCREENSHOTS', 'false').lower() in {'true', '1', 'yes'}
	SCREENSHOT_DIR: str = os.getenv('SCREENSHOT_DIR', './screenshots').strip()
	_HIGHLIGHT_SCREENSHOT_DELAY, _ = _parse_float('HIGHLIGHT_SCREENSHOT_DELAY', 0.5)
	HIGHLIGHT_SCREENSHOT_DELAY: float = _HIGHLIGHT_SCREENSHOT_DELAY  # Delay in seconds to wait for highlight to appear before screenshot

	@classmethod
	def validate(cls) -> bool:
		"""Ensure required keys exist before running."""
		if cls.LLM_PROVIDER == 'gemini' and not cls.GEMINI_API_KEY:
			logger.error('Missing GEMINI_API_KEY. Set it in your environment.')
			return False
		if cls.LLM_PROVIDER == 'claude' and not cls.CLAUDE_API_KEY:
			logger.error('Missing ANTHROPIC_API_KEY or CLAUDE_API_KEY. Set it in your environment.')
			return False
		if cls.LLM_PROVIDER == 'openai' and not cls.OPENAI_API_KEY:
			logger.error('Missing OPENAI_API_KEY. Set it in your environment.')
			return False
		return True

	@classmethod
	def validate_voice(cls) -> bool:
		"""Validate voice integration API keys."""
		if not cls.ELEVENLABS_API_KEY:
			logger.error('Missing ELEVENLABS_API_KEY. Set it in your environment.')
			return False
		if not cls.DEEPGRAM_API_KEY:
			logger.error('Missing DEEPGRAM_API_KEY. Set it in your environment.')
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
		elif cls.LLM_PROVIDER == 'claude':
			print(f'  Claude Model: {cls.CLAUDE_MODEL}')
			print(f'  Claude API Key: {"set" if bool(cls.CLAUDE_API_KEY) else "missing"}')
		elif cls.LLM_PROVIDER == 'openai':
			print(f'  OpenAI Model: {cls.OPENAI_MODEL}')
			print(f'  OpenAI API Key: {"set" if bool(cls.OPENAI_API_KEY) else "missing"}')
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
		if cls.ELEVENLABS_API_KEY or cls.DEEPGRAM_API_KEY:
			print(f'  ElevenLabs API Key: {"set" if bool(cls.ELEVENLABS_API_KEY) else "missing"}')
			print(f'  Deepgram API Key: {"set" if bool(cls.DEEPGRAM_API_KEY) else "missing"}')

