from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ReportVariableHelpService:
    """Approved, report-scoped variable definitions (including duplicate entries)."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(__file__).resolve().parent.parent / 'data' / 'help' / 'report_variable_descriptions.json'
        self.rows: list[dict[str, str]] = json.loads(Path(path).read_text(encoding='utf-8'))

    @staticmethod
    def _normalize(value: Any) -> str:
        return re.sub(r'[^a-z0-9]+', '_', str(value or '').lower()).strip('_')

    def for_report(self, report_name: str) -> list[dict[str, str]]:
        """All approved entries; preserves document order and duplicates."""
        report_key = self._normalize(report_name)
        return [r for r in self.rows if self._normalize(r.get('report_name')) == report_key]

    def find_matches(self, report_name: str, question: str) -> list[dict[str, str]]:
        question_text = str(question or '').lower()
        matched: list[dict[str, str]] = []
        for row in self.for_report(report_name):
            variable = str(row['variable_name']).lower()
            pattern = r'(?<![a-z0-9_])/?' + re.escape(variable).replace('_', r'[ _]') + r'(?![a-z0-9_])'
            if re.search(pattern, question_text):
                matched.append(row)
        return matched

    @staticmethod
    def wants_catalog(question: str) -> bool:
        """True when the user requests an overview/list of report variables."""
        q = ' '.join(str(question or '').lower().split())
        if not re.search(r'\b(variables?|parameters?|input fields?)\b', q):
            return False
        patterns = (
            r'\b(?:what|which|list|show|name|give|tell|describe|explain|all|every|available|how many)\b.{0,85}\b(?:variables?|parameters?|input fields?)\b',
            r'\b(?:variables?|parameters?|input fields?)\b.{0,45}\b(?:here|available|in (?:this|the) report|can i use|are there|used)\b',
        )
        return any(re.search(p, q) for p in patterns)

    @staticmethod
    def wants_application_guide(question: str) -> bool:
        q = ' '.join(str(question or '').lower().split())
        return bool(re.search(
            r'\b(?:application|app|chatbot|software|interface|sidebar|dropdown|new chat|'
            r'report selection|select (?:a |the )?report|how (?:does|do|can|to)|'
            r'how to use|getting started|guide|instructions|ask mode|help mode|'
            r'report terms|navigate|sign in|log in|login|use the system)\b', q,
        ))
