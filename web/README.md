# Kiosk frontend (Person B)

React + TypeScript + Vite. It talks only to the API in `docs/api.md`.

Run these from the repository root:

```sh
npm --prefix web install          # once
npm --prefix web run dev:mock     # fake data, no backend needed → http://localhost:5173
npm --prefix web run dev          # real backend on 127.0.0.1:8000 (proxied at /api)
npm --prefix web run typecheck
npm --prefix web run build        # writes web/dist, which FastAPI serves at /
```

## Files

| File | What it does |
| --- | --- |
| `src/api.ts` | Types from `docs/api.md`, one function per endpoint, `USE_MOCK`, `UI_DEADLINE_MS = 2000` |
| `src/mock.ts` | In-memory fake backend. About 1 in 8 verifies answers late, to exercise the UI deadline |
| `src/stats.ts` | Session statistics with the same definitions as `scripts/evaluate.py`: median, nearest-rank p95, attack and genuine groups, within-2 s |
| `src/Metrics.tsx` | Score meters, the "where the time went" breakdown, and the Session metrics section (tiles, by-scenario table, decision-time histogram) |
| `src/messages.ts` | Outcome titles, reason messages people can act on, scenario labels |
| `src/App.tsx` | Camera start/stop and preview, participant picker, enrollment, Verify, outcome card, recent attempts with the scenario dropdown |
| `src/styles.css` | Design tokens (neutral palette, 8px spacing scale, Inter) and all component styles. ALLOW is green, REQUEST_VERIFICATION amber, DENY red. Single column below 980 px |
| `vite.config.ts` | Proxies `/api` (including the MJPEG preview) to `127.0.0.1:8000` |

## Timing and the deadline

- `click_to_render_ms` is measured from the Verify click to the animation frame in which the outcome is painted. After the outcome renders, it is sent to `POST /api/attempts/{id}/client_timing`.
- If there is no response after `UI_DEADLINE_MS`, the kiosk shows REQUEST_VERIFICATION ("Please try again") and ignores the late response.
  - When that late response arrives, the kiosk reports `ui_deadline_missed: true`, with the time the fallback was painted.
- Verify stays disabled until the server request has settled, so attempts never queue up behind a slow one.
