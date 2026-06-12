"""Compare chunking strategies with the eval harness (ADR-0015).

  uv run python evals/compare_chunking.py

Each strategy gets a fresh eval team (own collection), the same gold corpus,
and the same questions. Deterministic retrieval metrics only — fast enough to
run on every chunking change.
"""

import argparse
import json
import sys

from harness import ForgeEvalClient, evaluate_retrieval, load_all_datasets

STRATEGIES = ["fixed", "sentence", "paragraph"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--key", default="change-me")
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args()

    items = load_all_datasets()
    table: dict[str, dict] = {}
    for strategy in STRATEGIES:
        client = ForgeEvalClient(args.base_url, args.key)
        client.ingest(items, chunking=strategy)
        summary, _ = evaluate_retrieval(client, items, args.top_k)
        table[strategy] = summary
        print(f"{strategy:>10}: {json.dumps(summary)}")

    best = max(table, key=lambda s: (table[s]["topic_in_top1"], table[s]["topic_recall"]))
    print(f"\nbest by topic_in_top1/topic_recall: {best}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
