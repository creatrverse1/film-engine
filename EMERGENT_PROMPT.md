# World Builder × Film Engine — build prompt for Emergent

Before pasting: replace the two values in Part 1 (your deployed Film Engine URL and its API token).
Paste everything below the line into Emergent. If Emergent works better in stages, paste one Part at a time, in order, and test after each.

---

You are extending my existing app **World Builder** (React frontend + FastAPI backend + MongoDB). It has a **Create** mode (left panel with Chat / Generate / Library / Prompt Library tabs, a center preview, a right-hand Shot Inspector, and a **Page Shot Deck** along the bottom) and an **Edit** mode (left page list with Classic/Timeline views, center page canvas, right panel with AI Chat and Tools: History, Page Tools, Add Elements, This Page). Pages are slides navigated with prev/next arrows and the bottom page timeline.

I'm connecting it to an external service I run called the **Film Engine**. From one short prompt, it plans and generates a continuous cinematic journey: ordered **keyframe images**, **transition video clips** between them, and a full **frame sequence** for scroll/play-through animation. Do NOT build any image or video generation for this; only call the Film Engine.

The goal has three parts:
1. Generate a film from a prompt and lay its shots out **in order on the Shot Deck**.
2. **Send any shot to a page's background**: a still image, a video clip, or an animated film segment.
3. Edit everything else on the page in **Edit** mode, including fonts, colors, text, layout and background settings.

Keep World Builder's current look throughout: black background, gold (#E3B341-style) primary buttons, lime accent in Create mode, the same fonts, spacing and component styles. Reuse existing components wherever they exist.

---

## Part 1 — Backend: Film Engine connection

**Environment variables (backend only; never send them to the browser):**
- `FILM_ENGINE_URL` = `https://YOUR-FILM-ENGINE-URL`
- `FILM_ENGINE_TOKEN` = `YOUR_API_TOKEN`

Every engine call sends the header `Authorization: Bearer {FILM_ENGINE_TOKEN}` and uses a 60 s timeout. If the engine returns an error, pass its message through to the frontend unchanged.

**Engine API (what we call):**
- `POST {FILM_ENGINE_URL}/api/sites` with body `{prompt, brand?, scenes?, auto_approve?}` returns a job.
- `GET {FILM_ENGINE_URL}/api/sites/{id}` returns the job.
- `POST {FILM_ENGINE_URL}/api/sites/{id}/approve`
- `POST {FILM_ENGINE_URL}/api/sites/{id}/regenerate` with body `{index, note?}`

**Job JSON returned by the engine:**
```json
{
  "id": "fdcb2360edc8",
  "status": "queued | running | awaiting_approval | done | failed",
  "stage": "planning | estimating | keyframes | review | clips | frames | building | done | failed",
  "brand": "Maison Atelier",
  "palette": {"light": "#F5F0E8", "mid": "#D9CBB4", "accent": "#5C4033", "dark": "#2B2B2B"},
  "estimate_usd": 6.9,
  "error": null,
  "site_url": "https://…/sites/maison-atelier-fdcb23/",
  "keyframes": [
    {"index": 0, "name": "Plan",
     "copy": {"kicker": "…", "headline": "It starts with *a plan.*", "body": "…", "cta": false},
     "url": "https://…/preview/k01.jpg",
     "image_url": "https://…/full/k01.webp"}
  ],
  "clips": [
    {"index": 0, "from": 0, "to": 1, "done": true, "motion": "The drawing lifts…",
     "video_url": "https://…/web/clip_01.mp4", "poster_url": "https://…/full/k01.webp", "duration": 5}
  ],
  "film": {
    "base_url": "https://…/sites/maison-atelier-fdcb23/",
    "manifest_url": "https://…/frames/manifest.json",
    "count": 1201, "fps": 24, "keys": [1, 121, 241, 361, 481, 601, 721, 841, 961, 1081, 1201]
  },
  "events": [{"t": 1761200000, "stage": "keyframes", "message": "Keyframe 3 ready"}]
}
```
Notes on these fields:
- `url` is a small preview for cards; `image_url` is the full-size image for backgrounds.
- In `copy.headline`, words wrapped in `*asterisks*` should be shown in italic.
- `film` is present only once the frames stage has finished.
- `keys[i]` is the frame where scene `i` starts. Frame `n`'s file is `{base_url}frames/f_{n padded to 4 digits}.webp`, and the 900px mobile version is `{base_url}frames/sm/f_….webp`.

**Our backend routes** (all require the logged-in user; scope everything to the user's project):
- `POST /api/film/create` with body `{project_id, prompt, brand?, scenes?}`: calls the engine, stores a `film_jobs` doc, and returns the job.
- `GET /api/film/{job_id}`: proxies the engine; on every call, upsert the job snapshot into `film_jobs` and sync the Shot Deck (Part 2).
- `POST /api/film/{job_id}/approve`
- `POST /api/film/{job_id}/regenerate` with body `{index, note?}`
- `GET /api/projects/{project_id}/film-jobs`: lists the project's jobs so polling can resume after a reload.

**MongoDB collection `film_jobs`:** `{_id: job_id, project_id, user_id, prompt, brand, status, stage, estimate_usd, snapshot (last engine JSON), created_at, updated_at}`

---

## Part 2 — Shot Deck: generated shots, in order

**MongoDB collection `shots`:** `{_id, project_id, job_id?, order, kind, title, subtitle, thumb_url, image_url?, video_url?, poster_url?, film?, duration_s, scene_index?, clip_index?, source: "film_engine" | "upload" | "generated", created_at}`
- `kind` is one of `"keyframe" | "clip" | "film"`.
- `film`, when present, is `{manifest_url, base_url, keys, fps, start_scene, end_scene}`.

**Sync rule.** When a job snapshot arrives, upsert shots for that job (keyed by `job_id + kind + index`) so the deck reads in journey order:
- `K1, clip 1→2, K2, clip 2→3, K3, …, Kn`
- Keyframe shots: `title` = keyframe `name`; `subtitle` = the copy headline with the asterisks removed, or the first 60 characters of the body; `thumb_url` = `url`; `image_url` = `image_url`.
- Clip shots: `title` = "{from name} → {to name}"; `subtitle` = `motion`; `thumb_url` = the from-keyframe preview; `video_url`, `poster_url` and `duration_s` = 5.
- Once `film` exists, add one `kind: "film"` shot at the front of the deck, titled "Full film · {brand}", with `film` = the whole range (start_scene 0, end_scene last) and `duration_s` = count / fps.
- Never overwrite a shot the user has renamed or reordered: keep their `title` and `order` if they already changed them.

**Shot Deck UI** (bottom "Page shot deck" strip):
- Show the cards in `order`, numbered.
- Keyframe cards show the image.
- Clip cards are narrower and show a ▶ badge with "5s". Hovering a clip card plays it muted inline.
- The film card shows a filmstrip badge and its total duration.
- Drag to reorder, and persist the new `order`.
- Each card gets a ⋯ menu: **Set as page background**, **Rename**, **Duplicate**, **Remove from deck**.
- While a job is running, show placeholder cards for upcoming keyframes and clips, with a subtle shimmer and the stage label, and fill them in as URLs arrive.

**Shot Inspector** (right panel in Create mode), for the selected shot:
- Show the title, subtitle and a large preview: the image, or the video with play controls, or for a film shot, a scrubber that previews frames.
- Keep the existing Duration, Narration and Hotspots fields.
- Add a prominent gold **Set as page background** button, plus a **Redo this keyframe** action for keyframe shots. It appears only while the job is `awaiting_approval`; it opens a note field and calls regenerate.

---

## Part 3 — Generate a film (Create mode)

In the left panel's **Generate** tab, add a third option, **Film**, next to Image / Video. It shows:
- **Prompt:** a large textarea with the placeholder "Describe the journey: the business, the spaces to move through, and how it should end."
- **Brand name** (optional).
- A **Scenes** stepper, 6–11, defaulting to 11.
- A note: "Takes about 20–30 minutes. You'll review the keyframes before clips are made."
- A gold **Generate film** button.

On submit, call `/api/film/create`, then poll `/api/film/{id}` every 5 s until the status is `done` or `failed`.

**Progress:**
- Show a slim stepper above the Shot Deck: Planning → Keyframes → Review → Clips → Frames → Done. Highlight the current `stage`.
- Under the stepper, show the latest event message.

**Review step:** when the status is `awaiting_approval`, show a gold bar above the deck:
- Text: "Review your {n} keyframes. Generating the clips costs about ${estimate_usd}."
- A button: **Approve & make clips**.
- A link: **Redo a keyframe**, which explains that you choose a card and use Redo in the inspector.

**Finishing:**
- When the status is `done`, show a toast with a **Send scenes to pages** button (Part 4).
- When the status is `failed`, show the `error` and a **Try again** button.
- If the user reloads or navigates away, resume polling any running job from `/api/projects/{id}/film-jobs`.

---

## Part 4 — Send shots to page backgrounds

**Add a `background` object to every page document:**
```json
{
  "type": "none | image | video | film",
  "image_url": null,
  "video_url": null,
  "poster_url": null,
  "film": null,
  "fit": "cover",
  "focal_x": 50,
  "focal_y": 50,
  "overlay": {"color": "#000000", "opacity": 0.35, "gradient": "bottom"},
  "blur": 0,
  "playback": "on_enter",
  "loop": false,
  "speed": 1,
  "shot_id": null
}
```
Field notes:
- `film`, when set, is `{manifest_url, base_url, keys, fps, start_scene, end_scene}`.
- `fit` is `cover` or `contain`; `focal_x` and `focal_y` are percentages.
- `overlay.gradient` is `none`, `bottom`, `left` or `full`.
- `blur` is in px.
- `playback` applies to video and film: `on_enter` (play once and hold the last frame), `loop`, or `scroll` (film only; scrubs with scroll on scrolling pages).
- `speed` is 0.5–2.

**"Set as page background" dialog** (opened from a deck card or the inspector):
- **Target page:** a dropdown of the project's pages, defaulting to the current page, plus an option "+ New page".
- **Use as:**
  - For a keyframe: Still image (default), or Animated: play the film segment into this scene.
  - For a clip: Video (default), or Still (its poster).
  - For a film: Film with **From scene** / **To scene** selects, defaulting to the whole range.
- **Playback** (for video and film): On enter, Loop, or Scroll.
- **Also apply the scene's text to the page** checkbox, on by default for keyframes that have `copy`. When on, set the page title from the headline (keep the *italic* markup as italic text), set the description from the body, and set the eyebrow/section label from the kicker.
- An **Apply** button writes the page `background` (and the text, if chosen) and records one undo step in History.

**"Send scenes to pages" (bulk)** — from the done toast, and from the Shot Deck ⋯ menu:
- For scene `i` (`i` = 0…n−1), use page `i + 1` of the project, creating pages that are missing and naming each new page after the scene `name`.
- If `i` = 0, set the background to the keyframe still.
- Otherwise, set a film background with start_scene `i − 1`, end_scene `i` and playback `on_enter`, so entering the page plays the camera move into that scene.
- Apply each scene's copy to its page.
- First show a confirmation that lists which pages will change, then apply everything as one undo step.

---

## Part 5 — The background renderer (used in Edit, Preview and published pages)

Build one `<PageBackground background={…} active={isCurrentPage} />` component that sits behind all page content and fills the page.

- **image:** an `<img>` with `object-fit` from `fit` and `object-position` from `focal_x% focal_y%`, plus blur. Use `loading="eager"` for the current and next page and `lazy` for the rest.
- **video:** `<video muted playsInline preload="auto" poster={poster_url}>`.
  - `on_enter`: when `active` becomes true, restart from 0 and play once; on end, hold the last frame.
  - `loop`: autoplay and loop while active; pause when not active.
  - Respect `speed` with `playbackRate`.
- **film:** a `<canvas>` frame player.
  - Fetch `manifest_url` and cache it per URL.
  - Frames run from `keys[start_scene]` to `keys[end_scene]`. Load them from `{base_url}frames/f_NNNN.webp`, or from `frames/sm/…` when the viewport is under 820px wide or the pointer is coarse.
  - Fetch frames as blobs, 6 at a time, in coarse-to-fine order (every 8th frame first, then every 4th, then the rest). Decode them with `createImageBitmap` and keep an LRU of about 48 decoded frames.
  - Draw cover-fit to a canvas sized to the element × devicePixelRatio, capped at 1.5×.
  - `on_enter`: when `active` becomes true, play from the first frame to the last at `fps × speed` with requestAnimationFrame, then hold the last frame. Show the first frame immediately; never show a blank canvas.
  - `loop`: play forward, then backward (ping-pong), so the loop has no jump.
  - `scroll`: map the page's scroll progress (0–1 across the page height) to the frame range, smoothed with a 0.5 s ease.
  - Before frames are ready, show the start keyframe still (`{base_url}stills/kNN.webp`) as a fallback.
  - Preload the next page's first frames while the current page is showing.
- **overlay:** a layer above the media.
  - `bottom`: a linear gradient from color at `opacity` at the bottom to transparent at 60% height.
  - `left`: the same gradient from the left.
  - `full`: a flat fill.
- **prefers-reduced-motion:** never animate. Show the poster or still (for film, the end scene's still).

---

## Part 6 — Edit mode: full design control

In Edit mode, every page shows its background through `PageBackground`, and everything on top of it stays editable.

**A. Tools → This Page → new "Background" section**
- A thumbnail of the current background, with **Replace** (opens a picker of this project's Shot Deck shots plus Upload), **Remove**, and **Open in Shot Deck**.
- **Type** (read-only label): Image, Video or Film.
- **Fit** (Cover / Contain) and a focal-point picker: click a point on the thumbnail to set `focal_x` / `focal_y`.
- **Overlay:** color swatch, opacity slider (0–90%) and gradient direction (None / Bottom / Left / Full).
- **Blur** slider, 0–20px.
- For Video/Film: **Playback** (On enter / Loop / Scroll), **Speed** (0.5×–2×), and **Replay** to preview.
- For Film: **From scene** and **To scene** selects, using scene names from the manifest's job.
- Every change updates the canvas live and is recorded in History (undo/redo).

**B. Typography and theme — new "Style" section in Tools**

*Project theme* (applies to all pages):
- **Heading font** and **Body font**: a searchable picker of Google Fonts, showing each font name rendered in that font. Include curated pairings at the top: Cormorant Garamond + Inter, Playfair Display + Inter, DM Serif Display + DM Sans, Fraunces + Manrope, Instrument Serif + Inter, Bodoni Moda + Montserrat, Space Grotesk + Space Grotesk.
- **Colors:** Text, Accent, Muted text, Button fill and Button text. Add a "Use film palette" button that maps the job's `palette` to these colors: light → text, accent → button text, mid → muted, accent → accent.
- **Type scale:** small / medium / large.

*Per-element overrides* (shown when a text element is selected on the canvas):
- Font family, weight (300–800), italic, size (px, with a slider), line height, letter spacing, text transform (none / uppercase), color, alignment, max width.
- A **Reset to theme** button.
- In headings, text wrapped in `*asterisks*` renders in italic. Also add an inline **Italic** toggle in the text toolbar.

Load fonts by injecting a single Google Fonts stylesheet link for the families the project uses. Always include a fallback stack (serif or sans-serif).

**C. Direct manipulation on the canvas**
- Click a text element to select it (gold outline). Double-click to edit its text inline.
- Drag to move and use the handles to resize width. Elements snap to a 12-column grid and to the page center lines.
- Arrow keys nudge by 1px, and Shift+arrow by 10px. Delete removes the element (with undo).
- Existing Add Elements (Callout, Stat Card, Title, Link Button, Form, etc.) inherit the theme fonts and colors by default.

**D. Readability guard**
If a selected text element's color has a contrast ratio under 4.5:1 against the average color of the background behind it, show a small amber hint: "Low contrast on this background — raise the overlay or change the text color." It needs a one-click **Fix** that raises the overlay opacity to the minimum value that passes. To estimate the background color, sample the background image or current frame in a small offscreen canvas.

**E. AI Chat in Edit mode** should understand requests like:
- "use the kitchen shot as this page's background"
- "make the title Cormorant Garamond, 96px, light"
- "darken the overlay"
- "play the film from Living to Kitchen on this page"

Implement these as tool functions the chat can call:
- `setPageBackground(pageId, shotId, options)`
- `updateBackground(pageId, patch)`
- `setTheme(patch)`
- `updateElementStyle(elementId, patch)`
- `applySceneCopy(pageId, sceneIndex)`

Each call records an undo step.

---

## Part 7 — Preview and Publish

- **Preview** and the published page render exactly what Edit shows: the same `PageBackground`, theme and overrides.
- Page transitions: when a page with a film or video `on_enter` background becomes active, start its playback. Otherwise crossfade the backgrounds over 600 ms.
- Published pages load the active page's media first and prefetch the next page's.
- Media URLs point to the Film Engine and stay valid. Store the URLs on the page; don't copy files for now.

---

## Acceptance checklist (test each before finishing)

1. Generating a film shows progress, and keyframe cards appear one by one in order. The deck reads K1, clip, K2, clip, … with the Full film card first once frames are ready.
2. Approve is shown only at the review step and starts the clips. Redo on a keyframe regenerates it, along with the later ones.
3. **Set as page background** works for each kind (still, video, film range) on the current page and on another chosen page, and Undo reverts it.
4. **Send scenes to pages** fills pages 1…n with backgrounds and copy, and entering each page plays the move into that scene, holding the last frame.
5. In Edit, changing fonts in Style updates every page live, a per-element override wins over the theme, and Reset to theme restores it.
6. The Background controls (fit, focal point, overlay, blur, playback, speed, scene range) all update live and are undoable.
7. Preview and the published page match Edit. On a phone-width viewport, the film uses the smaller frames and still plays smoothly.
8. With reduced motion turned on, there is no animation and the backgrounds show stills.
9. `FILM_ENGINE_TOKEN` never appears in frontend code or network responses.

Don't change unrelated features, routing or the existing visual design.
