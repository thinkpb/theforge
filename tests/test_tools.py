"""Tool library tests (ADR-0020): calculator safety, list_documents, and that
each new tool obeys the same authority + audit rules as the runtime (ADR-0019).
"""

import json

import pytest
from sqlalchemy import select

from forge.agents.tools import REGISTRY, _calculator
from forge.audit import AuditLog, key_fingerprint

FACTS = [
    ("policy-a", "The refund window is 47 days."),
    ("policy-b", "Late fees accrue at 2 percent monthly."),
]


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
        return _FakeResp(item)

    monkeypatch.setattr("forge.agents.runtime.litellm.acompletion", _fake)
    return script


def _hash(auth_headers):
    return key_fingerprint(auth_headers["Authorization"].removeprefix("Bearer "))


# --- calculator: pure + safe --------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [("2 + 3", "5"), ("(2 + 3) * 4", "20"), ("10 / 4", "2.5"), ("2 ** 8", "256"),
     ("17 % 5", "2"), ("-3 + 1", "-2")],
)
async def test_calculator_evaluates_arithmetic(expr, expected):
    assert await _calculator(None, expr) == expected


@pytest.mark.parametrize(
    "attack",
    ["__import__('os').system('id')", "open('/etc/passwd').read()", "x + 1",
     "().__class__", "2 ** 999999999999"],
)
async def test_calculator_refuses_non_arithmetic(attack):
    # no eval — names, calls, attribute access, and DoS exponents all rejected
    assert (await _calculator(None, attack)).startswith("Error:")


def test_registry_has_the_three_tools():
    assert set(REGISTRY) == {"document_search", "list_documents", "calculator"}


# --- list_documents: team-scoped read ----------------------------------------


async def test_list_documents_tool(client, app, auth_headers, agent_llm, fake_embeddings):
    for title, text in FACTS:
        await client.post(
            "/v1/documents", headers=auth_headers, json={"text": text, "title": title}
        )

    agent_llm += [_toolcall("list_documents", {}), _final("There are two policies.")]
    response = await client.post(
        "/v1/agents/analyst/run", headers=auth_headers, json={"input": "what documents exist?"}
    )
    assert response.status_code == 200
    step = next(t for t in response.json()["trace"] if t["type"] == "tool_call")
    assert step["tool"] == "list_documents" and step["outcome"] == "success"

    await app.state.audit_buffer.drain()
    async with app.state.db_session_factory() as session:
        audits = list(await session.scalars(
            select(AuditLog).where(AuditLog.api_key_hash == _hash(auth_headers))
        ))
    assert any(a.event == "tool_call" and a.tool == "list_documents" for a in audits)


async def test_list_documents_is_team_scoped(client, auth_headers, agent_llm, fake_embeddings):
    await client.post(
        "/v1/documents", headers=auth_headers, json={"text": "secret", "title": "admin-doc"}
    )
    created = await client.post(
        "/v1/keys", headers=auth_headers, json={"name": "t", "team": "other-team"}
    )
    other = {"Authorization": f"Bearer {created.json()['key']}"}

    agent_llm += [_toolcall("list_documents", {}), _final("none")]
    response = await client.post("/v1/agents/analyst/run", headers=other, json={"input": "list"})
    assert response.status_code == 200
    # the other team sees none of admin's documents
    tool_step = next(t for t in response.json()["trace"] if t["type"] == "tool_call")
    assert tool_step["outcome"] == "success"


# --- authority still applies to the new tools --------------------------------


async def test_calculator_denied_when_not_granted(client, auth_headers, agent_llm):
    # chat-only grants no tools — a calculator call must be denied
    agent_llm += [_toolcall("calculator", {"expression": "2+2"}), _final("done")]
    response = await client.post(
        "/v1/agents/chat-only/run", headers=auth_headers, json={"input": "add"}
    )
    assert response.status_code == 200
    step = next(t for t in response.json()["trace"] if t["type"] == "tool_call")
    assert step["tool"] == "calculator" and step["outcome"] == "denied"
