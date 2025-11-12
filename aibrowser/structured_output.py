"""Parse structured Gemini responses into actionable sections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern

SECTION_REGEX: Pattern[str] = re.compile(
	r'^\s*(Narration|Action|Result|Thinking|Thought|Step|Evaluate|Action_Name|Action_Input)\s*[:\-]\s*(.*)$',
	re.IGNORECASE,
)


@dataclass
class StructuredAgentResponse:
	raw_text: str
	narration: List[str] = field(default_factory=list)
	actions: List[str] = field(default_factory=list)
	results: List[str] = field(default_factory=list)
	thinking: List[str] = field(default_factory=list)
	steps: List[str] = field(default_factory=list)
	evaluate: List[str] = field(default_factory=list)
	action_name: str | None = None
	action_input: str | None = None

	def best_message(self) -> Optional[str]:
		for segment in (self.results, self.narration, self.thinking, self.steps, self.actions):
			if segment:
				return segment[-1]
		return None


def _dedupe(values: List[str]) -> List[str]:
	seen: set[str] = set()
	order: List[str] = []
	for value in values:
		if value not in seen:
			seen.add(value)
			order.append(value)
	return order


def parse_sections(text: str, pattern: Pattern[str] = SECTION_REGEX) -> Dict[str, List[str]]:
	sections: Dict[str, List[str]] = {}
	current_key: Optional[str] = None
	buffer: List[str] = []

	def flush() -> None:
		nonlocal buffer, current_key
		if current_key and buffer:
			content = ' '.join(part.strip() for part in buffer if part.strip())
			if content:
				sections.setdefault(current_key, []).append(content)
		buffer = []

	for raw_line in text.splitlines():
		line = raw_line.strip()
		if not line:
			flush()
			current_key = None
			continue

		match = pattern.match(line)
		if match:
			flush()
			current_key = match.group(1).lower()
			payload = match.group(2).strip()
			buffer = [payload] if payload else []
		elif current_key:
			buffer.append(line)

	flush()
	return sections


def parse_structured_response(text: str, pattern: Pattern[str] = SECTION_REGEX) -> StructuredAgentResponse:
	sections = parse_sections(text, pattern)
	response = StructuredAgentResponse(raw_text=text)
	response.narration = sections.get('narration', [])
	response.actions = sections.get('action', [])
	response.results = sections.get('result', [])
	response.thinking = sections.get('thinking', []) + sections.get('thought', [])
	response.steps = sections.get('step', [])
	response.evaluate = sections.get('evaluate', [])

	action_names = sections.get('action_name', [])
	if action_names:
		response.action_name = action_names[-1].strip()
	action_inputs = sections.get('action_input', [])
	if action_inputs:
		response.action_input = action_inputs[-1].strip()

	return response


def extract_narrations(text: str, pattern: Pattern[str] = SECTION_REGEX) -> List[str]:
	sections = parse_sections(text, pattern)
	entries: List[str] = []
	for key in ('narration', 'action', 'result'):
		for value in sections.get(key, []):
			cleaned = value.strip()
			if cleaned:
				entries.append(cleaned)
	if entries:
		return _dedupe(entries)

	lowered = text.lower()
	tagged: List[str] = []
	if '[memory]' in lowered:
		tagged.append(re.sub(r'^.*?\[memory\]\s*', '', text, flags=re.IGNORECASE).strip())
	if '[action]' in lowered:
		tagged.append(re.sub(r'^.*?\[action\]\s*', '', text, flags=re.IGNORECASE).strip())
	if '[result]' in lowered or '[success]' in lowered:
		tagged.append(
			re.sub(r'^.*?\[(result|success)\]\s*', '', text, flags=re.IGNORECASE).strip()
		)
	tagged = [entry for entry in tagged if entry]
	return _dedupe(entries + tagged) if tagged else _dedupe(entries)

