"""Higgsfield API client: estimate, submit, poll, download. Credentials come from environment variables only."""
import json, os, time, uuid, urllib.request, urllib.error
from . import config

API = 'https://api.higgsfield.ai'

class HiggsfieldError(RuntimeError):
    pass

def _auth():
    if not (config.HF_API_KEY_ID and config.HF_API_KEY_SECRET):
        raise HiggsfieldError('HF_API_KEY_ID / HF_API_KEY_SECRET are not set')
    return f'Key {config.HF_API_KEY_ID}:{config.HF_API_KEY_SECRET}'

def _req(method, url, body=None, idem=None):
    h = {'Authorization': _auth(), 'Content-Type': 'application/json', 'Accept': 'application/json'}
    if idem: h['Idempotency-Key'] = idem
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=60) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            msg = e.read()[:500].decode(errors='replace')
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 3); continue
            if 'not_enough_credits' in msg:
                raise HiggsfieldError('Higgsfield balance is too low. Top up at console.higgsfield.ai.')
            raise HiggsfieldError(f'{method} {url.split("?")[0]} -> {e.code}: {msg}')
        except urllib.error.URLError as e:
            if attempt < 3: time.sleep(3); continue
            raise HiggsfieldError(f'network error: {e}')

def estimate(endpoint, args):
    r = _req('POST', f'{API}/estimate/{endpoint}', args)
    return float(r.get('usd') or 0)

def run(endpoint, args, retries=1, on_submit=None, timeout=1800):
    """Submit, poll to a terminal state, return the completed response. Failed/nsfw are retried (and not charged)."""
    last = None
    for _ in range(retries + 1):
        sub = _req('POST', f'{API}/{endpoint}', args, str(uuid.uuid4()))
        if on_submit: on_submit(sub.get('request_id'))
        t0, delay = time.time(), 5
        while True:
            s = _req('GET', sub['status_url'])
            if s.get('status') in ('completed', 'failed', 'nsfw', 'canceled'): break
            if time.time() - t0 > timeout: raise HiggsfieldError(f'timed out after {timeout}s')
            time.sleep(delay); delay = min(20, delay * 1.3)
        if s['status'] == 'completed': return s
        last = f"{s['status']}: {s.get('error')}"
    raise HiggsfieldError(f'generation failed ({last})')

def output_url(res):
    if res.get('images'): return res['images'][0]['url']
    if res.get('video'): return res['video']['url']
    raise HiggsfieldError(f'no output in response: {list(res)}')

def download(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'film-engine'}), timeout=300) as r, open(path, 'wb') as f:
        while chunk := r.read(1 << 20): f.write(chunk)
    return path
