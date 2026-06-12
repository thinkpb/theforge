"""PII scrubbing at the outbound boundary (ADR-0007).

Detected entities (names, SSNs, emails, phones, …) are replaced with type
markers like <PERSON> before a prompt leaves the gateway for an upstream
provider. On by default; disabling is a deliberate, visible configuration
choice and shows up in the audit trail as pii_redactions = NULL.

Presidio's analyzer is synchronous and CPU-bound, so scrubbing runs in a
worker thread to keep the event loop free. Engines are built once per process
(model load is expensive) and shared across app instances.
"""

import asyncio
from functools import lru_cache
from typing import Any

from fastapi import Request
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine


@lru_cache
def _engines(spacy_model: str) -> tuple[AnalyzerEngine, AnonymizerEngine]:
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": spacy_model}],
        }
    )
    analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
    return analyzer, AnonymizerEngine()


class PIIScrubber:
    def __init__(
        self,
        enabled: bool,
        allow_list: list[str] | None = None,
        entities: list[str] | None = None,
        spacy_model: str = "en_core_web_lg",
    ):
        self.enabled = enabled
        self.spacy_model = spacy_model
        # Small NER models false-positive on domain vocabulary (e.g. drug names
        # tagged as PERSON). Operators allow-list known-safe terms rather than
        # losing clinical/legal content to over-scrubbing.
        self.allow_list = allow_list or []
        # Curated entity types (Settings.pii_entities): scrub identifiers, not
        # every date-like string — DATE_TIME destroys facts (ADR-0012).
        self.entities = entities

    async def scrub_text(self, text: str) -> tuple[str, int | None]:
        """Scrub a single text (RAG ingestion/search path). None = disabled."""
        if not self.enabled:
            return text, None
        return await asyncio.to_thread(self._scrub_text, text)

    async def scrub_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Return (scrubbed messages, redaction count).

        Count semantics for the audit trail: None means scrubbing was disabled,
        0 means it ran and found nothing — different compliance statements.
        """
        if not self.enabled:
            return messages, None
        total = 0
        scrubbed: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                text, count = await asyncio.to_thread(self._scrub_text, content)
                total += count
                message = {**message, "content": text}
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text, count = await asyncio.to_thread(self._scrub_text, part["text"])
                        total += count
                        part = {**part, "text": text}
                    parts.append(part)
                message = {**message, "content": parts}
            scrubbed.append(message)
        return scrubbed, total

    def _scrub_text(self, text: str) -> tuple[str, int]:
        analyzer, anonymizer = _engines(self.spacy_model)
        results = analyzer.analyze(
            text=text, language="en", allow_list=self.allow_list, entities=self.entities
        )
        if not results:
            return text, 0
        anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
        return anonymized.text, len(results)


def get_pii_scrubber(request: Request) -> PIIScrubber:
    return request.app.state.pii_scrubber
