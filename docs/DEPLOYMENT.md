# Frontend deployment

## Recommendation

The verified replay is deployed to **Vercel** at
<https://strategy-redteam-lab.vercel.app/>. It is the lowest-complexity target for this Next.js
App Router replay: Vercel runs the standard `next build` output without adding hosting
configuration or a runtime service. **Azure Static Web Apps** remains the fallback when Azure
hosting is preferred; use its Next.js-compatible managed-hosting configuration rather than
changing this repository to a static export.

The deployment covers only the verified, read-only dashboard. It does not deploy the Python
engine, Ollama, Foundry agents, market-data ingestion, or any trading capability.

## Production deployment record

| Setting | Production value |
| --- | --- |
| Platform | Vercel |
| Repository | `cx07-oss/strategy-redteam-lab` |
| Live URL / verified replay route | <https://strategy-redteam-lab.vercel.app/> (`/`) |
| Root directory | `frontend` |
| Framework / package manager | Next.js / pnpm |
| Environment variables | None |
| Runtime dependencies | No Python backend, Ollama, or model credentials |

The production route was verified to render run-024 evidence: `ollama-r01-c01`, three breaches,
maximum normalized excess `1.1986`, 81 chart points, the canonical `one_day_gap` attack, a
`reproduced` defender verdict, replay delta `0.0`, and the ordered telemetry timeline.

## Vercel deployment configuration

1. Push this repository to a Git provider and import it into Vercel.
2. Set the project root directory to `frontend`.
3. Use Node.js 22 LTS and pnpm (lockfile version 9).
4. Set the install command to `pnpm install --frozen-lockfile`.
5. Set the build command to `pnpm build`.
6. Leave environment variables empty: the verified replay requires none.
7. Deploy. The expected production route is `/`.

`pnpm sync:fixture` is a repository-maintenance command, not a production build dependency: the
validated fixture is checked in. Run it locally only when deliberately refreshing the verified
artifact, then review the byte-for-byte change before committing.

## Pre-deployment and post-deployment checks

Before deploying from a clean checkout:

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm build
```

After deployment, verify that `/` renders the run provider/model, `ollama-r01-c01`, three
breaches, maximum normalized excess `1.1986`, 81 chart points, a `reproduced` defender verdict,
and replay delta `0.0`. Use the Equity/Drawdown control and confirm the page makes no runtime
request for Python, Ollama, credentials, or local files.

## Redeploy and rollback

Each deployment is safe to redeploy from the same pinned lockfile and checked-in fixture. If a
release fails the checks above, promote the previous Vercel deployment or redeploy the last known
good Git revision. Do not alter telemetry values in the hosting console.

## Azure Static Web Apps fallback

Create a Static Web Apps project connected to the repository, set `frontend` as the app location,
and configure its build to use Node 22, pnpm, and `pnpm build`. Keep the app as the existing Next.js
deployment mode; no API, backend path, secrets, or Azure resource change is required for this
static verified replay. Confirm the same `/` checklist after release.
