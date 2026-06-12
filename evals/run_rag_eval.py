"""Run the RAG eval against a live gateway.

  uv run python evals/run_rag_eval.py                       # retrieval metrics
  uv run python evals/run_rag_eval.py --generate --model llama3.2 \
      --judge ollama/llama3.1:8b                            # + RAGAS metrics
  uv run python evals/run_rag_eval.py --baseline evals/baseline.json

Exit code 1 on baseline regressions. Requires the gateway, Qdrant, and Ollama
running. RAGAS metrics need the `evals` dependency group installed.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from harness import (
    ForgeEvalClient,
    compare_to_baseline,
    evaluate_retrieval,
    load_all_datasets,
)


def run_generation(client, items, model: str, judge: str, top_k: int) -> dict:
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import AnswerRelevancy, Faithfulness
    from ragas.run_config import RunConfig

    judge_model = judge.removeprefix("ollama/")
    llm = LangchainLLMWrapper(ChatOllama(model=judge_model, temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OllamaEmbeddings(model="nomic-embed-text"))

    rows = []
    for item in items:
        answer, contexts = client.rag_answer(item["question"], model, top_k)
        rows.append(
            {
                "user_input": item["question"],
                "response": answer,
                "retrieved_contexts": contexts,
                "reference": item["ground_truth"],
            }
        )
    result = evaluate(
        dataset=EvaluationDataset.from_list(rows),
        metrics=[Faithfulness(llm=llm), AnswerRelevancy(llm=llm, embeddings=embeddings)],
        # Ollama serializes requests — parallel jobs just starve and time out
        run_config=RunConfig(timeout=600, max_workers=1),
    )
    frame = result.to_pandas()[["faithfulness", "answer_relevancy"]]
    scored = frame.dropna()
    return {
        "model": model,
        "judge": judge,
        "items_scored": int(len(scored)),
        "items_failed": int(len(frame) - len(scored)),
        "faithfulness": round(float(scored["faithfulness"].mean()), 4),
        "answer_relevancy": round(float(scored["answer_relevancy"].mean()), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--key", default="change-me")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--judge", default="ollama/llama3.1:8b")
    parser.add_argument(
        "--sample", type=int, default=None, help="judge only the first N items (cost control)"
    )
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()

    items = load_all_datasets()
    client = ForgeEvalClient(args.base_url, args.key)
    print(f"eval team: {client.team} | items: {len(items)}")
    client.ingest(items)

    retrieval, per_item = evaluate_retrieval(client, items, args.top_k)
    report = {
        "ts": int(time.time()),
        "top_k": args.top_k,
        "retrieval": retrieval,
        "per_item": [asdict(r) for r in per_item],
    }
    if args.generate:
        judged = items[: args.sample] if args.sample else items
        report["generation"] = run_generation(client, judged, args.model, args.judge, args.top_k)

    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    out = reports_dir / "latest.json"
    out.write_text(json.dumps(report, indent=2))

    print(json.dumps({k: v for k, v in report.items() if k != "per_item"}, indent=2))
    misses = [r for r in per_item if r.rank != 1]
    for r in misses:
        print(f"  miss: {r.item_id} rank={r.rank} leaked={r.leaked}")

    if args.baseline and args.baseline.exists():
        regressions = compare_to_baseline(report, json.loads(args.baseline.read_text()))
        if regressions:
            print("REGRESSIONS vs baseline:")
            for regression in regressions:
                print(f"  {regression}")
            return 1
        print("baseline held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
