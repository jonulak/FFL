# FFL web

The in-season UI for the league. Next.js (App Router) + Tailwind + shadcn/ui, talking to the
FastAPI service in `ffl/api.py` on `127.0.0.1:8300`.

## Ports

- dev: **3301** (`pnpm dev`) — 3000 is taken by another project on this machine
- production: **3300**, bound to 127.0.0.1 (`pnpm build && pnpm start`)

`next.config.ts` rewrites `/api/:path*` to `$FFL_API_URL` (default `http://127.0.0.1:8300`), so the
browser never talks to FastAPI directly.

## Commands (pnpm only — never npm/yarn/bun)

```
pnpm dev         # dev server on 3301
pnpm build       # production build
pnpm start       # production server on 127.0.0.1:3300
pnpm lint        # eslint
pnpm typecheck   # tsc --noEmit
pnpm test        # vitest run
pnpm types       # regenerate lib/api.d.ts from the FastAPI OpenAPI schema
```

Start the API first: `uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300`.

## Types

`pnpm types` runs `scripts/gen-types.sh`, which dumps `app.openapi()` from `ffl.api` and feeds it to
`openapi-typescript` → `lib/api.d.ts`. The endpoints return untyped dicts, so the generated response
bodies are mostly `unknown`; the hand-written types in `lib/api.ts` are the working contract, and
`api.d.ts` is kept for the path names.
