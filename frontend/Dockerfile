# syntax=docker/dockerfile:1
# Frontend web image. Python API and worker use backend/Dockerfile.
# Release step (run once per deploy, before the new web server starts):
#   node --conditions=react-server --import tsx scripts/migrate.ts
# All configuration comes from environment variables at runtime; nothing secret
# is baked into the image. See docs/deploy.md.
#
# Both installs share one download cache, so packages are fetched once and
# reused on later rebuilds.
#
# Behind a TLS-intercepting proxy, pass its CA for the install steps only:
#   docker build --secret id=ca,src=/path/to/ca.pem .

FROM node:22-bookworm-slim AS base
# Patient, retrying downloads so a slow or flaky connection (Wi-Fi, VPN) does
# not fail the build. npm and pnpm both read these settings.
ENV NEXT_TELEMETRY_DISABLED=1 \
    npm_config_fetch_retries=6 \
    npm_config_fetch_retry_mintimeout=10000 \
    npm_config_fetch_retry_maxtimeout=120000 \
    npm_config_fetch_timeout=600000 \
    npm_config_update_notifier=false
WORKDIR /app
# pnpm at the version package.json pins, installed with npm (which retries)
# rather than corepack (which gives up on the first stalled download).
COPY package.json ./
RUN --mount=type=secret,id=ca,required=false \
    NODE_EXTRA_CA_CERTS=$([ -f /run/secrets/ca ] && echo /run/secrets/ca) \
    npm install -g "$(node -p "require('./package.json').packageManager")" --no-fund --no-audit --loglevel=error

FROM base AS build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=secret,id=ca,required=false \
    --mount=type=cache,id=pnpm-store,target=/pnpm/store,sharing=locked \
    NODE_EXTRA_CA_CERTS=$([ -f /run/secrets/ca ] && echo /run/secrets/ca) pnpm install --frozen-lockfile --store-dir /pnpm/store
COPY . .
# public/ has no files yet and git does not keep empty folders.
RUN mkdir -p public && pnpm build

FROM base AS prod-deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=secret,id=ca,required=false \
    --mount=type=cache,id=pnpm-store,target=/pnpm/store,sharing=locked \
    NODE_EXTRA_CA_CERTS=$([ -f /run/secrets/ca ] && echo /run/secrets/ca) pnpm install --frozen-lockfile --prod --store-dir /pnpm/store \
 && rm -rf node_modules/.pnpm/@next+swc-*  # the compiler is only needed to build

FROM node:22-bookworm-slim AS app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
# Migrations and admin scripts run from source.
COPY --from=prod-deps /app/node_modules ./node_modules
COPY package.json tsconfig.json ./
COPY src ./src
COPY scripts/migrate.ts scripts/create-business.ts scripts/reset-link.ts scripts/link-backend-org.ts scripts/provision-backend.ts ./scripts/
COPY supabase/migrations ./supabase/migrations
# Web server (Next.js standalone output).
COPY --from=build /app/.next/standalone ./web
COPY --from=build /app/.next/static ./web/.next/static
COPY --from=build /app/public ./web/public
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "web/server.js"]

# Self-contained laptop demo (docker compose): adds the fictional fixtures, the
# seed and the local auth gateway. Synthetic providers only; never used for deployments.
FROM app AS local
USER root
COPY scripts/seed-users.ts scripts/seed-provision.ts scripts/seed-backend.ts ./scripts/
COPY scripts/local/gateway.mjs ./scripts/local/
COPY docker ./docker
RUN mkdir -p /config /data/storage && chown app:app /config /data/storage
USER app
ENTRYPOINT ["sh", "/app/docker/with-config.sh"]

# Deployment image (the default target).
FROM app AS runner
