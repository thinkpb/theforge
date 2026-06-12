# Forge gateway image. Two-step uv install keeps the dependency layer cached
# across source-only changes.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# dependency layer — invalidated only when the lockfile changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# application layer (README.md is part of the package metadata build)
COPY src/ src/
COPY README.md alembic.ini ./
COPY alembic/ alembic/
RUN uv sync --frozen --no-dev

# run as non-root with a NUMERIC uid: k8s runAsNonRoot can't verify named users
RUN useradd --system --no-create-home --uid 10001 forge && chown -R forge:forge /app
USER 10001

EXPOSE 8000
# run straight from the venv — uv isn't needed (and can't cache) at runtime
CMD ["/app/.venv/bin/uvicorn", "forge.main:app", "--host", "0.0.0.0", "--port", "8000"]
