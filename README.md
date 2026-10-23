# Film Engine

A small backend that turns a one-line brief into a cinematic scroll-driven website:

```
brief ─▶ Claude plans the film (scenes, prompts, copy, palette)
      ─▶ Higgsfield Grok Image 2 makes the keyframes (each built from the last)
      ─▶ Claude checks each keyframe, bad ones are regenerated once
      ─▶ ⏸ review: your app shows the keyframes, the user approves (or redoes one)
      ─▶ Higgsfield Kling O3 makes the transition clips (start + end frame)
      ─▶ ffmpeg turns clips into one WebP sequence + manifest
      ─▶ the site template is filled from the plan and published at /sites/<slug>/
```

A run takes roughly 20–30 minutes and costs about $7 in Higgsfield credits for 11 scenes (plus a few cents of Claude).
Your app talks to it with four HTTP calls.

## Deploy (any host that runs a Docker container with a persistent disk)

Railway, Render and Fly.io all work. The service needs:

- **A long-running container** (not serverless): jobs run for up to half an hour and use ffmpeg.
- **A persistent volume mounted at `/data`**: jobs, keyframes, clips and published sites live there.
- **Outbound access** to `api.higgsfield.ai`, Higgsfield's download CDN, and `api.anthropic.com`.

Steps (Railway as the example):
1. Put this folder in a GitHub repo.
2. New project → Deploy from repo. Railway detects the Dockerfile.
3. Add a volume mounted at `/data`.
4. Set the environment variables below, then deploy.
5. Open `https://<your-service>/health`; it should report both keys as `true`.

### Environment variables

| Variable | Required | What it is |
|---|---|---|
| `HF_API_KEY_ID`, `HF_API_KEY_SECRET` | yes | Higgsfield API key (console.higgsfield.ai → API keys) |
| `ANTHROPIC_API_KEY` | yes | Claude API key (console.anthropic.com) for planning and keyframe checks |
| `API_TOKEN` | yes | A long random string. Your app's backend sends it as `Authorization: Bearer …` |
| `PUBLIC_BASE_URL` | yes | The service's public URL, e.g. `https://film-engine.up.railway.app` |
| `MAX_BUDGET_USD` | no (15) | Jobs estimated above this are refused before spending anything |
| `REQUIRE_APPROVAL` | no (true) | Pause after keyframes until `/approve` is called |
| `CLAUDE_MODEL` | no | Defaults to `claude-sonnet-5-5` |
| `KLING_MODE` | no (pro) | `std` is cheaper (~$0.42/clip vs $0.56) |
| `JOB_WORKERS` | no (2) | Sites built at the same time |
| `CORS_ORIGINS` | no (*) | Lock to your app's domain if the browser calls the engine directly |

Never put these keys in your front end. Your app's backend calls the engine; the browser calls your backend.

## API

All `/api` calls need `Authorization: Bearer <API_TOKEN>`.

**Start a site**
```
POST /api/sites
{ "prompt": "Luxury interior design studio. Start with a floor plan that becomes a real room, then walk through kitchen, bedroom and bath, end on the house at dusk.",
  "brand": "Maison Atelier",          // optional
  "scenes": 11,                        // optional, 4–11
  "form_endpoint": "https://…",        // optional: where the site's lead form POSTs (JSON)
  "callback_url": "https://…",         // optional: receives the job JSON on every progress step
  "auto_approve": false }              // true skips the review pause
→ { "id": "8f41ab4873fa", "status": "queued", ... }
```

**Check progress** (poll every 5–10 s, or use `callback_url`)
```
GET /api/sites/{id}
→ { "id", "status", "stage", "brand", "estimate_usd", "site_url", "error",
    "palette": { "light", "mid", "accent", "dark" },
    "keyframes": [{ "index", "name", "copy", "url", "image_url" }],   // url = 640px preview, image_url = 1920px WebP for backgrounds
    "clips": [{ "index", "from", "to", "done", "motion", "video_url", "poster_url", "duration" }],  // web MP4, no audio
    "film": { "base_url", "manifest_url", "count", "fps", "keys" },   // after frames stage; frame n = base_url + frames/f_NNNN.webp (sm/ for 900px)
    "events": [{ "t", "stage", "message" }] }
```
`status`: `queued` → `running` → `awaiting_approval` → `running` → `done` (or `failed`).
`stage`: `planning`, `estimating`, `keyframes`, `review`, `clips`, `frames`, `building`, `done`.

**Approve the keyframes** → `POST /api/sites/{id}/approve`

**Redo a keyframe before approving** → `POST /api/sites/{id}/regenerate { "index": 9, "note": "show the bathroom, not the bedroom" }`
(redoes that keyframe and every later one, because later frames are built from it)

**The finished site** is served at `site_url` (`/sites/<slug>/`). Each site folder also contains `plan.json`.

## Test without spending credits

```
DRY_RUN=1 DRY_RUN_PLAN=examples/maison-plan.json DRY_RUN_MEDIA=/path/to/media \
DATA_DIR=./data API_TOKEN=test uvicorn app.main:app --port 8000
```
`DRY_RUN_MEDIA` holds `keyframes/k01.png…` and `clips/clip_01.mp4…` from an earlier run (the Maison Atelier media works).

## Notes
- Higgsfield keeps outputs ~7 days; the engine downloads everything immediately, so sites don't depend on it.
- One container with SQLite is fine for a handful of builds at a time. For heavy traffic, move jobs to a real queue and sites to object storage + CDN.
- Charge for builds in your app (credits or a paywall). Each run spends real money.
