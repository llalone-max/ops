# ops

Self-hosted, auto-refreshing static pages for variable AI-spend telemetry.

- `index.html` — public rolled-up view (aggregate spend, tokens, anomaly watch). No breakdown.
- `dashboard.html` — internal operating view (per-line + per-stage cost).

Both pages are generated from a private data source and carry a `noindex` tag; `robots.txt`
blocks crawlers. Regenerated daily by GitHub Actions (`.github/workflows/refresh.yml`) using
encrypted repo secrets; no credential is ever written into the committed HTML. The build
refuses to render if its display config is missing, so nothing sensitive can leak on a misfire.

Generators: `build_dashboard.py`, `build_public.py` (stdlib only).
