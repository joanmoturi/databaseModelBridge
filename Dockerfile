FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.12.12-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml requirements.lock README.md LICENSE ./
COPY backend ./backend
COPY docs ./docs
RUN uv venv /app/.venv && uv pip sync --require-hashes requirements.lock && uv pip install --no-deps .
RUN useradd --uid 10001 --create-home app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "modelbridge.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
