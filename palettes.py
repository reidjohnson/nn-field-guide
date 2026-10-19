#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Reid Johnson
# SPDX-License-Identifier: MIT
"""
palettes.py: proofs of the field guide in other color schemes.

Renders the real poster once (guide.py), tags every cell fill, outline and inner glyph with
its job, and writes one page where the poster can be recolored by scheme and seen as
color-blind readers and grayscale prints would see it, beside measurements of each scheme:

  proofs/palettes.html    the page, as a fragment ready to publish

Measurements: color blindness simulated after Machado, Oliveira and
Fernandes (IEEE TVCG 2009) at full severity, grayscale as relative luminance, and the
smallest CIE76 difference between any two of the five jobs' fills. Contrast is WCAG's ratio.

Usage:  python palettes.py
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guide  # noqa: E402

JOBS = [g for g, v in guide.GROUPS.items() if v.get("job", True)]  # the five jobs; not Network
VISIONS = [
    ("normal", "Normal"),
    ("protan", "Protanopia"),
    ("deutan", "Deuteranopia"),
    ("tritan", "Tritanopia"),
    ("gray", "Grayscale"),
]
GLYPH = guide.GLYPH_INK
LIGHT, DARK = guide.BG_LIGHT, guide.BG_DARK

# Each scheme: its fills by job, in the Zoo's hue order where the scheme allows
# (input yellow, hidden green, state blue, mixing pink or purple, output orange or red).
CURRENT = "default"  # the scheme the guide uses (guide.PALETTE); the page opens on it
ZOO_OUTLINES = dict(
    input="#D9A400", hidden="#4C9900", state="#0077CC", mixing="#D673D6", output="#CC5200"
)
SCHEMES = [
    dict(
        id="default",
        name="Default",
        source="This guide",
        note="The guide's five colors.",
        fills=dict(
            input="#E9DD03", hidden="#3FBF0A", state="#578DE6", mixing="#F4A1FD", output="#CE481E"
        ),
    ),
    dict(
        id="zoo",
        name="Zoo (2019)",
        source="van Veen and Leijnen, The Neural Network Zoo",
        note="The original chart's five colors.",
        fills=dict(
            input="#FFCC00", hidden="#66CC00", state="#0099FF", mixing="#FF99FF", output="#FF6600"
        ),
    ),
    dict(
        id="okabe",
        name="Okabe and Ito",
        source="Okabe and Ito, Color Universal Design, 2008",
        note="The palette designed to stay distinct for every common kind of color vision.",
        fills=dict(
            input="#F0E442", hidden="#009E73", state="#0072B2", mixing="#CC79A7", output="#D55E00"
        ),
    ),
    dict(
        id="bright",
        name="Tol bright",
        source="Paul Tol, Colour Schemes, SRON technote, 2021",
        note="Bright colors for lines and markers that stay distinct under color blindness.",
        fills=dict(
            input="#CCBB44", hidden="#228833", state="#66CCEE", mixing="#AA3377", output="#EE6677"
        ),
    ),
    dict(
        id="light",
        name="Tol light",
        source="Paul Tol, Colour Schemes, SRON technote, 2021",
        note="Pale fills made to carry dark marks and text.",
        fills=dict(
            input="#EEDD88", hidden="#BBCC33", state="#77AADD", mixing="#FFAABB", output="#EE8866"
        ),
    ),
    dict(
        id="muted",
        name="Tol muted",
        source="Paul Tol, Colour Schemes, SRON technote, 2021",
        note="A softer set with a printed look.",
        fills=dict(
            input="#DDCC77", hidden="#44AA99", state="#88CCEE", mixing="#AA4499", output="#CC6677"
        ),
    ),
]

# --------------------------------------------------------------------------
# Color math
# --------------------------------------------------------------------------

MACHADO = {  # severity 1.0, applied to linear RGB
    "protan": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
    "deutan": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ],
    "tritan": [
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809, 0.147602],
        [0.004733, 0.691367, 0.303900],
    ],
}


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def rgb2hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in rgb)


def lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def delin(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def luminance(h):
    r, g, b = (lin(c) for c in hex2rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def simulate(h, vision):
    if vision == "normal":
        return h.upper()
    r, g, b = (lin(c) for c in hex2rgb(h))
    if vision == "gray":
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return rgb2hex((delin(y),) * 3)
    m = MACHADO[vision]
    out = [max(0.0, min(1.0, m[i][0] * r + m[i][1] * g + m[i][2] * b)) for i in range(3)]
    return rgb2hex(tuple(delin(c) for c in out))


def lab(h):
    r, g, b = (lin(c) for c in hex2rgb(h))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab2hex(L, a, b):
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def finv(t):
        return t**3 if t**3 > 216 / 24389 else (116 * t - 16) / (24389 / 27)

    x, y, z = finv(fx) * 0.95047, finv(fy), finv(fz) * 1.08883
    rgb = (
        3.2404542 * x - 1.5371385 * y - 0.4985314 * z,
        -0.9692660 * x + 1.8760108 * y + 0.0415560 * z,
        0.0556434 * x - 0.2040259 * y + 1.0572252 * z,
    )
    return rgb2hex(tuple(delin(max(0.0, min(1.0, c))) for c in rgb))


def outline_of(fill):
    """An outline 14 L* darker than the fill, the way the Zoo's own outlines sit."""
    L, a, b = lab(fill)
    return lab2hex(max(0.0, L - 14), a, b)


def closest(fills, vision):
    sim = {j: simulate(fills[j], vision) for j in JOBS}
    best = (1e9, None)
    for i, a in enumerate(JOBS):
        for b in JOBS[i + 1 :]:
            d = math.dist(lab(sim[a]), lab(sim[b]))
            if d < best[0]:
                best = (d, (a, b))
    return best


# --------------------------------------------------------------------------
# The poster, tagged so CSS can recolor it
# --------------------------------------------------------------------------

DEFAULT_HEX = {}
for job in JOBS:
    fill, stroke = guide.PALETTE[guide.GROUPS[job]["color"]]
    DEFAULT_HEX[fill.upper()] = f"f-{job}"
    DEFAULT_HEX[stroke.upper()] = f"s-{job}"


def tagged_poster(doc, diagrams):
    """The light poster with ink from currentColor, a transparent ground, and a class on every
    element that paints a job's fill (f-<job>), a job's outline (s-<job>) or an inner glyph (glyph).
    """
    guide.INK_LIGHT, guide.BG_LIGHT = "currentColor", "transparent"
    svg = guide.poster_svg(doc, diagrams, "light")

    def tag(m):
        t = m.group(0)
        classes = []
        for attr in ("fill", "stroke"):
            v = re.search(rf'\b{attr}="(#[0-9A-Fa-f]{{6}})"', t)
            if v and v.group(1).upper() in DEFAULT_HEX:
                classes.append(DEFAULT_HEX[v.group(1).upper()])
            elif v and attr == "stroke" and v.group(1).upper() == GLYPH.upper():
                classes.append("glyph")
        if not classes:
            return t
        return (
            t[:-2] + f' class="{" ".join(classes)}"/>'
            if t.endswith("/>")
            else t[:-1] + f' class="{" ".join(classes)}">'
        )

    svg = re.sub(r"<(?:circle|rect|polygon|g)\b[^>]*>", tag, svg)
    svg = svg.replace('width="', 'data-w="', 1).replace(
        'height="', 'data-h="', 1
    )  # let CSS size it
    return svg


# --------------------------------------------------------------------------
# Measurements and the page
# --------------------------------------------------------------------------


def scheme_data(s):
    fills = s["fills"]
    outlines = dict(ZOO_OUTLINES) if s["id"] == "zoo" else {j: outline_of(fills[j]) for j in JOBS}
    vars_, close = {}, {}
    for v, _ in VISIONS:
        vars_[v] = {
            **{f"f-{j}": simulate(fills[j], v) for j in JOBS},
            **{f"s-{j}": simulate(outlines[j], v) for j in JOBS},
            "glyph": simulate(GLYPH, v),
        }
        d, pair = closest(fills, v)
        close[v] = dict(de=round(d, 1), pair=list(pair))
    return dict(
        id=s["id"],
        name=s["name"],
        source=s["source"],
        note=s["note"],
        fills=fills,
        outlines=outlines,
        vars=vars_,
        closest=close,
        glyph=round(min(contrast(GLYPH, fills[j]) for j in JOBS), 1),
        on_light=round(min(contrast(outlines[j], LIGHT) for j in JOBS), 1),
        on_dark=round(min(contrast(fills[j], DARK) for j in JOBS), 1),
    )


def dots(colors, outlines):
    out = []
    for k, (c, o) in enumerate(zip(colors, outlines)):
        out.append(
            f'<circle cx="{9 + k * 17}" cy="9" r="6.5" fill="{c}" stroke="{o}" stroke-width="1"/>'
        )
    return f'<svg class="dots" viewBox="0 0 86 18" aria-hidden="true" focusable="false">{"".join(out)}</svg>'


PAGE_CSS = r"""
/* Layout: a proofing desk. A short header, the measurements as one table, then a sticky
   bar of scheme and vision controls over the full poster, recolored in place. */
:root {
  --bg: #FFFFFF; --surface: #F4F4F1; --ink: #1C1B19; --muted: #66655F; --rule: #E3E2DD;
  --focus: #0077CC; --on: #1C1B19; --on-ink: #FFFFFF; --flag: #B3261E;
  --display: 'Poppins', 'Avenir Next', 'Segoe UI', Helvetica, Arial, sans-serif;
  --body: 'Lato', 'Helvetica Neue', Helvetica, Arial, sans-serif;
  --mono: 'Noto Sans Mono', ui-monospace, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141413; --surface: #1F1F1D; --ink: #ECEBE6; --muted: #A3A19A; --rule: #302F2C;
    --focus: #4DB8FF; --on: #ECEBE6; --on-ink: #141413; --flag: #FF8A80; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #141413; --surface: #1F1F1D; --ink: #ECEBE6; --muted: #A3A19A; --rule: #302F2C;
  --focus: #4DB8FF; --on: #ECEBE6; --on-ink: #141413; --flag: #FF8A80; color-scheme: dark;
}
:root[data-theme="light"] { color-scheme: light; }
body { background: var(--bg); color: var(--ink); font: 15px/1.55 var(--body); margin: 0; }
.page { max-width: 1240px; margin: 0 auto; box-sizing: border-box; padding-block: 32px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.topbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.eyebrow { font: 500 11px/1.2 var(--display); letter-spacing: 0.13em; text-transform: uppercase; color: var(--muted); }
h1 { font: 500 clamp(28px, 4.6vw, 44px)/1.08 var(--display); letter-spacing: -0.01em; margin: 10px 0 8px; text-wrap: balance; }
.lede { max-width: 66ch; margin: 0 0 10px; font-size: 17px; text-wrap: pretty; }
.how { max-width: 76ch; margin: 0; color: var(--muted); font-size: 14px; text-wrap: pretty; }
h2 { font: 500 12px/1.2 var(--display); letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted);
  margin: 34px 0 10px; padding-bottom: 8px; border-bottom: 1px solid var(--rule); }
.icon-btn { display: inline-grid; place-items: center; width: 40px; height: 40px; padding: 0; border-radius: 10px;
  border: 1px solid var(--rule); background: var(--bg); color: var(--ink); cursor: pointer; flex: none; }
.icon-btn:hover { border-color: var(--muted); }
.icon-btn svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.icon-btn .i-sun { display: none; }
:root[data-theme="dark"] .icon-btn .i-sun { display: block; }
:root[data-theme="dark"] .icon-btn .i-moon { display: none; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .icon-btn .i-sun { display: block; }
  :root:not([data-theme="light"]) .icon-btn .i-moon { display: none; }
}
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 12px; }
table { border-collapse: collapse; width: 100%; min-width: 980px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid var(--rule); }
tr:last-child td { border-bottom: 0; }
thead th { font: 500 11px/1.3 var(--display); letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
  background: var(--surface); white-space: nowrap; }
tbody tr { cursor: pointer; }
tbody tr:hover td { background: var(--surface); }
tbody tr[aria-selected="true"] td { background: var(--surface); }
tbody tr[aria-selected="true"] td:first-child { box-shadow: inset 3px 0 0 var(--ink); }
.s-name { font: 500 15px/1.25 var(--display); margin: 0; }
.inuse { font: 500 10.5px/1 var(--display); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted);
  border: 1px solid var(--rule); border-radius: 999px; padding: 3px 7px; margin-left: 6px; vertical-align: 2px; white-space: nowrap; }
.s-note { margin: 2px 0 0; color: var(--muted); font-size: 13px; max-width: 30ch; }
.s-src { margin: 4px 0 0; color: var(--muted); font: 11px/1.4 var(--mono); max-width: 34ch; }
.dots { width: 86px; height: 18px; display: block; }
.de { font: 500 15px/1.2 var(--mono); margin-top: 4px; }
.de.low { color: var(--flag); }
.pair { font-size: 12px; color: var(--muted); }
.ratio { font: 500 15px/1.2 var(--mono); }
.ratio.low { color: var(--flag); }
.controls { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 2; background: var(--bg);
  border-bottom: 1px solid var(--rule); padding-block: 12px; display: flex; flex-wrap: wrap; gap: 10px 22px; align-items: center; }
.seg { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.seg .label { margin-right: 4px; }
.chip { font: 500 12.5px/1 var(--display); padding: 8px 12px; border-radius: 999px; border: 1px solid var(--rule);
  background: transparent; color: var(--ink); cursor: pointer; display: inline-flex; gap: 7px; align-items: center; }
.chip:hover { border-color: var(--muted); }
.chip[aria-pressed="true"] { background: var(--on); color: var(--on-ink); border-color: var(--on); }
.chip .sw { display: inline-flex; gap: 2px; }
.chip .sw i { width: 8px; height: 8px; border-radius: 50%; display: block; box-shadow: 0 0 0 1px rgba(0,0,0,0.12); }
.now { margin: 12px 0 0; color: var(--muted); font-size: 14px; }
.poster { margin-top: 14px; color: var(--ink); }
.poster svg { width: 100%; height: auto; display: block; }
.f-input { fill: var(--f-input); } .s-input { stroke: var(--s-input); }
.f-hidden { fill: var(--f-hidden); } .s-hidden { stroke: var(--s-hidden); }
.f-state { fill: var(--f-state); } .s-state { stroke: var(--s-state); }
.f-mixing { fill: var(--f-mixing); } .s-mixing { stroke: var(--s-mixing); }
.f-output { fill: var(--f-output); } .s-output { stroke: var(--s-output); }
.glyph { stroke: var(--glyph); }
footer { margin-top: 40px; border-top: 1px solid var(--rule); padding-top: 14px; color: var(--muted); font-size: 13px; }
footer a { color: inherit; text-underline-offset: 2px; }
"""

PAGE_JS = r"""
(() => {
  const D = JSON.parse(document.getElementById('pal-data').textContent);
  const poster = document.getElementById('poster');
  const rows = Array.from(document.querySelectorAll('tbody tr[data-scheme]'));
  const schemeBtns = Array.from(document.querySelectorAll('[data-pick-scheme]'));
  const visionBtns = Array.from(document.querySelectorAll('[data-pick-vision]'));
  const now = document.getElementById('now');
  const visionName = Object.fromEntries(D.visions.map(v => [v[0], v[1]]));
  let scheme = D.current, vision = 'normal';
  try {
    const s = localStorage.getItem('palettes-scheme'), v = localStorage.getItem('palettes-vision');
    if (D.schemes.some(x => x.id === s)) scheme = s;
    if (visionName[v]) vision = v;
  } catch (e) {}

  function paint() {
    const s = D.schemes.find(x => x.id === scheme);
    for (const [k, v] of Object.entries(s.vars[vision])) poster.style.setProperty('--' + k, v);
    schemeBtns.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.pickScheme === scheme)));
    visionBtns.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.pickVision === vision)));
    rows.forEach(r => r.setAttribute('aria-selected', String(r.dataset.scheme === scheme)));
    const c = s.closest[vision];
    now.textContent = s.name + ', ' + visionName[vision].toLowerCase() + ': the closest pair is ' +
      c.pair[0] + ' and ' + c.pair[1] + ', ' + c.de.toFixed(1) + ' apart.';
    try { localStorage.setItem('palettes-scheme', scheme); localStorage.setItem('palettes-vision', vision); } catch (e) {}
  }
  schemeBtns.forEach(b => b.addEventListener('click', () => { scheme = b.dataset.pickScheme; paint(); }));
  visionBtns.forEach(b => b.addEventListener('click', () => { vision = b.dataset.pickVision; paint(); }));
  rows.forEach(r => {
    r.addEventListener('click', () => { scheme = r.dataset.scheme; paint(); });
    r.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); scheme = r.dataset.scheme; paint(); }
    });
  });

  const root = document.documentElement;
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const themeBtn = document.getElementById('theme');
  const themeNow = () => {
    const t = root.getAttribute('data-theme');
    return t === 'light' || t === 'dark' ? t : (mq && mq.matches ? 'dark' : 'light');
  };
  const label = () => {
    const l = themeNow() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
    themeBtn.setAttribute('aria-label', l); themeBtn.title = l;
  };
  themeBtn.addEventListener('click', () => {
    const next = themeNow() === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('palettes-theme', next); } catch (e) {}
    label();
  });
  if (mq && mq.addEventListener) mq.addEventListener('change', label);
  label();
  paint();
})();
"""


def build():
    doc = guide.load(os.path.join(HERE, "architectures.yaml"))
    diagrams = guide.build_all(doc)
    data = [scheme_data(s) for s in SCHEMES]
    svg = tagged_poster(doc, diagrams)

    head_cells = "".join(f'<th scope="col">{html.escape(n)}</th>' for _, n in VISIONS)
    rows = []
    for s in data:
        cells = []
        for v, _ in VISIONS:
            c = s["closest"][v]
            vv = s["vars"][v]
            colors = [vv[f"f-{j}"] for j in JOBS]
            outl = [vv[f"s-{j}"] for j in JOBS]
            low = " low" if c["de"] < 10 else ""
            cells.append(
                f'<td>{dots(colors, outl)}<div class="de{low}">{c["de"]:.1f}</div>'
                f'<div class="pair">{c["pair"][0]} and {c["pair"][1]}</div></td>'
            )
        ratios = "".join(
            f'<td><span class="ratio{" low" if s[k] < 3 else ""}">{s[k]:.1f}</span></td>'
            for k in ("glyph", "on_light", "on_dark")
        )
        inuse = ' <span class="inuse">in the guide</span>' if s["id"] == CURRENT else ""
        rows.append(
            f'<tr data-scheme="{s["id"]}" tabindex="0" aria-selected="false">'
            f'<td><p class="s-name">{html.escape(s["name"])}{inuse}</p><p class="s-note">{html.escape(s["note"])}</p>'
            f'<p class="s-src">{html.escape(s["source"])}</p></td>{"".join(cells)}{ratios}</tr>'
        )

    def swatch(fills):
        return "".join(f'<i style="background:{fills[j]}"></i>' for j in JOBS)

    chips = "".join(
        f'<button class="chip" type="button" data-pick-scheme="{s["id"]}" aria-pressed="false">'
        f'<span class="sw" aria-hidden="true">{swatch(s["fills"])}</span>'
        f'{html.escape(s["name"])}</button>'
        for s in data
    )
    vchips = "".join(
        f'<button class="chip" type="button" data-pick-vision="{v}" aria-pressed="false">{n}</button>'
        for v, n in VISIONS
    )
    payload = json.dumps(
        dict(
            current=CURRENT,
            schemes=[{k: s[k] for k in ("id", "name", "vars", "closest")} for s in data],
            visions=VISIONS,
        )
    ).replace("</", "<\\/")

    body = f"""<div class="page">
<div class="topbar"><span class="eyebrow">Neural Network Field Guide · proofs</span>
<button class="icon-btn" type="button" id="theme" aria-label="Switch to dark mode" title="Switch to dark mode">{guide.THEME_ICONS}</button></div>
<h1>Field Guide Palettes</h1>
<p class="lede">Six color schemes for the guide's five jobs, each on the full poster below, and measured as readers with each common kind of color blindness, and a grayscale print, would see them.</p>
<p class="how">Each number is the smallest color difference (CIE76 ΔE) between any two of the five jobs, with the closest pair named. Around 2 is the smallest difference most people can see side by side; cells this small need far more to be told apart at a glance, so numbers under 10 are marked. Color blindness is simulated after Machado, Oliveira and Fernandes (2009) at full strength; grayscale is relative luminance. The last three columns are contrast ratios, where 3 is the usual floor for graphics: the inner glyphs against their cells, the outlines against a white ground, and the cells against the dark ground. Whatever the scheme, each cell's mark keeps the jobs apart where colors collide.</p>
<h2>The schemes, measured</h2>
<div class="table-wrap"><table>
<thead><tr><th scope="col">Scheme</th>{head_cells}<th scope="col">Glyphs</th><th scope="col">On white</th><th scope="col">On dark</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h2>The poster</h2>
<div class="controls" role="group" aria-label="Proof settings">
<div class="seg" role="group" aria-label="Scheme"><span class="eyebrow label">Scheme</span>{chips}</div>
<div class="seg" role="group" aria-label="Vision"><span class="eyebrow label">Seen with</span>{vchips}</div>
</div>
<p class="now" id="now" aria-live="polite"></p>
<div class="poster" id="poster">{svg}</div>
<footer>{html.escape(doc["copyright"])}. Proofs drawn by <code>palettes.py</code> from the same specs as the guide. Sources: Okabe and Ito, <a href="https://jfly.uni-koeln.de/color/" target="_blank" rel="noopener">Color Universal Design</a>; Paul Tol, <a href="https://personal.sron.nl/~pault/" target="_blank" rel="noopener">Colour Schemes</a>; Machado, Oliveira and Fernandes, <a href="https://doi.org/10.1109/TVCG.2009.113" target="_blank" rel="noopener">A Physiologically-based Model for Simulation of Color Vision Deficiency</a>.</footer>
</div>
<script type="application/json" id="pal-data">{payload}</script>
<script>{PAGE_JS}</script>"""
    head = (
        f"<title>Field Guide Palettes</title>\n"
        f'<meta name="claude:viewport-fit-sides" content="cover">\n'
        f'<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'<link rel="stylesheet" href="{guide.FONT_LINK}">\n'
        f"<style>{PAGE_CSS}</style>\n"
        "<script>try{var t=localStorage.getItem('palettes-theme');"
        "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>\n"
    )
    current = next(s for s in data if s["id"] == CURRENT)
    defaults = (
        "#poster { "
        + " ".join(f"--{k}: {v};" for k, v in current["vars"]["normal"].items())
        + " }"
    )
    head = head.replace(
        "</style>", defaults + "\n</style>", 1
    )  # the guide's colors before any script runs
    out = os.path.join(HERE, "proofs", "palettes.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(head + body)
    for s in data:
        print(
            f'{s["name"]:<16}'
            + "  ".join(f'{v} {s["closest"][v]["de"]:5.1f}' for v, _ in VISIONS)
            + f'  | glyph {s["glyph"]} on white {s["on_light"]} on dark {s["on_dark"]}'
        )
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    build()
