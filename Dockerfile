# am-i-exposed (the git submodule) with the patches in patches/am-i-exposed/, built as
# upstream's own Dockerfile.umbrel does: a static export, nothing of Node in the result.
FROM node:22-alpine AS exposed
RUN apk add --no-cache git && corepack enable
WORKDIR /src
COPY am-i-exposed/package.json am-i-exposed/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY am-i-exposed/ ./
COPY patches/am-i-exposed/ /patches/
RUN git apply /patches/*.patch && pnpm build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY tracker tracker
COPY --from=exposed /src/out exposed
ENV CT_HOST=0.0.0.0 CT_DB=/data/tracker.db CT_EXPOSED=/app/exposed
VOLUME /data
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "python", "-m", "tracker"]
