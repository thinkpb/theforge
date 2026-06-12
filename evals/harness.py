"""RAG eval harness (TESTING.md Layer 2, ADR-0014).

Black-box by design: drives a running Forge gateway over HTTP exactly like a
user, so the eval covers the real pipeline — auth, scrubbing, chunking,
embedding, retrieval, injection — not a unit under glass.

Two metric families:
- Retrieval (deterministic, no judge): hit@1, hit@k, MRR, and PII-leak checks
  against each item's should_not_contain list. Exact numbers, comparable
  across runs.
- Generation (LLM-judged, optional): RAGAS faithfulness + answer relevancy.
  Scores are regression lines, not absolute truths — track deltas.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

DATASETS_DIR = Path(__file__).parent / "datasets"
REQUIRED_FIELDS = {
    "id",
    "vertical",
    "doc_title",
    "doc_text",
    "question",
    "ground_truth",
    "expected_topics",
    "should_not_contain",
}


def load_dataset(path: Path) -> list[dict[str, Any]]:
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for item in items:
        missing = REQUIRED_FIELDS - item.keys()
        if missing:
            raise ValueError(f"{path.name}:{item.get('id', '?')} missing fields {missing}")
    return items


def load_all_datasets() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(DATASETS_DIR.glob("*.jsonl")):
        items.extend(load_dataset(path))
    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate item ids across datasets")
    return items


def mrr(ranks: list[int | None]) -> float:
    """Mean reciprocal rank; None = gold doc not retrieved at all."""
    if not ranks:
        return 0.0
    return sum(1.0 / rank for rank in ranks if rank is not None) / len(ranks)


@dataclass
class RetrievalResult:
    item_id: str
    rank: int | None  # 1-based rank of the gold document, None if absent
    leaked: list[str] = field(default_factory=list)


class ForgeEvalClient:
    """Thin client for the gateway, namespaced to a fresh eval team."""

    def __init__(self, base_url: str, master_key: str):
        self._client = httpx.Client(base_url=base_url, timeout=120)
        self._master = {"Authorization": f"Bearer {master_key}"}
        team = f"eval-{int(time.time())}"
        created = self._client.post(
            "/v1/keys",
            headers=self._master,
            json={"name": "rag-eval", "team": team},
        )
        created.raise_for_status()
        self.team = team
        self._headers = {"Authorization": f"Bearer {created.json()['key']}"}

    def ingest(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            response = self._client.post(
                "/v1/documents",
                headers=self._headers,
                json={"text": item["doc_text"], "title": item["doc_title"]},
            )
            response.raise_for_status()

    def search(self, question: str, top_k: int) -> list[dict[str, Any]]:
        response = self._client.post(
            "/v1/search", headers=self._headers, json={"query": question, "limit": top_k}
        )
        response.raise_for_status()
        return response.json()["data"]

    def rag_answer(self, question: str, model: str, top_k: int) -> tuple[str, list[str]]:
        response = self._client.post(
            "/v1/chat/completions",
            headers=self._headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": question}],
                "rag": {"top_k": top_k},
                "temperature": 0,
            },
        )
        response.raise_for_status()
        body = response.json()
        answer = body["choices"][0]["message"]["content"]
        contexts = [r["text"] for r in self.search(question, top_k)]
        return answer, contexts


def evaluate_retrieval(
    client: ForgeEvalClient, items: list[dict[str, Any]], top_k: int
) -> tuple[dict[str, Any], list[RetrievalResult]]:
    results: list[RetrievalResult] = []
    for item in items:
        hits = client.search(item["question"], top_k)
        rank = next(
            (i + 1 for i, hit in enumerate(hits) if hit.get("title") == item["doc_title"]),
            None,
        )
        retrieved_text = " ".join(hit["text"] for hit in hits)
        leaked = [s for s in item["should_not_contain"] if s in retrieved_text]
        results.append(RetrievalResult(item["id"], rank, leaked))

    ranks = [r.rank for r in results]
    summary = {
        "items": len(results),
        "hit_at_1": sum(1 for r in ranks if r == 1) / len(ranks),
        f"hit_at_{top_k}": sum(1 for r in ranks if r is not None) / len(ranks),
        "mrr": round(mrr(ranks), 4),
        "pii_leaks": sum(len(r.leaked) for r in results),
    }
    return summary, results


def compare_to_baseline(
    report: dict[str, Any], baseline: dict[str, Any], max_drop: float = 0.05
) -> list[str]:
    """Return regression descriptions; empty list means the line held."""
    regressions = []
    for section in ("retrieval", "generation"):
        current, previous = report.get(section), baseline.get(section)
        if not current or not previous:
            continue
        for metric, old_value in previous.items():
            new_value = current.get(metric)
            if not isinstance(old_value, int | float) or not isinstance(new_value, int | float):
                continue
            if metric == "pii_leaks":
                if new_value > old_value:
                    regressions.append(f"{section}.{metric}: {old_value} -> {new_value}")
            elif new_value < old_value - max_drop:
                regressions.append(
                    f"{section}.{metric}: {old_value:.3f} -> {new_value:.3f} "
                    f"(drop > {max_drop})"
                )
    return regressions
