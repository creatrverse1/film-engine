"""Fill the site template with a plan. Plan text is JSON-encoded into a script tag and rendered as text by the page,
so nothing the model writes is ever inserted as HTML."""
import html, json, os, re

TEMPLATE = os.path.join(os.path.dirname(__file__), 'template', 'site.html')
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
DEFAULTS = {'light': '#F5F0E8', 'mid': '#D9CBB4', 'accent': '#5C4033', 'dark': '#2B2B2B'}

def build(plan, out_dir, form_endpoint=None):
    pal = {k: (v if HEX.match(str(v or '')) else DEFAULTS[k]) for k, v in {**DEFAULTS, **(plan.get('palette') or {})}.items() if k in DEFAULTS}
    data = {k: plan.get(k) for k in ('brand', 'tagline', 'scenes', 'cta', 'footer')}
    data['form_endpoint'] = form_endpoint or ''
    blob = json.dumps(data).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = open(TEMPLATE).read()
    for key, val in {'__TITLE__': html.escape(plan.get('brand', 'Site')), '__DESCRIPTION__': html.escape(plan.get('tagline', '')),
                     '__LIGHT__': pal['light'], '__MID__': pal['mid'], '__ACCENT__': pal['accent'], '__DARK__': pal['dark'],
                     '__PLAN_JSON__': blob}.items():
        page = page.replace(key, val)
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, 'index.html'), 'w').write(page)
    json.dump(plan, open(os.path.join(out_dir, 'plan.json'), 'w'), indent=2)
