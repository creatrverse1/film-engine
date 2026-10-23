"""Tiny SQLite job store. One row per site build; JSON columns hold the plan, assets and event log."""
import json, os, sqlite3, threading, time, uuid
from . import config

_lock = threading.Lock()
os.makedirs(config.DATA_DIR, exist_ok=True)
DB = os.path.join(config.DATA_DIR, 'jobs.sqlite')

def _conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c

with _conn() as c:
    c.execute('''create table if not exists jobs(
        id text primary key, created real, updated real,
        prompt text, options text, status text, stage text,
        plan text, assets text, events text, estimate text,
        site_url text, error text, callback_url text)''')

JSON_COLS = ('options', 'plan', 'assets', 'events', 'estimate')

def _row(r):
    if not r: return None
    d = dict(r)
    for k in JSON_COLS:
        d[k] = json.loads(d[k]) if d[k] else ({} if k != 'events' else [])
    return d

def create(prompt, options, callback_url=None):
    jid = uuid.uuid4().hex[:12]
    now = time.time()
    with _lock, _conn() as c:
        c.execute('insert into jobs values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (jid, now, now, prompt, json.dumps(options), 'queued', 'queued',
                   None, json.dumps({}), json.dumps([]), None, None, None, callback_url))
    return jid

def get(jid):
    with _conn() as c:
        return _row(c.execute('select * from jobs where id=?', (jid,)).fetchone())

def update(jid, **fields):
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f'{k}=?'); vals.append(json.dumps(v) if k in JSON_COLS else v)
    sets.append('updated=?'); vals.append(time.time())
    with _lock, _conn() as c:
        c.execute(f'update jobs set {", ".join(sets)} where id=?', (*vals, jid))

def set_asset(jid, key, value):
    with _lock, _conn() as c:
        r = c.execute('select assets from jobs where id=?', (jid,)).fetchone()
        a = json.loads(r['assets'] or '{}'); a[key] = value
        c.execute('update jobs set assets=?, updated=? where id=?', (json.dumps(a), time.time(), jid))

def log(jid, stage, message, **extra):
    with _lock, _conn() as c:
        r = c.execute('select events from jobs where id=?', (jid,)).fetchone()
        ev = json.loads(r['events'] or '[]')
        ev.append({'t': time.time(), 'stage': stage, 'message': message, **extra})
        c.execute('update jobs set events=?, stage=?, updated=? where id=?', (json.dumps(ev[-300:]), stage, time.time(), jid))

def recent(limit=20):
    with _conn() as c:
        return [_row(r) for r in c.execute('select * from jobs order by created desc limit ?', (limit,)).fetchall()]
