# The web image: `next build` with output: "standalone", served by its server.js on :3000.
FROM node:26-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94 AS build
RUN corepack enable
WORKDIR /web
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
# baked in at build: the /api/* rewrite's target (the server components read it again at run time)
ARG FFL_API_URL=http://api:8300
ENV FFL_API_URL=$FFL_API_URL NEXT_TELEMETRY_DISABLED=1
# Reproducible build: the build ID is a hash of the sources, so it changes exactly when they do. The two keys are for
# server actions and draft mode, which the app doesn't use; they're readable in this public image anyway. Next reads the
# preview keys only from its cache file (expiry 2100).
ENV NEXT_SERVER_ACTIONS_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
RUN mkdir -p .next/cache \
 && echo '{"previewModeId":"00000000000000000000000000000000","previewModeSigningKey":"0000000000000000000000000000000000000000000000000000000000000000","previewModeEncryptionKey":"0000000000000000000000000000000000000000000000000000000000000000","expireAt":4102444800000}' > .next/cache/.previewinfo \
 && FFL_BUILD_ID=$(find . -path ./node_modules -prune -o -path ./.next -prune -o -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-20) \
    pnpm exec next build

FROM node:26-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94
WORKDIR /web
ARG FFL_API_URL=http://api:8300
ENV NODE_ENV=production FFL_API_URL=$FFL_API_URL HOSTNAME=0.0.0.0 PORT=3000 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
COPY --from=build /web/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
