"""Utilities for composing structured prompts from template files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / 'prompts'


def _load_template(name: str) -> str:
	template_path = PROMPTS_DIR / name
	if not template_path.exists():
		raise FileNotFoundError(f'Missing prompt template: {template_path}')
	return template_path.read_text(encoding='utf-8')


def _clean(text: str | None, fallback: str = '') -> str:
	if not text:
		return fallback
	cleaned = str(text).strip()
	return cleaned or fallback


@dataclass
class StructuredPromptBuilder:
	base_prompt: str
	search_engine: str

	def build(self) -> str:
		template = _load_template('system.md')
		return template.format(
			search_engine=self.search_engine,
			base_prompt_section=_clean(self.base_prompt),
		).strip()


@dataclass
class ObservationPromptBuilder:
	search_engine: str

	def build(self, *, task: str, tab_summary: str, extra_context: str = '') -> str:
		template = _load_template('observation.md')
		return template.format(
			task=_clean(task),
			tab_summary=_clean(tab_summary, 'No active browser state available yet.'),
			search_engine=self.search_engine,
			extra_context=_clean(extra_context),
		).strip()


@dataclass
class AnswerPromptBuilder:
	template_name: str = 'answer.md'

	def build(self, *, narration: str, action: str, result: str) -> str:
		template = _load_template(self.template_name)
		return template.format(
			narration=_clean(narration),
			action=_clean(action),
			result=_clean(result),
		).strip()

