# The web image: `next build` with output: "standalone", served by its server.js on :3000.
FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /web
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
# baked in at build: the /api/* rewrite's target (the server components read it again at run time)
ARG FFL_API_URL=http://api:8300
ENV FFL_API_URL=$FFL_API_URL NEXT_TELEMETRY_DISABLED=1
RUN pnpm exec next build

FROM node:22-alpine
WORKDIR /web
ARG FFL_API_URL=http://api:8300
ENV NODE_ENV=production FFL_API_URL=$FFL_API_URL HOSTNAME=0.0.0.0 PORT=3000 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
COPY --from=build /web/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
