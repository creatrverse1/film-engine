"""The build: plan -> estimate -> keyframes (+QA) -> [approval] -> clips -> frames -> site.
Every stage records its outputs on the job, so a job can resume after a pause or a restart."""
import json, os, re, shutil, subprocess, threading, traceback, urllib.request
from concurrent.futures import ThreadPoolExecutor
from . import config, db, higgsfield as hf, planner, sitebuilder

def job_dir(jid): return os.path.join(config.DATA_DIR, 'jobs', jid)
def site_dir(slug): return os.path.join(config.DATA_DIR, 'sites', slug)

def slugify(s):
    return (re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40] or 'site')

def notify(job):
    url = job.get('callback_url')
    if not url: return
    try:
        body = json.dumps(public_view(job)).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json'}), timeout=10)
    except Exception:
        pass  # callbacks are best effort; clients can always poll

def public_view(job):
    a, plan = job.get('assets') or {}, job.get('plan') or {}
    scenes = plan.get('scenes') or []
    base = f"{config.PUBLIC_BASE_URL}/media/{job['id']}"
    keyframes = []
    for i, s in enumerate(scenes):
        k = a.get(f'k{i:02d}') or {}
        keyframes.append({'index': i, 'name': s.get('name'), 'copy': s.get('copy'),
                          'url': k.get('public'),                                   # 640px preview for cards
                          'image_url': f'{base}/full/k{i+1:02d}.webp' if k else None})  # 1920px for backgrounds
    clips = []
    for i in range(max(0, len(scenes) - 1)):
        c = a.get(f'clip_{i:02d}')
        clips.append({'index': i, 'from': i, 'to': i + 1, 'done': bool(c),
                      'motion': (plan.get('clips') or [])[i].get('motion_prompt') if i < len(plan.get('clips') or []) else None,
                      'video_url': f'{base}/web/clip_{i+1:02d}.mp4' if c else None,
                      'poster_url': f'{base}/full/k{i+1:02d}.webp' if c else None,
                      'duration': config.CLIP_SECONDS})
    film = a.get('film')
    if film:
        film = {**film, 'manifest_url': f"{film['base_url']}frames/manifest.json"}
    return {'id': job['id'], 'status': job['status'], 'stage': job['stage'], 'prompt': job['prompt'],
            'estimate_usd': (job.get('estimate') or {}).get('total'), 'site_url': job.get('site_url'),
            'error': job.get('error'), 'brand': plan.get('brand'), 'palette': plan.get('palette'),
            'keyframes': keyframes, 'clips': clips, 'film': film,
            'events': (job.get('events') or [])[-30:]}

def _step(jid, stage, msg, **extra):
    db.log(jid, stage, msg, **extra)
    notify(db.get(jid))

# ---------------- stages ----------------
def stage_plan(jid, job):
    if job.get('plan'): return job['plan']
    _step(jid, 'planning', 'Planning the film from your brief')
    plan = planner.make_plan(job['prompt'], job.get('options') or {})
    db.update(jid, plan=plan)
    _step(jid, 'planning', f"Plan ready: {len(plan['scenes'])} scenes for {plan.get('brand')}")
    return plan

def stage_estimate(jid, job, plan):
    if job.get('estimate'): return job['estimate']
    n = len(plan['scenes'])
    if config.DRY_RUN:
        est = {'image': 0.0, 'clip': 0.0, 'total': 0.0}
    else:
        img = hf.estimate(config.IMAGE_MODEL, {'prompt': 'x', 'aspect_ratio': '16:9', 'resolution': '2k', 'quality': 'medium', 'image_urls': ['https://example.com/a.jpg']})
        clip = hf.estimate(config.VIDEO_MODEL, {'prompt': 'x', 'first_frame_url': 'https://example.com/a.jpg', 'last_frame_url': 'https://example.com/b.jpg',
                                                 'duration': config.CLIP_SECONDS, 'mode': config.KLING_MODE, 'aspect_ratio': '16:9', 'multi_shots': False, 'sound': 'off'})
        # +20% headroom for QA regenerations
        est = {'image': img, 'clip': clip, 'total': round((n * img * 1.2) + (n - 1) * clip, 2)}
    db.update(jid, estimate=est)
    _step(jid, 'estimating', f"Estimated cost ${est['total']:.2f}")
    if est['total'] > config.MAX_BUDGET_USD:
        raise RuntimeError(f"Estimated ${est['total']:.2f} is above the ${config.MAX_BUDGET_USD:.2f} limit (MAX_BUDGET_USD).")
    return est

def _keyframe_prompt(plan, scene, strict=False):
    suffix = plan['style_suffix']
    if scene['camera'] == 'top-down':
        suffix = re.sub(r'eye-level camera,?\s*', '', suffix) + ', top-down overhead camera'
    p = f"{scene['keyframe_prompt']} {suffix}"
    if scene.get('exclude'): p += f". Do not show: {scene['exclude']}."
    if strict: p = 'IMPORTANT: this is a different space from the reference images; use them only for materials, palette and light. ' + p
    return p

def _refs(plan, i, a, strict=False):
    scene, scenes = plan['scenes'][i], plan['scenes']
    anchor = next((j for j, s in enumerate(scenes) if s.get('is_anchor') and j < i), None)
    mode = 'anchor' if strict and scene['reference'] != 'none' else scene['reference']
    refs = []
    if 'previous' in mode and i > 0: refs.append(a[f'k{i-1:02d}']['remote'])
    if 'anchor' in mode and anchor is not None: refs.append(a[f'k{anchor:02d}']['remote'])
    if not refs and i > 0 and mode != 'none': refs.append(a[f'k{i-1:02d}']['remote'])
    return refs

def _gen_keyframe(jid, plan, i, a, strict=False):
    scene = plan['scenes'][i]
    path = os.path.join(job_dir(jid), 'keyframes', f'k{i+1:02d}.png')
    if config.DRY_RUN:
        src = os.path.join(config.DRY_RUN_MEDIA, 'keyframes', f'k{i+1:02d}.png')
        os.makedirs(os.path.dirname(path), exist_ok=True); shutil.copy(src, path); remote = 'dry-run'
    else:
        args = {'prompt': _keyframe_prompt(plan, scene, strict), 'aspect_ratio': '16:9', 'resolution': '2k', 'quality': 'medium'}
        refs = _refs(plan, i, a, strict)
        if refs: args['image_urls'] = refs
        res = hf.run(config.IMAGE_MODEL, args)
        remote = hf.output_url(res); hf.download(remote, path)
    # small preview the client can show in its shot deck
    prev = os.path.join(job_dir(jid), 'preview', f'k{i+1:02d}.jpg')
    os.makedirs(os.path.dirname(prev), exist_ok=True)
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', path, '-vf', 'scale=640:-2', prev], check=True)
    full = os.path.join(job_dir(jid), 'full', f'k{i+1:02d}.webp')   # background-ready still
    os.makedirs(os.path.dirname(full), exist_ok=True)
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', path, '-vf', 'scale=1920:-2', '-c:v', 'libwebp', '-quality', '82', full], check=True)
    info = {'file': path, 'remote': remote, 'public': f'{config.PUBLIC_BASE_URL}/media/{jid}/preview/k{i+1:02d}.jpg'}
    db.set_asset(jid, f'k{i:02d}', info)
    return info

def stage_keyframes(jid, plan):
    scenes = plan['scenes']
    for i, scene in enumerate(scenes):
        a = db.get(jid)['assets']
        if f'k{i:02d}' in a: continue
        _step(jid, 'keyframes', f'Generating keyframe {i+1} of {len(scenes)}: {scene["name"]}', index=i)
        info = _gen_keyframe(jid, plan, i, a)
        if i > 0:
            ok, problem = planner.check_keyframe(info['file'], scene, scenes[i-1]['name'])
            if not ok:
                _step(jid, 'keyframes', f'Keyframe {i+1} drifted ({problem}); regenerating', index=i)
                info = _gen_keyframe(jid, plan, i, db.get(jid)['assets'], strict=True)
        _step(jid, 'keyframes', f'Keyframe {i+1} ready', index=i, preview=info['public'])

def stage_clips(jid, plan):
    scenes, lock = plan['scenes'], threading.Lock()
    def one(i):
        a = db.get(jid)['assets']
        if f'clip_{i:02d}' in a: return
        path = os.path.join(job_dir(jid), 'clips', f'clip_{i+1:02d}.mp4')
        if config.DRY_RUN:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy(os.path.join(config.DRY_RUN_MEDIA, 'clips', f'clip_{i+1:02d}.mp4'), path)
        else:
            motion = plan['clips'][i]['motion_prompt']
            args = {'prompt': f'{motion} Steady, slow, continuous camera motion, no cuts, no shaking, consistent lighting, photorealistic.',
                    'first_frame_url': a[f'k{i:02d}']['remote'], 'last_frame_url': a[f'k{i+1:02d}']['remote'],
                    'duration': config.CLIP_SECONDS, 'mode': config.KLING_MODE, 'aspect_ratio': '16:9', 'multi_shots': False, 'sound': 'off'}
            res = hf.run(config.VIDEO_MODEL, args)
            hf.download(hf.output_url(res), path)
        # web copy for <video> backgrounds: H.264, no audio, fast start, 1080p
        web = os.path.join(job_dir(jid), 'web', f'clip_{i+1:02d}.mp4')
        os.makedirs(os.path.dirname(web), exist_ok=True)
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', path, '-an', '-vf', 'scale=1920:-2', '-c:v', 'libx264', '-crf', '23',
                        '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', web], check=True)
        db.set_asset(jid, f'clip_{i:02d}', {'file': path, 'web': web})
        with lock:
            done = sum(1 for k in db.get(jid)['assets'] if k.startswith('clip_'))
            _step(jid, 'clips', f'Clip {done} of {len(scenes)-1} ready', index=i)
    _step(jid, 'clips', f'Generating {len(scenes)-1} transition clips')
    with ThreadPoolExecutor(max_workers=config.CLIP_WORKERS) as ex:
        for f in [ex.submit(one, i) for i in range(len(scenes) - 1)]: f.result()

def stage_frames(jid, plan, out):
    """Clips -> one continuous WebP sequence (desktop 1600px, mobile 900px), duplicates dropped, scene start frames recorded."""
    _step(jid, 'frames', 'Extracting frames')
    tmp = os.path.join(job_dir(jid), 'tmp_frames')
    shutil.rmtree(tmp, ignore_errors=True)
    for d in ('frames/sm', 'stills'): os.makedirs(os.path.join(out, d), exist_ok=True)
    keys, n = [1], 0
    for i in range(len(plan['scenes']) - 1):
        clip = os.path.join(job_dir(jid), 'clips', f'clip_{i+1:02d}.mp4')
        for w, q in ((1600, 70), (900, 64)):
            d = os.path.join(tmp, str(i), str(w)); os.makedirs(d, exist_ok=True)
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', clip, '-vf', f'fps=24,scale={w}:-2:flags=lanczos',
                            '-c:v', 'libwebp', '-quality', str(q), '-compression_level', '6', '-preset', 'photo', f'{d}/%05d.webp'], check=True)
        files = sorted(os.listdir(os.path.join(tmp, str(i), '1600')))
        for j, f in enumerate(files):
            if i > 0 and j == 0: continue
            n += 1; name = f'f_{n:04d}.webp'
            shutil.move(os.path.join(tmp, str(i), '1600', f), os.path.join(out, 'frames', name))
            sm = os.path.join(tmp, str(i), '900', f)
            if os.path.exists(sm): shutil.move(sm, os.path.join(out, 'frames', 'sm', name))
        keys.append(n)
    shutil.rmtree(tmp, ignore_errors=True)
    for k, f in enumerate(keys):
        shutil.copy(os.path.join(out, 'frames', f'f_{f:04d}.webp'), os.path.join(out, 'stills', f'k{k+1:02d}.webp'))
    man = {'count': n, 'fps': 24, 'width': 1600, 'keys': keys}
    json.dump(man, open(os.path.join(out, 'frames', 'manifest.json'), 'w'))
    _step(jid, 'frames', f'{n} frames, scenes start at {keys}')
    return man

# ---------------- orchestration ----------------
def run(jid):
    job = db.get(jid)
    try:
        db.update(jid, status='running', error=None)
        plan = stage_plan(jid, job)
        stage_estimate(jid, db.get(jid), plan)
        stage_keyframes(jid, plan)
        job = db.get(jid)
        if config.REQUIRE_APPROVAL and not (job.get('options') or {}).get('approved'):
            db.update(jid, status='awaiting_approval')
            _step(jid, 'review', 'Keyframes ready for review. Approve to generate the clips.')
            return
        stage_clips(jid, plan)
        slug = (job.get('options') or {}).get('slug') or f"{slugify(plan.get('brand', 'site'))}-{jid[:6]}"
        out = site_dir(slug)
        shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
        man = stage_frames(jid, plan, out)
        db.set_asset(jid, 'film', {'base_url': f'{config.PUBLIC_BASE_URL}/sites/{slug}/', 'count': man['count'], 'fps': man['fps'], 'keys': man['keys'],
                                   'frame_pattern': 'frames/f_{0000}.webp', 'mobile_pattern': 'frames/sm/f_{0000}.webp', 'stills_pattern': 'stills/k{00}.webp'})
        _step(jid, 'building', 'Building the site')
        sitebuilder.build(plan, out, (job.get('options') or {}).get('form_endpoint'))
        url = f'{config.PUBLIC_BASE_URL}/sites/{slug}/'
        db.update(jid, status='done', site_url=url)
        _step(jid, 'done', 'Site published', url=url)
    except Exception as e:
        traceback.print_exc()
        db.update(jid, status='failed', error=str(e)[:500])
        _step(jid, 'failed', str(e)[:300])

_pool = ThreadPoolExecutor(max_workers=config.JOB_WORKERS)
def enqueue(jid): _pool.submit(run, jid)

def resume_unfinished():
    for j in db.recent(200):
        if j['status'] in ('queued', 'running'): enqueue(j['id'])
