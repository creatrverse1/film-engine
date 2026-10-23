import os

def env(name, default=None, cast=str):
    v = os.environ.get(name, default)
    return cast(v) if v is not None else None

HF_API_KEY_ID      = env('HF_API_KEY_ID')
HF_API_KEY_SECRET  = env('HF_API_KEY_SECRET')
ANTHROPIC_API_KEY  = env('ANTHROPIC_API_KEY')
CLAUDE_MODEL       = env('CLAUDE_MODEL', 'claude-sonnet-5-5')

API_TOKEN          = env('API_TOKEN')                 # shared secret your app sends as: Authorization: Bearer <API_TOKEN>
PUBLIC_BASE_URL    = env('PUBLIC_BASE_URL', 'http://localhost:8000').rstrip('/')
DATA_DIR           = env('DATA_DIR', './data')

IMAGE_MODEL        = env('IMAGE_MODEL', 'xai/grok-imagine-image-2.0')
VIDEO_MODEL        = env('VIDEO_MODEL', 'kling-video/o3/first-last-frame')
KLING_MODE         = env('KLING_MODE', 'pro')
CLIP_SECONDS       = env('CLIP_SECONDS', '5', int)
CLIP_WORKERS       = env('CLIP_WORKERS', '5', int)

MAX_BUDGET_USD     = env('MAX_BUDGET_USD', '15', float)   # refuse jobs whose estimate is above this
REQUIRE_APPROVAL   = env('REQUIRE_APPROVAL', 'true').lower() == 'true'  # pause after keyframes for a human yes
QA_ENABLED         = env('QA_ENABLED', 'true').lower() == 'true'
JOB_WORKERS        = env('JOB_WORKERS', '2', int)        # how many sites build at once

# DRY_RUN=1 skips every paid call: plan comes from DRY_RUN_PLAN, keyframes/clips from DRY_RUN_MEDIA (local files)
DRY_RUN            = env('DRY_RUN', '0') == '1'
DRY_RUN_PLAN       = env('DRY_RUN_PLAN')
DRY_RUN_MEDIA      = env('DRY_RUN_MEDIA')
