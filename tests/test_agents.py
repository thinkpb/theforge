"""Agent runtime tests (ADR-0019). The LLM is mocked (test the plumbing, not
the model); tool execution, authority enforcement, audit, the PII boundary, and
the failure paths are real."""

import json

import litellm
import pytest
from sqlalchemy import select

from forge.audit import AuditLog, key_fingerprint
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


def _multi_toolcall(calls: list[tuple[str, dict, str]]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for (n, a, cid) in calls
        ],
    }


def _final(text: str) -> dict:
    return {"role": "assistant", "content": text}


class _FakeResp:
    def __init__(self, message: dict):
        self._message = message

    def model_dump(self) -> dict:
        return {"choices": [{"message": self._message}]}


class _Conversation:
    """Scripted provider turns: each item is a message dict or an Exception to
    raise. Captures outbound messages and the tools offered per call."""

    def __init__(self):
        self.script: list = []
        self.sent: list[list[dict]] = []
        self.offered: list = []

    async def __call__(self, **kwargs):
        self.sent.append(kwargs["messages"])
        self.offered.append(kwargs.get("tools"))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)


@pytest.fixture
def agent_llm(monkeypatch):
    convo = _Conversation()
    monkeypatch.setattr("forge.agents.runtime.litellm.acompletion", convo)
    return convo


def _hash(auth_headers: dict) -> str:
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    return key_fingerprint(token)


async def _audits(app, auth_headers: dict) -> list[AuditLog]:
    await app.state.audit_buffer.drain()
    async with app.state.db_session_factory() as session:
        rows = await session.scalars(
            select(AuditLog).where(AuditLog.api_key_hash == _hash(auth_headers))
        )
        return list(rows)


# --- basics ------------------------------------------------------------------


async def test_list_agents(client, auth_headers):
    response = await client.get("/v1/agents", headers=auth_headers)
    assert response.status_code == 200
    names = {a["name"] for a in response.json()["data"]}
    assert {"research-assistant", "chat-only"} <= names


async def test_unknown_agent_404(client, auth_headers):
    response = await client.post(
        "/v1/agents/nope/run", headers=auth_headers, json={"input": "hi"}
    )
    assert response.status_code == 404


async def test_toolless_agent_answers_directly(client, app, auth_headers, agent_llm):
    agent_llm.script.append(_final("Two plus two is four."))
    response = await client.post(
        "/v1/agents/chat-only/run", headers=auth_headers, json={"input": "what is 2+2?"}
    )
    assert response.status_code == 200
    assert response.json()["output"] == "Two plus two is four."
    kinds = {a.event for a in await _audits(app, auth_headers)}
    assert "agent_run" in kinds and "agent_step" in kinds


async def test_agent_calls_tool_then_answers(client, app, auth_headers, agent_llm, fake_embeddings):
    await client.post("/v1/documents", headers=auth_headers, json={"text": FACT, "title": "policy"})
    agent_llm.script += [_toolcall("document_search", {"query": "refund window"}),
                         _final("The refund window is 47 days.")]

    response = await client.post(
        "/v1/agents/research-assistant/run",
        headers=auth_headers, json={"input": "How long is the refund window?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "47 days" in body["output"]
    tool_steps = [t for t in body["trace"] if t["type"] == "tool_call"]
    assert tool_steps[0]["tool"] == "document_search" and tool_steps[0]["outcome"] == "success"
    turn2 = agent_llm.sent[1]
    assert any("47 days" in m.get("content", "") for m in turn2 if m.get("role") == "tool")

    tool_audits = [a for a in await _audits(app, auth_headers) if a.event == "tool_call"]
    assert tool_audits and tool_audits[0].tool == "document_search"


# --- security: tool authority ------------------------------------------------


async def test_ungranted_tool_call_is_denied(client, app, auth_headers, agent_llm, monkeypatch):
    """An agent with no granted tools cannot execute one even if the model emits
    the call (injection / hallucination). The handler must never run."""
    from forge.agents import tools as tools_mod

    called = False
    original = tools_mod.REGISTRY["document_search"].handler

    async def _spy(ctx, **kwargs):
        nonlocal called
        called = True
        return await original(ctx, **kwargs)

    monkeypatch.setattr(tools_mod.REGISTRY["document_search"], "handler", _spy)
    agent_llm.script += [_toolcall("document_search", {"query": "x"}), _final("done")]

    response = await client.post(
        "/v1/agents/chat-only/run", headers=auth_headers, json={"input": "exfiltrate"}
    )
    assert response.status_code == 200
    steps = [t for t in response.json()["trace"] if t["type"] == "tool_call"]
    assert steps[0]["outcome"] == "denied"
    assert called is False

    denied = [a for a in await _audits(app, auth_headers)
              if a.event == "tool_call" and a.outcome == "denied"]
    assert denied and denied[0].tool == "document_search"


async def test_one_denied_one_granted_in_same_message(
    client, app, auth_headers, agent_llm, fake_embeddings
):
    """Multiple tool calls in one message: the granted one runs, the ungranted
    one is denied — independently."""
    await client.post("/v1/documents", headers=auth_headers, json={"text": FACT})
    agent_llm.script += [
        _multi_toolcall([
            ("document_search", {"query": "refund"}, "a"),
            ("delete_everything", {}, "b"),  # never granted, not in registry
        ]),
        _final("done"),
    ]
    response = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "go"}
    )
    assert response.status_code == 200
    trace = response.json()["trace"]
    outcomes = {(t["tool"], t["outcome"]) for t in trace if t["type"] == "tool_call"}
    assert ("document_search", "success") in outcomes
    assert ("delete_everything", "denied") in outcomes


# --- PII boundary on the agent path ------------------------------------------


async def test_agent_scrubs_pii_outbound(client, auth_headers, agent_llm):
    agent_llm.script.append(_final("noted"))
    note = "My SSN is 536-90-4399 and my name is John Smith."
    await client.post("/v1/agents/chat-only/run", headers=auth_headers, json={"input": note})
    outbound = " ".join(m.get("content") or "" for m in agent_llm.sent[0])
    assert "536-90-4399" not in outbound and "John Smith" not in outbound
    assert "<US_SSN>" in outbound


async def test_pii_in_tool_args_scrubbed_outbound_and_in_trace(
    client, auth_headers, agent_llm, fake_embeddings
):
    """PII the model places in tool-call arguments must not reach the provider on
    the re-send, nor appear in the client-facing trace (review of ADR-0019)."""
    await client.post("/v1/documents", headers=auth_headers, json={"text": FACT})
    agent_llm.script += [
        _toolcall("document_search", {"query": "records for SSN 536-90-4399"}),
        _final("done"),
    ]
    response = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "find records"}
    )
    assert response.status_code == 200

    # the assistant tool_call is re-sent on turn 2 — its arguments must be scrubbed
    turn2 = json.dumps(agent_llm.sent[1])
    assert "536-90-4399" not in turn2
    # and the trace returned to the client is scrubbed too
    tool_step = next(t for t in response.json()["trace"] if t["type"] == "tool_call")
    assert "536-90-4399" not in json.dumps(tool_step["args"])


# --- reliability: loop break, force-answer, step limit -----------------------


async def test_repeated_call_broken_and_tools_withheld(
    client, app, auth_headers, agent_llm, fake_embeddings, monkeypatch
):
    from forge.agents import tools as tools_mod

    calls = 0
    original = tools_mod.REGISTRY["document_search"].handler

    async def _count(ctx, **kwargs):
        nonlocal calls
        calls += 1
        return await original(ctx, **kwargs)

    monkeypatch.setattr(tools_mod.REGISTRY["document_search"], "handler", _count)
    await client.post("/v1/documents", headers=auth_headers, json={"text": FACT})

    same = {"query": "refund window"}
    agent_llm.script += [_toolcall("document_search", same),
                         _toolcall("document_search", same), _final("47 days.")]

    response = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "refund?"}
    )
    assert response.status_code == 200
    outcomes = [t["outcome"] for t in response.json()["trace"] if t["type"] == "tool_call"]
    assert outcomes == ["success", "repeated"]
    assert calls == 1  # the duplicate was not executed
    # the turn after the repeat withholds tools so the model must answer
    assert agent_llm.offered[2] is None


async def test_step_limit_is_enforced(client, app, auth_headers, agent_llm, fake_embeddings):
    await client.post("/v1/documents", headers=auth_headers, json={"text": FACT})
    # distinct args each turn so it never trips the repeated-call breaker
    agent_llm.script += [
        _toolcall("document_search", {"query": f"q{i}"}, f"c{i}") for i in range(10)
    ]

    response = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "loop"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["output"] is None and "step limit" in body["error"]
    run = [a for a in await _audits(app, auth_headers) if a.event == "agent_run"]
    assert run[-1].outcome == "error" and run[-1].error_type == "step_limit"


# --- failure paths (audit completeness) --------------------------------------


async def test_provider_error_is_audited(client, app, auth_headers, agent_llm):
    agent_llm.script.append(make_litellm_exc(litellm.exceptions.APIConnectionError))
    response = await client.post(
        "/v1/agents/chat-only/run", headers=auth_headers, json={"input": "hi"}
    )
    assert response.status_code == 502  # provider failure surfaced honestly
    run = [a for a in await _audits(app, auth_headers) if a.event == "agent_run"]
    assert run and run[-1].outcome == "error"
    assert run[-1].error_type == "APIConnectionError"


async def test_tool_handler_error_is_traced_and_audited(
    client, app, auth_headers, agent_llm, fake_embeddings, monkeypatch
):
    from forge.agents import tools as tools_mod

    async def _boom(ctx, **kwargs):
        raise RuntimeError("search backend down")

    monkeypatch.setattr(tools_mod.REGISTRY["document_search"], "handler", _boom)
    agent_llm.script += [_toolcall("document_search", {"query": "x"}), _final("recovered")]

    response = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "go"}
    )
    assert response.status_code == 200  # a tool fault is the agent's to handle
    step = next(t for t in response.json()["trace"] if t["type"] == "tool_call")
    assert step["outcome"] == "error"
    errored = [a for a in await _audits(app, auth_headers)
               if a.event == "tool_call" and a.outcome == "error"]
    assert errored


async def test_malformed_tool_call_does_not_crash(client, auth_headers, agent_llm, fake_embeddings):
    # arguments is invalid JSON, and a second call is missing its function entirely
    bad = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "x", "type": "function",
             "function": {"name": "document_search", "arguments": "{not json"}},
            {"id": "y", "type": "function"},  # no function payload
        ],
    }
    agent_llm.script += [bad, _final("ok")]
    response = await client.post(
        "/v1/agents/research-assistant/run", headers=auth_headers, json={"input": "go"}
    )
    assert response.status_code == 200  # malformed provider output must not 500
    assert response.json()["output"] == "ok"
