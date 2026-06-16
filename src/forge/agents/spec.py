"""Agent specs: deploy agents by config, not code (ADR-0019).

Each YAML file in agents_dir defines one agent. Tool references are validated
against the registry at load time, so an agent that names an unknown tool fails
fast at startup rather than at run time.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from forge.agents.tools import REGISTRY


class AgentSpec(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    model: str  # a Forge alias (validated against model_map at run time)
    system_prompt: str = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)
    max_steps: int = Field(default=6, ge=1, le=20)


class UnknownToolError(Exception):
    pass


def _validate_tools(spec: AgentSpec) -> None:
    unknown = [t for t in spec.tools if t not in REGISTRY]
    if unknown:
        raise UnknownToolError(
            f"agent {spec.name!r} references unknown tools {unknown}; "
            f"available: {sorted(REGISTRY)}"
        )


def load_agents(directory: str) -> dict[str, AgentSpec]:
    specs: dict[str, AgentSpec] = {}
    path = Path(directory)
    if not path.exists():
        return specs
    for file in sorted(path.glob("*.yaml")):
        data = yaml.safe_load(file.read_text())
        spec = AgentSpec(**data)
        _validate_tools(spec)
        specs[spec.name] = spec
    return specs
