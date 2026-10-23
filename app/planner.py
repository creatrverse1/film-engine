"""Claude does the thinking parts: turn a short brief into a full film plan, and check each keyframe."""
import base64, io, json, urllib.request, urllib.error, time
from . import config

API = 'https://api.anthropic.com/v1/messages'

PLAN_TOOL = {
  'name': 'film_plan',
  'description': 'The complete plan for one scroll-driven film website.',
  'input_schema': {
    'type': 'object',
    'required': ['brand', 'tagline', 'palette', 'style_suffix', 'scenes', 'clips', 'cta'],
    'properties': {
      'brand': {'type': 'string'},
      'tagline': {'type': 'string', 'description': 'One short line under the brand in page metadata.'},
      'palette': {'type': 'object', 'required': ['light', 'mid', 'accent', 'dark'],
                  'properties': {k: {'type': 'string', 'description': 'hex like #F5F0E8'} for k in ['light', 'mid', 'accent', 'dark']}},
      'style_suffix': {'type': 'string', 'description': 'Appended to every keyframe prompt: style, palette, light, photography language, 16:9, no people, no text.'},
      'scenes': {
        'type': 'array', 'minItems': 6, 'maxItems': 11,
        'items': {
          'type': 'object',
          'required': ['name', 'camera', 'keyframe_prompt', 'reference'],
          'properties': {
            'name': {'type': 'string', 'description': '1-2 words, shown in the scene counter.'},
            'nav_label': {'type': 'string', 'description': 'Label in the top nav, or empty. Use for at most 5 scenes.'},
            'camera': {'type': 'string', 'enum': ['top-down', 'eye-level', 'aerial', 'exterior']},
            'keyframe_prompt': {'type': 'string', 'description': 'Self-contained description of this exact frame. Say what stays the same as the previous frame and what changes.'},
            'reference': {'type': 'string', 'enum': ['none', 'previous', 'anchor', 'previous+anchor'],
                          'description': 'Which earlier keyframes to pass as reference images. First scene: none. Same space, new state: previous. New room/space: anchor (or previous+anchor if the camera literally looks from one into the other).'},
            'is_anchor': {'type': 'boolean', 'description': 'True for exactly one scene: the most finished, representative frame, used as the style reference for later new spaces.'},
            'exclude': {'type': 'string', 'description': 'Things that must NOT appear (helps stop the model repeating the previous room).'},
            'copy': {'type': 'object', 'description': 'Overlay text for this scene, or omit for a scene with no text.',
                     'properties': {'kicker': {'type': 'string'}, 'headline': {'type': 'string', 'description': 'Under 8 words. Wrap one or two words in *asterisks* for italic.'},
                                    'body': {'type': 'string', 'description': 'Under 30 words.'}, 'cta': {'type': 'boolean'}}},
          }}},
      'clips': {'type': 'array', 'description': 'Exactly scenes-1 transitions, in order.',
                'items': {'type': 'object', 'required': ['motion_prompt'], 'properties': {'motion_prompt': {'type': 'string', 'description': 'One sentence of camera/scene motion from this keyframe to the next.'}}}},
      'cta': {'type': 'object', 'required': ['headline', 'body', 'project_types'],
              'properties': {'headline': {'type': 'string'}, 'body': {'type': 'string'},
                             'project_types': {'type': 'array', 'items': {'type': 'string'}},
                             'budget_ranges': {'type': 'array', 'items': {'type': 'string'}}}},
      'footer': {'type': 'object', 'properties': {'address': {'type': 'string'}, 'email': {'type': 'string'}}},
    }}}

PLANNER_SYSTEM = """You plan cinematic scroll-driven websites. The site is ONE continuous camera journey: a sequence of keyframe images joined by short image-to-video clips (each clip starts on one keyframe and ends on the next), scrubbed by scroll.

Rules for a plan that generates well:
- 6 to 11 scenes. The last scene is where the booking/contact form sits, so make it a wide, calm establishing view (often an exterior or a pull-back).
- One location and one visual world throughout: same materials, palette and time of day unless the brief asks otherwise.
- A strong opening is a transformation (a drawing, sketch, empty site or bare room that becomes the finished thing) followed by moving through spaces.
- Each keyframe prompt must stand alone and be concrete: camera position, materials, key objects, light. For a new space, describe it fully and use 'exclude' to name the previous space so it isn't repeated (e.g. 'no bed, no bedroom').
- Consecutive keyframes must be reachable by one slow, continuous camera move. Avoid 180-degree turns in a single clip.
- Clip motion prompts: one sentence, slow and steady (dolly, glide, push in, pull back, orbit, tilt, lower).
- Overlay copy: short, specific, written for the brand's customer, no clichés. Not every scene needs copy; 5-7 scenes with copy is ideal. Mark one early scene with cta=true.
- Never include real people's names, logos or trademarks in image prompts. No people in frames unless the brief requires it.
- style_suffix ends with: 'photorealistic, 16:9, no people, no text'. For top-down scenes the camera wording is handled for you."""

def _call(body, attempts=3):
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY is not set')
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method='POST', headers={
        'x-api-key': config.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read()[:500].decode(errors='replace')
            if e.code in (429, 500, 529) and i < attempts - 1: time.sleep(5 * (i + 1)); continue
            raise RuntimeError(f'Claude API {e.code}: {msg}')

def _tool_input(resp, name):
    for block in resp.get('content', []):
        if block.get('type') == 'tool_use' and block.get('name') == name:
            return block['input']
    raise RuntimeError('Claude did not return a structured result')

def make_plan(prompt, options):
    if config.DRY_RUN:
        return json.load(open(config.DRY_RUN_PLAN))
    brief = prompt.strip()
    if options.get('brand'): brief += f"\nBrand name: {options['brand']}"
    if options.get('scenes'): brief += f"\nUse exactly {int(options['scenes'])} scenes."
    resp = _call({
        'model': config.CLAUDE_MODEL, 'max_tokens': 8000, 'system': PLANNER_SYSTEM,
        'tools': [PLAN_TOOL], 'tool_choice': {'type': 'tool', 'name': 'film_plan'},
        'messages': [{'role': 'user', 'content': f'Plan the site for this brief:\n\n{brief}'}]})
    plan = _tool_input(resp, 'film_plan')
    validate(plan)
    return plan

def validate(plan):
    s = plan['scenes']
    if not 2 <= len(s) <= 12: raise ValueError('plan needs 2-12 scenes')
    if len(plan['clips']) != len(s) - 1:
        plan['clips'] = (plan['clips'] + [{'motion_prompt': 'The camera glides slowly forward.'}] * len(s))[:len(s) - 1]
    s[0]['reference'] = 'none'
    if not any(x.get('is_anchor') for x in s):
        s[min(len(s) - 2, max(1, len(s) // 2 - 1))]['is_anchor'] = True
    return plan

# ---------- keyframe QA ----------
QA_TOOL = {'name': 'verdict', 'description': 'Does the image match the brief?',
           'input_schema': {'type': 'object', 'required': ['ok', 'problem'],
                            'properties': {'ok': {'type': 'boolean'}, 'problem': {'type': 'string', 'description': 'Empty if ok; otherwise what is wrong, in one sentence.'}}}}

def check_keyframe(image_path, scene, prev_scene_name=None):
    """Returns (ok, problem). Catches the common failure: the model repeats the previous room instead of making the new one."""
    if config.DRY_RUN or not config.QA_ENABLED:
        return True, ''
    from PIL import Image
    im = Image.open(image_path).convert('RGB'); im.thumbnail((1024, 1024))
    buf = io.BytesIO(); im.save(buf, 'JPEG', quality=82)
    text = (f"Brief for this frame:\n{scene['keyframe_prompt']}\n"
            + (f"Must NOT show: {scene['exclude']}\n" if scene.get('exclude') else '')
            + (f"The previous frame was the '{prev_scene_name}' scene; this one is '{scene['name']}'.\n" if prev_scene_name else '')
            + "Judge only big mismatches: wrong room or space, missing the main subject, wrong camera angle (e.g. not top-down when it should be), visible text or people when not wanted. Ignore small differences.")
    resp = _call({'model': config.CLAUDE_MODEL, 'max_tokens': 400, 'tools': [QA_TOOL], 'tool_choice': {'type': 'tool', 'name': 'verdict'},
                  'messages': [{'role': 'user', 'content': [
                      {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(buf.getvalue()).decode()}},
                      {'type': 'text', 'text': text}]}]})
    v = _tool_input(resp, 'verdict')
    return bool(v['ok']), v.get('problem', '')
