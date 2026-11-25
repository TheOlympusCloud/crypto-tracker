# The web app (web/, am-i-exposed with the wallet tracker in it), built as am-i-exposed's own
# Dockerfile.umbrel does: a static export, nothing of Node in the result.
FROM node:22-alpine AS web
# Next would start a build worker per CPU the host shows (52 on a big Proxmox host), about
# 115 MB each: more than a small container has. With 2 the build peaks at about 2.5 GB.
ARG NEXT_BUILD_CPUS=2
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /src/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY web/ ./
COPY scripts/entity-data.sh /src/scripts/
RUN sh /src/scripts/entity-data.sh && pnpm build

# The server: the wallet tracker's API on /api/tracker/ and the web app on everything else.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY tracker tracker
COPY --from=web /src/web/out web
# Without CT_PASSWORD, one is made on the first start and kept in the data volume.
ENV CT_HOST=0.0.0.0 CT_DB=/data/tracker.db CT_WEB=/app/web CT_PASSWORD_FILE=/data/password
VOLUME /data
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "python", "-m", "tracker"]
