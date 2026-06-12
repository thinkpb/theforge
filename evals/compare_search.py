"""Compare dense vs hybrid retrieval with the eval harness (ADR-0016).

  uv run python evals/compare_search.py [--top-k 1]

One corpus ingestion, both query modes. top_k=1 is the discriminating setting:
the keyword dataset's near-duplicate runbooks differ only by exact alert codes.
"""

import argparse
import json
import sys

from harness import ForgeEvalClient, evaluate_retrieval, load_all_datasets

MODES = ["dense", "hybrid"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--key", default="change-me")
    parser.add_argument("--top-k", type=int, default=1)
    args = parser.parse_args()

    items = load_all_datasets()
    client = ForgeEvalClient(args.base_url, args.key)
    client.ingest(items)

    for mode in MODES:
        summary, per_item = evaluate_retrieval(client, items, args.top_k, mode=mode)
        print(f"{mode:>7}: {json.dumps(summary)}")
        for r in per_item:
            if r.rank != 1:
                print(f"         miss: {r.item_id} rank={r.rank}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
