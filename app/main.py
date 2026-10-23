"""Film Engine API.

POST /api/sites                 start a build      {prompt, brand?, scenes?, slug?, form_endpoint?, callback_url?, auto_approve?}
GET  /api/sites/{id}            status, keyframe previews, events, site_url
POST /api/sites/{id}/approve    continue to clips after keyframe review
POST /api/sites/{id}/regenerate {index, note?}  redo one keyframe before approving
GET  /api/sites                 recent jobs
GET  /sites/{slug}/             the published site
"""
import os
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import config, db, pipeline

app = FastAPI(title='Film Engine', version='1.0')
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','), allow_methods=['*'], allow_headers=['*'])

def auth(authorization: str = Header(default='')):
    if config.API_TOKEN and authorization != f'Bearer {config.API_TOKEN}':
        raise HTTPException(401, 'Missing or wrong API token')

class NewSite(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)
    brand: str | None = None
    scenes: int | None = Field(default=None, ge=4, le=11)
    slug: str | None = Field(default=None, pattern=r'^[a-z0-9-]{3,40}$')
    form_endpoint: str | None = None
    callback_url: str | None = None
    auto_approve: bool = False

class Regenerate(BaseModel):
    index: int
    note: str | None = Field(default=None, max_length=500)

@app.post('/api/sites', dependencies=[Depends(auth)])
def create_site(body: NewSite):
    opts = {'brand': body.brand, 'scenes': body.scenes, 'slug': body.slug, 'form_endpoint': body.form_endpoint, 'approved': body.auto_approve}
    jid = db.create(body.prompt, opts, body.callback_url)
    pipeline.enqueue(jid)
    return pipeline.public_view(db.get(jid))

@app.get('/api/sites', dependencies=[Depends(auth)])
def list_sites():
    return [pipeline.public_view(j) for j in db.recent(30)]

@app.get('/api/sites/{jid}', dependencies=[Depends(auth)])
def get_site(jid: str):
    j = db.get(jid)
    if not j: raise HTTPException(404, 'No such job')
    return pipeline.public_view(j)

@app.post('/api/sites/{jid}/approve', dependencies=[Depends(auth)])
def approve(jid: str):
    j = db.get(jid)
    if not j: raise HTTPException(404, 'No such job')
    if j['status'] != 'awaiting_approval': raise HTTPException(409, f"Job is {j['status']}, not awaiting approval")
    db.update(jid, options={**j['options'], 'approved': True}, status='queued')
    pipeline.enqueue(jid)
    return pipeline.public_view(db.get(jid))

@app.post('/api/sites/{jid}/regenerate', dependencies=[Depends(auth)])
def regenerate(jid: str, body: Regenerate):
    j = db.get(jid)
    if not j: raise HTTPException(404, 'No such job')
    if j['status'] != 'awaiting_approval': raise HTTPException(409, 'Keyframes can only be redone while awaiting approval')
    plan, n = j['plan'], len(j['plan']['scenes'])
    if not 0 <= body.index < n: raise HTTPException(400, f'index must be 0..{n-1}')
    if body.note:
        plan['scenes'][body.index]['keyframe_prompt'] += f' {body.note}'
        db.update(jid, plan=plan)
    # drop this keyframe and every later one (later ones used it as a reference), then rerun
    assets = {k: v for k, v in j['assets'].items() if not (k.startswith('k') and k[1:].isdigit() and int(k[1:]) >= body.index)}
    db.update(jid, assets=assets, status='queued')
    pipeline.enqueue(jid)
    return pipeline.public_view(db.get(jid))

@app.get('/health')
def health():
    return {'ok': True, 'dry_run': config.DRY_RUN, 'higgsfield_key': bool(config.HF_API_KEY_ID), 'anthropic_key': bool(config.ANTHROPIC_API_KEY)}

os.makedirs(os.path.join(config.DATA_DIR, 'sites'), exist_ok=True)
os.makedirs(os.path.join(config.DATA_DIR, 'jobs'), exist_ok=True)
app.mount('/sites', StaticFiles(directory=os.path.join(config.DATA_DIR, 'sites'), html=True), name='sites')

class Previews(StaticFiles):
    """Serve only the web-ready outputs from job folders: preview JPGs, full WebP stills, web MP4 clips."""
    ALLOWED = (('/preview/', '.jpg'), ('/full/', '.webp'), ('/web/', '.mp4'))
    async def get_response(self, path, scope):
        p = '/' + path
        if not any(d in p and p.endswith(ext) for d, ext in self.ALLOWED): raise HTTPException(404)
        return await super().get_response(path, scope)
app.mount('/media', Previews(directory=os.path.join(config.DATA_DIR, 'jobs')), name='media')

@app.on_event('startup')
def startup():
    pipeline.resume_unfinished()
