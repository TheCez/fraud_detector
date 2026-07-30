# Frontend

React + TypeScript + Vite dashboard for the audit-dossier reviewer. Upload a dossier, browse its files and normalized records, and read findings with their source evidence.

```bash
npm install
npm run dev        # dev server, expects the backend on its default port
npx vitest run     # tests
npx oxlint         # lint
```

The backend must be running for anything beyond the mock data in `src/api/mock-data.ts`:

```bash
cd ../backend && uvicorn app.main:app --reload
```

## Layout

- `src/pages/` - the two screens, upload and dashboard
- `src/api/` - client and mock data
- `src/types/models.ts` - types mirroring the backend's `app/models/schemas.py`

## What to preserve when changing this

Every displayed claim must trace to evidence the backend supplied - never render a figure the API did not return alongside its source location. `analysis_incomplete` is a real state and must stay visually distinct from "no findings": a dossier whose analysis failed must not look like a clean one. See `AGENTS.md` and `PROJECT_CONTEXT.md`.
