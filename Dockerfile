FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY tracker tracker
ENV CT_HOST=0.0.0.0 CT_DB=/data/tracker.db
VOLUME /data
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "python", "-m", "tracker"]
