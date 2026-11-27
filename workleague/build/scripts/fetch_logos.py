#!/usr/bin/env python3
"""Download each season's team logos -> ../logos/<year>/<owner-slug>.<ext> (skips files already on disk), then
pick a key color per logo -> data/processed/logo_colors.json."""
import colorsys, json, re, sys, warnings
from pathlib import Path
import requests
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import build_data as bd

slug = lambda o: re.sub(r"[^a-z0-9]+", "-", o.lower()).strip("-")
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg"}
years = bd.load_years(); owner_of = bd.build_owner_names(years)
for y, d in years.items():
    for t in d["teams"]:
        url, o = t.get("logo"), owner_of[t["primaryOwner"]]
        if not url: continue
        out = bd.LOGO_DIR / str(y)
        if list(out.glob(slug(o) + ".*")): continue
        try:
            r = requests.get(url, timeout=30)
            ext = EXT.get(r.headers.get("content-type", "").split(";")[0])
            if r.status_code != 200 or not ext: print("skip", y, o, r.status_code); continue
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{slug(o)}.{ext}").write_bytes(r.content)
        except Exception as e:
            print("fail", y, o, e)

from PIL import Image
def key_color(im):
    bins = {}
    for r, g, b, a in im.getdata():
        if a < 200: continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.35 or v < 0.25: continue
        k = int(h * 24) % 24; w = s * v
        t = bins.setdefault(k, [0, 0, 0, 0]); t[0] += w; t[1] += h * w; t[2] += s * w; t[3] += v * w
    if not bins: return None
    w, h, s, v = max(bins.values(), key=lambda t: t[0]); h, s, v = h / w, s / w, v / w
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.6), max(v, 0.78))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

owners = {slug(o): o for o in set(owner_of.values())}
cols = {}
for f in sorted(bd.LOGO_DIR.glob("*/*")):
    if f.suffix == ".svg" or f.stem not in owners or not f.parent.name.isdigit(): continue
    try:
        c = key_color(Image.open(f).convert("RGBA").resize((64, 64)))
    except Exception: c = None
    if c: cols[f"{f.parent.name}|{owners[f.stem]}"] = c
bd.OUT.mkdir(parents=True, exist_ok=True)
(bd.OUT / "logo_colors.json").write_text(json.dumps(cols, indent=0, sort_keys=True))
print(len(list(bd.LOGO_DIR.glob('*/*'))), "logos,", len(cols), "colors")
