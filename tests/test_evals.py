"""Eval harness unit tests — the deterministic parts run in CI; actual eval
runs need live infrastructure and happen on demand (docs/TESTING.md)."""

import re

import pytest
from evals.harness import compare_to_baseline, load_all_datasets, mrr


def test_datasets_load_and_validate():
    items = load_all_datasets()
    assert len(items) >= 16
    assert {i["vertical"] for i in items} == {"healthcare", "legal"}


def test_dataset_pii_is_synthetic_format_only():
    """Repo rule: synthetic PII only — and SSNs must be realistic-format
    (Presidio invalidates obvious fakes like 123-45-6789, see ADR-0007)."""
    for item in load_all_datasets():
        for ssn in re.findall(r"\b\d{3}-\d{2}-\d{4}\b", item["doc_text"]):
            assert ssn != "123-45-6789"
            digits = ssn.replace("-", "")
            assert digits != "123456789"


def test_every_should_not_contain_actually_appears_in_doc():
    """A leak check that can't fire is a false sense of safety."""
    for item in load_all_datasets():
        for needle in item["should_not_contain"]:
            assert needle in item["doc_text"], f"{item['id']}: {needle!r} not in doc"


def test_mrr():
    assert mrr([1, 1, 1]) == 1.0
    assert mrr([2]) == 0.5
    assert mrr([None]) == 0.0
    assert mrr([1, None]) == 0.5  # misses count against the mean
    assert mrr([]) == 0.0


def test_baseline_comparison_flags_drops_and_leaks():
    baseline = {"retrieval": {"mrr": 0.9, "pii_leaks": 0}}
    held = {"retrieval": {"mrr": 0.88, "pii_leaks": 0}}
    assert compare_to_baseline(held, baseline) == []

    dropped = {"retrieval": {"mrr": 0.7, "pii_leaks": 0}}
    assert any("mrr" in r for r in compare_to_baseline(dropped, baseline))

    leaked = {"retrieval": {"mrr": 0.9, "pii_leaks": 2}}
    assert any("pii_leaks" in r for r in compare_to_baseline(leaked, baseline))


def test_baseline_comparison_ignores_missing_sections():
    baseline = {"retrieval": {"mrr": 0.9}, "generation": {"faithfulness": 0.8}}
    report = {"retrieval": {"mrr": 0.9}}  # generation not run this time
    assert compare_to_baseline(report, baseline) == []


@pytest.mark.parametrize("path_glob", ["healthcare", "legal"])
def test_questions_are_answerable_from_their_doc(path_glob):
    """expected_topics must appear in the source doc — the retrieval target
    has to actually contain the answer."""
    for item in load_all_datasets():
        if item["vertical"] != path_glob:
            continue
        assert any(
            topic.lower() in item["doc_text"].lower() for topic in item["expected_topics"]
        ), item["id"]