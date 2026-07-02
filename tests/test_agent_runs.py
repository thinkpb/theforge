"""Durable agent run record tests (ADR-0022). Metadata-only: the record proves
what the agent did (tools, outcomes, status), never what it said."""

import json

import litellm
import pytest

from tests.conftest import make_litellm_exc

FACT = "The enterprise refund window is 47 days from purchase."


def _toolcall(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def _final(text: str) -> dict:
    return {"role": "assistant", "content": text}


class _FakeResp:
    def __init__(self, message):
        self._message = message

    def model_dump(self):
        return {"choices": [{"message": self._message}]}


@pytest.fixture
def agent_llm(monkeypatch):
    script: list = []

    async def _fake(**kwargs):
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)

    monkeypatch.setattr("forge.agents.runtime.litellm.acompletion", _fake)
    return script


async def test_successful_run_is_persisted_metadata_only(
    client, auth_headers, agent_llm, fake_embeddings
):
    await client.post("/v1/documents", headers=auth_headers, json={"text": FACT})
    agent_llm += [_toolcall("document_search", {"query": "refund"}), _final("47 days.")]

    run = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "refund?"}
    )
    run_id = run.json()["run_id"]

    fetched = await client.get(f"/v1/agents/runs/{run_id}", headers=auth_headers)
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["status"] == "success"
    assert body["agent"] == "research-assistant"
    assert body["num_steps"] >= 1
    # the durable record is metadata-only: step summaries carry no content/args
    tool_steps = [s for s in body["steps"] if s["type"] == "tool_call"]
    assert tool_steps and tool_steps[0]["tool"] == "document_search"
    for step in body["steps"]:
        assert set(step.keys()) == {"type", "tool", "outcome"}
    # no free-text output or arguments anywhere in the persisted record
    assert "47 days" not in json.dumps(body)


async def test_runs_are_listable(client, auth_headers, agent_llm):
    agent_llm += [_final("hi")]
    await client.post("/v1/agents/chat-only/run", headers=auth_headers, json={"input": "hi"})

    listing = await client.get("/v1/agents/runs", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()["data"]) >= 1


async def test_run_is_team_scoped(client, auth_headers, agent_llm):
    agent_llm += [_final("hi")]
    run = await client.post("/v1/agents/chat-only/run", headers=auth_headers, json={"input": "hi"})
    run_id = run.json()["run_id"]

    created = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "t", "team": "other-team"}
    )
    other = {"Authorization": f"Bearer {created.json()['key']}"}
    # another team can't see this run — 404, no existence leak
    assert (await client.get(f"/v1/agents/runs/{run_id}", headers=other)).status_code == 404


async def test_step_limit_run_recorded_as_error(client, auth_headers, agent_llm, fake_embeddings):
    await client.post("/v1/documents", headers=auth_headers, json={"text": FACT})
    agent_llm += [_toolcall("document_search", {"query": f"q{i}"}, f"c{i}") for i in range(10)]

    run = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "loop"}
    )
    body = await client.get(f"/v1/agents/runs/{run.json()['run_id']}", headers=auth_headers)
    record = body.json()
    assert record["status"] == "error"
    assert record["error_type"] == "step_limit"


async def test_provider_error_run_recorded_as_error(client, auth_headers, agent_llm):
    agent_llm += [make_litellm_exc(litellm.exceptions.APIConnectionError)]
    run = await client.post(
        "/v1/agents/chat-only/run", headers=auth_headers, json={"input": "hi"}
    )
    assert run.status_code == 502

    listing = await client.get("/v1/agents/runs", headers=auth_headers)
    latest = listing.json()["data"][0]
    assert latest["status"] == "error"
    assert latest["error_type"] == "APIConnectionError"


async def test_non_openai_error_finishes_the_run(client, auth_headers, agent_llm, monkeypatch):
    """A non-OpenAIError from the runtime (here: unknown model → HTTPException)
    must still finish the run — never leave it stuck 'running' (ADR-0022 inv. #3;
    the review of this milestone found the endpoint only caught OpenAIError)."""
    from fastapi import HTTPException, status

    def _bad_model(*args, **kwargs):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown model")

    monkeypatch.setattr("forge.agents.runtime.resolve_model", _bad_model)
    agent_llm += [_final("unused")]

    run = await client.post(
        "/v1/agents/chat-only/run", headers=auth_headers, json={"input": "test"}
    )
    assert run.status_code == 400  # HTTPException propagates with its status

    listing = await client.get("/v1/agents/runs", headers=auth_headers)
    latest = listing.json()["data"][0]
    assert latest["status"] == "error"  # finished, not stuck 'running'
    assert latest["error_type"] == "HTTPException"


async def test_audit_backpressure_finishes_the_run(client, auth_headers, agent_llm, monkeypatch):
    """If the audit buffer is full mid-run (AuditBufferFull), the run still
    finishes as error rather than hanging in 'running'."""
    from forge.audit import AuditBufferFull

    def _full(self, record):  # class-level patch → receives self
        raise AuditBufferFull

    monkeypatch.setattr("forge.agents.runtime.AuditBuffer.put", _full, raising=False)
    agent_llm += [_final("unused")]

    # the ASGI test transport re-raises app exceptions; the point is that
    # finish_run ran (in the endpoint's except) before the re-raise
    with pytest.raises(AuditBufferFull):
        await client.post(
            "/v1/agents/chat-only/run", headers=auth_headers, json={"input": "test"}
        )

    listing = await client.get("/v1/agents/runs", headers=auth_headers)
    latest = listing.json()["data"][0]
    assert latest["status"] == "error"  # finished, not stuck 'running'
