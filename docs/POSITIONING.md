# Positioning: Where Forge Fits

*Research snapshot, June 2026. This analysis drives [ADR-0005](adr/0005-compliance-first-design.md)
and the launch blog post.*

## The competitive landscape

| Project | Stars | What it does | What it lacks |
|---|---|---|---|
| **Dify** | 143k | Visual LLM app builder, RAG, agents, self-hostable | No MLOps/fine-tuning, no compliance layer, UI-first not dev-first |
| **RAGFlow** | 81k | Deep RAG engine, document-heavy | Just RAG — no gateway, no fine-tuning, no compliance |
| **PrivateGPT** | 56k | Private document Q&A | Very simple — no agents, no MLOps |
| **Flowise** | 42k | Drag-and-drop agent builder | No-code only, no compliance, no MLOps |
| **LiteLLM** | ~20k | LLM gateway/proxy | Just routing, nothing else |
| **AnythingLLM** | Large | All-in-one chat + RAG + agents | Desktop-focused, no MLOps, no compliance |
| **Haystack** | Large | RAG/NLP framework | Library not platform — no gateway, no compliance |
| **OpenWebUI** | Large | Chat interface | Just a UI, nothing underneath |

## The gap

No open-source project combines all five layers:

```
LLM Gateway        → LiteLLM has this alone
RAG Engine         → RAGFlow / Dify have this alone
Agent Runtime      → Flowise / Dify have this
MLOps + Fine-tune  → nobody open-source has this integrated
Compliance Layer   → nobody has this at all
```

And nobody occupies the regulated-industry angle. The closest product — Aptible AI
Gateway — is managed-cloud, closed-source, enterprise-priced. The open-source world
has nothing designed from the ground up for HIPAA/SOC2 compliance.

## Forge's position

**"The open-source AI infrastructure layer for regulated industries."**

> HIPAA-ready, SOC2-aligned, self-hostable AI platform with built-in audit trails,
> PII scrubbing, and MLOps — deploy in one Helm chart.

That sentence doesn't describe anything currently on GitHub.

What compliance-first means in practice, versus the generic approach:

| Generic AI platform | Forge |
|---|---|
| Build it, add compliance later | Compliance shapes every decision |
| "We support HIPAA" | Every request is audit-logged by default |
| Optional PII scrubbing | PII scrubbing is in the request pipeline |
| Docs say "self-hostable" | Helm chart ships HIPAA-ready out of the box |
| Anyone can use it | Built for legal, healthcare, fintech teams |
| Competes with Dify (143k stars) | Occupies empty regulated-industry space |

## What Forge does NOT compete on

- **Visual app building** — Dify and Flowise own this; Forge is dev-first (API, SDK, config)
- **Chat UI polish** — OpenWebUI and AnythingLLM own this; Forge's dashboard is operational, not end-user
- **Generic RAG benchmarks** — RAGFlow goes deeper on retrieval; Forge's RAG is differentiated by PII-safe ingestion, not retrieval exotica
