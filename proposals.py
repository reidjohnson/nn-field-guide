#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Reid Johnson
# SPDX-License-Identifier: MIT
"""
proposals.py: a proof of changes the field guide might make, drawn before they are made.

Reads proofs/proposals.yaml and draws, beside what each would replace:

  1. the poster redrawn at other sizes: type, line weight and cell radius, alone and together,
     with each preset's lint and the size of its smallest marks in print;
  2. a fix for the two panels that are drawn alike;
  3. candidate panels, drafted from their specs, beside the panels nearest to them;
  4. layouts that make room for them.

  proofs/proposals.html    the proof, as a fragment ready to publish

Nothing here changes the chart; adopting a proposal means copying its values into guide.py or
architectures.yaml. proofs/ is a local working folder, not part of the repository; when
proofs/proposals.yaml doesn't exist yet, the first run writes a starter to fill in.
Usage:  python proposals.py
"""

from __future__ import annotations

import copy
import html
import json
import math
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guide  # noqa: E402
import revision  # noqa: E402  (shares the proof pages' style, script and theme button)

esc = html.escape
f = guide.fmt

# --------------------------------------------------------------------------
# New cell types a proposal may need, drawn in the chart's own idiom
# --------------------------------------------------------------------------


def install_cells(cells: dict):
    for k, v in (cells or {}).items():
        guide.CELLS.setdefault(k, dict(v))


# --------------------------------------------------------------------------
# Posters at other sizes
# --------------------------------------------------------------------------


class preset:
    """Set guide.py's size knobs for one proposal (type, LINE, R) and put them back after."""

    def __init__(self, o: dict):
        self.o = o

    def __enter__(self):
        self.saved = (dict(guide.TYPE), guide.LINE, guide.R)
        guide.TYPE.update(
            {k: (v[0] + self.o["type"], v[1]) for k, v in guide.TYPE.items() if k != "title"}
        )
        guide.LINE, guide.R = self.o["line"], self.o["r"]
        return self

    def __exit__(self, *exc):
        guide.TYPE.clear()
        guide.TYPE.update(self.saved[0])
        guide.LINE, guide.R = self.saved[1], self.saved[2]


def poster_symbol(svg: str, sid: str) -> tuple:
    """Turn a whole poster into a <g> that crops can <use>, with its own arrowhead id."""
    w, h = (float(v) for v in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups())
    inner = svg[svg.index(">", svg.index("<svg")) + 1 : svg.rindex("</svg>")]
    inner = re.sub(r"<title>.*?</title>", "", inner, count=1)
    inner = inner.replace('id="arr"', f'id="arr-{sid}"').replace("url(#arr)", f"url(#arr-{sid})")
    return f'<g id="{sid}">{inner}</g>', w, h


def text_y(svg: str, text: str) -> float:
    m = re.search(r'<text x="[^"]+" y="([\d.]+)"[^>]*>' + re.escape(esc(text)) + "</text>", svg)
    return float(m.group(1))


# --------------------------------------------------------------------------
# Panels as symbols, so a drawing used many times is written once
# --------------------------------------------------------------------------


class Panels:
    def __init__(self):
        self.defs, self.done = [], set()

    def add(self, key: str, spec: dict) -> str:
        sid = f"p-{key}"
        if sid not in self.done:
            d = guide.build_diagram(key, spec)
            self.lint = getattr(self, "lint", []) + guide.lint_diagram(d)
            mid = f"arr-{sid}"
            self.defs.append(
                f'<symbol id="{sid}" viewBox="0 0 {f(guide.W)} {f(guide.H)}">'
                f'<defs>{guide.marker_def(mid, "currentColor")}</defs>{guide.render_diagram(d, mid)}</symbol>'
            )
            self.done.add(sid)
        return sid

    def use(self, key: str, spec: dict, label: str) -> str:
        sid = self.add(key, spec)
        return (
            f'<svg class="dia" viewBox="0 0 {f(guide.W)} {f(guide.H)}" role="img" aria-label="{esc(label)}">'
            f'<use href="#{sid}"/></svg>'
        )

    def sprite(self) -> str:
        return (
            '<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">'
            + "".join(self.defs)
            + "</svg>"
        )


def card(panels: Panels, key: str, a: dict, side: str = "", spec=None) -> str:
    top = f'<figcaption class="side">{esc(side)}</figcaption>' if side else ""
    return (
        f'<figure class="panel">{top}<p class="name">{esc(a["name"])}</p>'
        f'{panels.use(key, spec or a["diagram"], a["name"])}'
        f'<p class="cap">{esc(a["caption"])}</p><p class="meta">{a["year"]} · {esc(a["objective"])}</p></figure>'
    )


def chips(target: str, options: list, first: str) -> str:
    out = "".join(
        f'<button class="chip" type="button" data-side="{k}" aria-pressed="{str(k == first).lower()}">{esc(l)}</button>'
        for k, l in options
    )
    return f'<div class="switch" role="group" aria-label="Show" data-switch="{target}"><span class="eyebrow">Show</span>{out}</div>'


def para(text) -> str:
    return " ".join(str(text or "").split())


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

EXTRA_CSS = r"""
.vgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }

.note { max-width: 76ch; margin: 10px 0 0; text-wrap: pretty; }
.muted { color: var(--muted); }
table { border-collapse: collapse; width: 100%; font-size: 14px; margin-top: 14px; }
th, td { text-align: left; padding: 7px 10px 7px 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { font: 500 11px/1.3 var(--display); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
td.num { font: 13px/1.4 var(--mono); white-space: nowrap; }
.low { text-decoration: underline wavy; text-decoration-thickness: 1px; text-underline-offset: 3px; }
.tablewrap { overflow-x: auto; }
.opts { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px 24px; margin-top: 14px; }
.opts h3, .cand h3, .layout h3 { font: 500 16px/1.3 var(--display); margin: 0 0 4px; }
.opts p { margin: 0; font-size: 14px; text-wrap: pretty; }
.crop { margin-top: 14px; border: 1px solid var(--rule); border-radius: 12px; overflow: hidden; }
.crop svg { width: 100%; height: auto; display: block; }
.crop-label { font: 500 11px/1.2 var(--display); letter-spacing: 0.13em; text-transform: uppercase; color: var(--muted); margin: 18px 0 0; }
.dia { width: 100%; height: auto; display: block; }
.pairs { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 18px 28px; margin-top: 14px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; border-top: 1px solid var(--rule); padding-top: 12px; }
.pair .panel .name { font-size: 13.5px; }
.pair p.v { grid-column: 1 / -1; margin: 0; font-size: 14px; }
.verdict { font: 500 11px/1.2 var(--display); letter-spacing: 0.13em; text-transform: uppercase; margin-right: 6px; }
.lgs { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-top: 8px; font-size: 14px; }
.lg { display: inline-flex; align-items: center; gap: 6px; }
.gl-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px 20px; }
.gl-small { display: flex; flex-wrap: wrap; gap: 4px 16px; }
.page-stage { margin-top: 12px; }
.page-wrap { position: relative; overflow: hidden; border: 1px solid var(--rule); border-radius: 12px; }
.page-wrap iframe { border: 0; transform-origin: 0 0; display: block; background: #fff; }
.fold { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; background: #CE481E; }
.mks { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 18px 24px; margin-top: 14px; }
.mk { border-top: 1px solid var(--rule); padding-top: 12px; min-width: 0; }
.mk .note { font-size: 14px; }
.legend-row { margin: 8px 0 0; font-size: 14px; }
.cand { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.25fr); gap: 18px 24px;
  padding: 18px 0; border-bottom: 1px solid var(--rule); align-items: start; }
.cand .why p { margin: 0 0 8px; max-width: 58ch; text-wrap: pretty; }
.why p.what { color: var(--muted); font: 13.5px/1.5 var(--body); }
.layout .lrow { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px 10px; margin-top: 12px; }
.layout .fam { grid-row: 1; font: 500 10.5px/1.25 var(--display); letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--muted); border-bottom: 1px solid var(--rule); padding-bottom: 4px; align-self: end; }
.slot { grid-row: 2; min-width: 0; text-align: center; border-radius: 8px; padding: 4px 2px 6px; }
.slot .nm { font-size: 11.5px; line-height: 1.25; display: block; overflow-wrap: anywhere; }
.slot.new { outline: 2px solid var(--ink); outline-offset: -2px; }
.slot.new .nm::before { content: "New · "; font-weight: 700; }
.slot.empty { border: 1px dashed var(--rule); min-height: 60px; }
.slot.moved .nm::before { content: "Moved · "; font-weight: 700; }
details summary { cursor: pointer; }
ul.lint { margin: 6px 0 0; padding-left: 18px; font: 12px/1.5 var(--mono); color: var(--muted); }
.folded { margin-top: 12px; font-size: 14px; color: var(--muted); }
@media (max-width: 760px) {
  .cand { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .cand .why { grid-column: 1 / -1; }
  .slot .nm { font-size: 10px; }
}
"""


PRINT_SIZES = [
    ("A2, 16.5 × 23.4 in", 16.54, 23.39),
    ("18 × 24 in", 18, 24),
    ("24 × 36 in", 24, 36),
]


def sizes_section(doc: dict, sz: dict) -> tuple:
    opts = sz["options"]
    first = opts[0]["key"]
    syms, views, rows = [], [], []
    for o in opts:
        with preset(o):
            diagrams = guide.build_all(doc)
            warns = guide.lint_doc(doc, diagrams)
            svg = guide.poster_svg(doc, diagrams)
            g, w, h = poster_symbol(svg, f"poster-{o['key']}")
            syms.append(g)
            bh = guide.block_h()
            crops = []
            for c in sz["crops"]:
                if c.get("from") == "legend":
                    y0 = (
                        float(re.search(r'<g transform="translate\(0,([\d.]+)\)">', svg).group(1))
                        - 8
                    )
                    y1 = text_y(svg, doc["sections"][0]["title"].upper()) - 18
                else:
                    y0 = text_y(svg, c["section"].upper()) - 16
                    y1 = y0 + 16 + 34 + bh + 8
                crops.append((c["label"], y0, y1))
            meta, line = guide.TYPE["meta"][0], guide.LINE
        hidden = "" if o["key"] == first else " hidden"
        parts = [f'<p class="note">{esc(para(o["note"]))}</p>']
        for label, y0, y1 in crops:
            parts.append(
                f'<p class="crop-label">{esc(label)}</p><div class="crop">'
                f'<svg viewBox="0 {f(y0)} {f(w)} {f(y1 - y0)}" role="img" aria-label="{esc(label)}, {esc(o["label"])}">'
                f'<use href="#poster-{o["key"]}"/></svg></div>'
            )
        parts.append(
            f'<p class="crop-label">The whole poster</p><div class="crop">'
            f'<svg viewBox="0 0 {f(w)} {f(h)}" role="img" aria-label="The poster, {esc(o["label"])}">'
            f'<use href="#poster-{o["key"]}"/></svg></div>'
        )
        views.append(
            f'<div data-view="sizes" data-side="{o["key"]}"{hidden}>{"".join(parts)}</div>'
        )
        cells = []
        for _, wi, hi in PRINT_SIZES:
            k = min(wi * 72 / w, hi * 72 / h)
            cells.append(f'<td class="num">{meta * k:.1f} pt · {line * k:.2f} pt</td>')
        lint = (
            f'<details><summary>{len(warns)} warning{"s" if len(warns) != 1 else ""}</summary>'
            f'<ul class="lint">{"".join(f"<li>{esc(x)}</li>" for x in warns)}</ul></details>'
            if warns
            else "clean"
        )
        rows.append(
            f'<tr><td>{esc(o["label"])}</td><td class="num">{o["type"]:+d} px</td><td class="num">{o["line"]}</td>'
            f'<td class="num">{o["r"]}</td><td>{lint}</td><td class="num">{f(w)} × {f(h)}</td>{"".join(cells)}</tr>'
        )
    head = "".join(f"<th>{esc(l)}</th>" for l, _, _ in PRINT_SIZES)
    table = (
        '<div class="tablewrap"><table><thead><tr><th>Preset</th><th>Type</th><th>Lines</th><th>Cells</th><th>Lint</th>'
        f'<th>Poster</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="note muted">The print columns give the year · objective line and the line weight as printed '
        "(aim for 6 pt and 0.25 pt at least). Lint is the chart's own: overlapping cells, lines that pass "
        "too close to a cell, marks that touch, and text too wide for its column, measured with the real fonts.</p>"
    )
    body = (
        '<h2>Type, lines and cells</h2><p class="note">Each preset redraws the whole poster with guide.py\'s knobs '
        "set: type adds px to every text role but the title, lines set the stroke of every link and arrow, and cells set "
        "the radius (squares, diamonds and inner marks scale with it). The grid stays as it is.</p>"
        + table
        + chips("sizes", [(o["key"], o["label"]) for o in opts], first)
        + "".join(views)
    )
    sprite = (
        '<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">'
        + "".join(syms)
        + "</svg>"
    )
    return body, sprite


def marks_section(doc: dict, mk: dict, panels: Panels) -> str:
    """One panel redrawn with each option's cell type in place of the current one, beside its
    legend entry and a copy at poster size."""
    a = doc["architectures"][mk["panel"]]
    out = []
    for o in mk["options"]:
        if o.get("def"):
            guide.CELLS[o["cell"]] = dict(o["def"])
        spec = copy.deepcopy(a["diagram"])
        for r in spec["rows"]:
            if r["cell"] == mk["cell"]:
                r["cell"] = o["cell"]
        d = guide.build_diagram(f'{mk["panel"]}-{o["key"]}', spec)
        warns = guide.lint_diagram(d)
        sid = panels.add(f'{mk["panel"]}-{o["key"]}', spec)
        legend = (
            f'<svg viewBox="-14 -10 28 20" width="34" height="24" style="vertical-align:middle" aria-hidden="true">'
            f'{guide.cell_svg(o["cell"], 0, 0, ink="currentColor", marks=False)}</svg> {esc(guide.CELLS[o["cell"]]["label"])}'
        )
        small = (
            f'<svg viewBox="0 0 {f(guide.W)} {f(guide.H)}" width="{f(guide.W)}" height="{f(guide.H)}" style="max-width:100%;height:auto" '
            f'role="img" aria-label="{esc(a["name"])} at poster size"><use href="#{sid}"/></svg>'
        )
        lint = (
            f'{len(warns)} lint warning{"s" if len(warns) != 1 else ""}' if warns else "Lint clean"
        )
        out.append(
            f'<div class="mk"><figure class="panel"><figcaption class="side">{esc(o["label"])}</figcaption>'
            f'<p class="name">{esc(a["name"])}</p><svg class="dia" viewBox="0 0 {f(guide.W)} {f(guide.H)}" role="img" '
            f'aria-label="{esc(a["name"])}, {esc(o["label"])}"><use href="#{sid}"/></svg></figure>'
            f'<p class="legend-row">{legend}</p><p class="crop-label">At poster size</p>{small}'
            f'<p class="note">{esc(para(o["note"]))}</p><p class="note muted">{lint}</p></div>'
        )
    return (
        f'<h2>{esc(mk.get("title", "Marks"))}</h2><p class="note">{esc(para(mk["intro"]))}</p>'
        f'<div class="mks">{"".join(out)}</div>'
    )


def contrast(a: str, b: str) -> float:
    """WCAG contrast between two hex colors."""

    def lum(h):
        c = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class knobs:
    """Set any of guide.py's module values for one option (e.g. GLYPH_STROKE) and put them back."""

    def __init__(self, values: dict):
        self.values = values or {}

    def __enter__(self):
        self.saved = {k: getattr(guide, k) for k in self.values}
        for k, v in self.values.items():
            setattr(guide, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(guide, k, v)


def glyphs_section(doc: dict, gl: dict, panels: Panels) -> str:
    """The panels and legend entries whose cells carry inner marks, redrawn per option, at
    poster size and enlarged, with the marks' contrast on each fill."""
    arch = doc["architectures"]
    opts, first = gl["options"], gl["options"][1]["key"]
    views = []
    for o in opts:
        with knobs(o.get("set")):
            ink_on = guide.GLYPH_INK_ON
            rows = []
            for color, (fill, _) in guide.PALETTE.items():
                ink = ink_on.get(color, guide.GLYPH_INK)
                rows.append(
                    f'<tr><td>{esc(color)}</td><td class="num">{esc(ink)}</td>'
                    f'<td class="num">{contrast(ink, fill):.1f}:1</td></tr>'
                )
            table = (
                '<div class="tablewrap"><table><thead><tr><th>Fill</th><th>Mark ink</th><th>Contrast</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>'
            )
            cards, small = [], []
            for pid in gl["panels"]:
                key = f'{pid}-{o["key"]}'
                sid = panels.add(key, arch[pid]["diagram"])
                cards.append(
                    f'<figure class="panel"><p class="name">{esc(arch[pid]["name"])}</p>'
                    f'<svg class="dia" viewBox="0 0 {f(guide.W)} {f(guide.H)}" role="img" aria-label="{esc(arch[pid]["name"])}">'
                    f'<use href="#{sid}"/></svg></figure>'
                )
                small.append(
                    f'<svg viewBox="0 0 {f(guide.W)} {f(guide.H)}" width="{f(guide.W)}" height="{f(guide.H)}" '
                    f'style="max-width:100%;height:auto" role="img" aria-label="{esc(arch[pid]["name"])} at poster size">'
                    f'<use href="#{sid}"/></svg>'
                )
            kinds = [k for k, c in guide.CELLS.items() if c.get("glyph")]
            legend = "".join(
                f'<span class="lg"><svg viewBox="-12 -12 24 24" width="36" height="36" aria-hidden="true">'
                f'{guide.cell_svg(k, 0, 0, ink="currentColor", marks=False)}</svg>{esc(guide.CELLS[k]["label"])}</span>'
                for k in kinds
            )
        hidden = "" if o["key"] == first else " hidden"
        views.append(
            f'<div data-view="glyphs" data-side="{o["key"]}"{hidden}><p class="note">{esc(para(o["note"]))}</p>'
            f'{table}<p class="crop-label">Every cell with an inner mark, as the legend draws it</p>'
            f'<div class="lgs">{legend}</div><p class="crop-label">Enlarged</p><div class="gl-grid">{"".join(cards)}</div>'
            f'<p class="crop-label">At poster size</p><div class="gl-small">{"".join(small)}</div></div>'
        )
    return (
        f'<h2>{esc(gl.get("title", "Inner marks"))}</h2><p class="note">{esc(para(gl["intro"]))}</p>'
        + chips("glyphs", [(o["key"], o["label"]) for o in opts], first)
        + "".join(views)
    )


PAGE_PROOF_JS = r"""
(() => {
  // One copy of the page; each option adds its CSS and script and reloads the frame, which
  // is laid out at a real screen size and scaled to fit, with the fold drawn on it.
  const BASE = JSON.parse(document.getElementById('page-base').textContent);
  const OPTS = JSON.parse(document.getElementById('page-opts').textContent);
  const frame = document.getElementById('page-frame');
  const wrap = document.getElementById('page-wrap');
  const out = document.getElementById('page-measure');
  const note = document.getElementById('page-note');
  let key = document.querySelector('[data-page-opt][aria-pressed="true"]').dataset.pageOpt;
  let size = [1440, 900];
  function fit() {
    const k = Math.min(1, wrap.parentElement.clientWidth / size[0]);
    frame.style.width = size[0] + 'px'; frame.style.height = size[1] + 'px';
    frame.style.transform = 'scale(' + k + ')';
    wrap.style.width = size[0] * k + 'px'; wrap.style.height = size[1] * k + 'px';
  }
  function show() {
    const o = OPTS[key];
    note.textContent = o.note;
    frame.srcdoc = BASE.replace('</head>', '<style>' + o.css + '</style></head>')
                       .replace('</body>', '<script>' + o.js + '<' + '/script></body>');
    fit();
  }
  frame.addEventListener('load', () => {
    setTimeout(() => {
      const d = frame.contentDocument;
      const card = d && d.querySelector('.card');
      if (!card) { out.textContent = ''; return; }
      const top = Math.round(card.getBoundingClientRect().top);
      const seen = top < size[1];
      const caps = Array.from(d.querySelectorAll('.card .cap'));
      const lh = caps.length ? parseFloat(d.defaultView.getComputedStyle(caps[0]).lineHeight) : 0;
      const wrapped = caps.filter(c => c.getBoundingClientRect().height > lh * 1.5).map(c => c.textContent);
      const cut = caps.filter(c => c.scrollWidth > c.clientWidth + 0.5).length;
      const fs = caps.length ? d.defaultView.getComputedStyle(caps[0]).fontSize : '';
      out.textContent = 'First diagram starts at ' + top + ' px; the screen is ' + size[1] + ' px tall, so it is '
        + (seen ? 'in view, with ' + (size[1] - top) + ' px of it showing.' : 'below the fold by ' + (top - size[1]) + ' px.')
        + ' Captions at ' + fs + ': ' + (wrapped.length ? wrapped.length + ' wrap to two lines (' + wrapped.join('; ') + ')' : 'every one on one line')
        + (cut ? ', ' + cut + ' cut short' : '') + '.';
    }, 400);
  });
  document.querySelectorAll('[data-page-opt]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('[data-page-opt]').forEach(o => o.setAttribute('aria-pressed', String(o === b)));
    key = b.dataset.pageOpt; show();
  }));
  document.querySelectorAll('[data-page-size]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('[data-page-size]').forEach(o => o.setAttribute('aria-pressed', String(o === b)));
    size = b.dataset.pageSize.split('x').map(Number); show();
  }));
  window.addEventListener('resize', fit);
  show();
})();
"""


def page_section(doc: dict, pg: dict) -> str:
    """The web page's top, redrawn per option in a frame at a real screen size."""
    base = guide.web_page(doc, guide.build_all(doc), mode="standalone")
    opts = {
        o["key"]: dict(css=o.get("css", ""), js=o.get("js", ""), note=para(o["note"]))
        for o in pg["options"]
    }
    first = next(
        (o["key"] for o in pg["options"] if "recommended" in o["label"]), pg["options"][0]["key"]
    )
    enc = lambda v: json.dumps(v, ensure_ascii=False).replace("</", "<\\/")
    opt_chips = "".join(
        f'<button class="chip" type="button" data-page-opt="{o["key"]}" aria-pressed="{str(o["key"] == first).lower()}">'
        f'{esc(o["label"])}</button>'
        for o in pg["options"]
    )
    size_chips = "".join(
        f'<button class="chip" type="button" data-page-size="{s}" aria-pressed="{str(i == 0).lower()}">'
        f'{esc(s.replace("x", " × "))}</button>'
        for i, s in enumerate(pg.get("sizes", ["1440x900"]))
    )
    return (
        f'<h2>{esc(pg.get("title", "The page"))}</h2><p class="note">{esc(para(pg["intro"]))}</p>'
        f'<div class="switch" role="group" aria-label="Option"><span class="eyebrow">Show</span>{opt_chips}</div>'
        f'<div class="switch" role="group" aria-label="Screen"><span class="eyebrow">Screen</span>{size_chips}</div>'
        f'<p class="note" id="page-note"></p><p class="note"><strong id="page-measure"></strong></p>'
        f'<div class="page-stage"><div id="page-wrap" class="page-wrap"><iframe id="page-frame" title="The page, with this option" '
        f'loading="eager"></iframe><div class="fold" aria-hidden="true"></div></div></div>'
        f'<script type="application/json" id="page-base">{enc(base)}</script>'
        f'<script type="application/json" id="page-opts">{enc(opts)}</script>'
        f"<script>{PAGE_PROOF_JS}</script>"
    )


def tie_svg(d, at, width: float = 7.0, gap: float = 2.6, ink: str = "currentColor") -> str:
    """A tie mark: an equals sign between two parts that are one network (shared weights).
    `at` is [x, y] in panel units, before PAD_Y."""
    x, y = at[0], at[1] + guide.PAD_Y
    w = guide.LINE * 1.4
    return (
        f'<g stroke="{ink}" stroke-width="{f(w)}" stroke-linecap="round">'
        f'<line x1="{f(x - width / 2)}" y1="{f(y - gap / 2)}" x2="{f(x + width / 2)}" y2="{f(y - gap / 2)}"/>'
        f'<line x1="{f(x - width / 2)}" y1="{f(y + gap / 2)}" x2="{f(x + width / 2)}" y2="{f(y + gap / 2)}"/></g>'
    )


def kernel_extra(d, style: str, ink: str = "currentColor") -> str:
    """Restyle a convolution's links so each filter weight has one look across the row: a
    unit's link from its left neighbour stays solid, its link from the right takes `style`."""
    extra = []
    for sg in d.segs:
        if sg.style != "link" or len(sg.ends) != 2:
            continue
        a, b = (d.cells[k] for k in sg.ends)
        if guide.CELLS[b.kind]["group"] != "mixing" or guide.CELLS[a.kind]["group"] not in (
            "input",
            "mixing",
        ):
            continue
        if a.x > b.x:  # the right-hand weight of the filter
            if style == "dash":
                sg.style = "kernel"
            else:  # a short tick across the link's middle
                (x0, y0), (x1, y1) = sg.pts[0], sg.pts[-1]
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                L = math.hypot(x1 - x0, y1 - y0)
                nx, ny = -(y1 - y0) / L * 2.6, (x1 - x0) / L * 2.6
                extra.append(
                    f'<line x1="{f(mx - nx)}" y1="{f(my - ny)}" x2="{f(mx + nx)}" y2="{f(my + ny)}"/>'
                )
    if not extra:
        return ""
    return f'<g stroke="{ink}" stroke-width="{f(guide.LINE)}" stroke-linecap="round">{"".join(extra)}</g>'


def sharing_section(doc: dict, sh: dict) -> tuple:
    """Panels redrawn with a proposed mark for shared weights, beside the current drawing."""
    arch = doc["architectures"]
    guide.LINKS.setdefault("kernel", dict(dash="6 2.5", label="Same filter weight"))
    if "kernel" not in guide.STYLE_ORDER:
        guide.STYLE_ORDER.append("kernel")
    syms, rows = [], []
    for o in sh["options"]:
        a = arch[o["panel"]]
        figs = []
        for side, key in (("Now", "now"), (o["label"], o["key"])):
            d = guide.build_diagram(o["panel"], a["diagram"])
            extra = ""
            if key != "now":
                if o["kind"] == "tie":
                    extra = tie_svg(d, o["at"])
                else:
                    extra = kernel_extra(d, o["style"])
            sid = f'sh-{o["key"]}-{key}'
            mid = f"arr-{sid}"
            syms.append(
                f'<symbol id="{sid}" viewBox="0 0 {f(guide.W)} {f(guide.H)}"><defs>{guide.marker_def(mid, "currentColor")}</defs>'
                f"{guide.render_diagram(d, mid)}{extra}</symbol>"
            )
            use = f'<use href="#{sid}"/>'
            figs.append(
                f'<figure class="panel"><figcaption class="side">{esc(side)}</figcaption>'
                f'<p class="name">{esc(a["name"])}</p><svg class="dia" viewBox="0 0 {f(guide.W)} {f(guide.H)}" role="img" '
                f'aria-label="{esc(a["name"])}, {esc(side)}">{use}</svg>'
                f'<svg viewBox="0 0 {f(guide.W)} {f(guide.H)}" width="{f(guide.W)}" height="{f(guide.H)}" '
                f'style="width:{f(guide.W)}px;max-width:100%;height:auto;display:block;margin-top:10px" aria-hidden="true">{use}</svg></figure>'
            )
        legend = ""
        if o.get("legend"):
            sample = (
                tie_svg(None, [11, -guide.PAD_Y])
                if o["kind"] == "tie"
                else (
                    guide.link_sample("kernel", 0, 0, "currentColor", "x")
                    if o["style"] == "dash"
                    else '<line x1="0" y1="0" x2="22" y2="0" stroke="currentColor" stroke-width="0.8"/>'
                    '<line x1="11" y1="-2.6" x2="11" y2="2.6" stroke="currentColor" stroke-width="0.8"/>'
                )
            )
            legend = (
                f'<p class="legend-row"><svg viewBox="-2 -8 26 16" width="40" height="24" aria-hidden="true" '
                f'style="vertical-align:middle">{sample}</svg> {esc(o["legend"])}</p>'
            )
        rows.append(
            f'<div class="change">{"".join(figs)}<div class="why"><h3>{esc(o["label"])}</h3>'
            f'<p>{esc(para(o["note"]))}</p>{legend}</div></div>'
        )
    sprite = (
        '<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">'
        + "".join(syms)
        + "</svg>"
    )
    return (
        f'<h2>{esc(sh.get("title", "Shared weights"))}</h2><p class="note">{esc(para(sh["intro"]))}</p>'
        f'<p class="note muted">Each drawing is shown enlarged, then at the size the poster prints it.</p>'
        + "".join(rows),
        sprite,
    )


def plate_svg(d, pl: dict, ink: str = "currentColor") -> str:
    """Plate notation: a rounded box around one copy of a repeated part, with how many times
    it repeats (all with the same weights) in its corner."""
    keys = [f"{g}.{i}" for g, i in pl.get("cells", [])] + [
        k for g in pl.get("groups", []) for k in d.groups[g]
    ]
    cells = [d.cells[k] for k in keys]
    pad = pl.get("pad", 5.0)
    x0 = min(c.x - guide.extent(c, -1, 0) for c in cells) - pad
    x1 = max(c.x + guide.extent(c, 1, 0) for c in cells) + pad
    y0 = min(c.y - guide.extent(c, 0, -1) for c in cells) - pad
    y1 = max(c.y + guide.extent(c, 0, 1) for c in cells) + pad
    lab = pl.get("label", "")
    return (
        f'<rect x="{f(x0)}" y="{f(y0)}" width="{f(x1 - x0)}" height="{f(y1 - y0)}" rx="5" fill="none" '
        f'stroke="{ink}" stroke-opacity="0.6" stroke-width="{f(guide.LINE)}"/>'
        f'<text x="{f(x1 + 2)}" y="{f(y1)}" font-family="Noto Sans Mono, monospace" '
        f'font-size="9" fill="{ink}" fill-opacity="0.8">{esc(lab)}</text>'
    )  # outside the box, by its foot


def sharing_all_section(doc: dict, sa: dict) -> tuple:
    """A way to show shared weights drawn on every kind of panel that shares them, one view
    per option, with a switch between options."""
    arch = doc["architectures"]
    opts, first = sa["options"], sa["options"][1]["key"]
    syms, views = [], []
    for o in opts:
        figs = []
        for item in o["panels"]:
            pid = item["panel"]
            d = guide.build_diagram(pid, arch[pid]["diagram"])
            extra = ""
            if item.get("plate"):
                extra = plate_svg(d, item["plate"])
            for x, y in item.get("ties", []):
                extra += tie_svg(d, [x, y])
            sid = f'sa-{o["key"]}-{pid}'
            mid = f"arr-{sid}"
            syms.append(
                f'<symbol id="{sid}" viewBox="0 0 {f(guide.W)} {f(guide.H)}"><defs>{guide.marker_def(mid, "currentColor")}</defs>'
                f"{guide.render_diagram(d, mid)}{extra}</symbol>"
            )
            note = f'<p class="cap">{esc(item["note"])}</p>' if item.get("note") else ""
            figs.append(
                f'<figure class="panel"><p class="name">{esc(arch[pid]["name"])}</p>'
                f'<svg class="dia" viewBox="0 0 {f(guide.W)} {f(guide.H)}" role="img" aria-label="{esc(arch[pid]["name"])}">'
                f'<use href="#{sid}"/></svg>'
                f'<svg viewBox="0 0 {f(guide.W)} {f(guide.H)}" style="width:{f(guide.W)}px;max-width:100%;height:auto;display:block;margin-top:6px" '
                f'aria-hidden="true"><use href="#{sid}"/></svg>{note}</figure>'
            )
        hidden = "" if o["key"] == first else " hidden"
        views.append(
            f'<div data-view="sharing-all" data-side="{o["key"]}"{hidden}><p class="note">{esc(para(o["note"]))}</p>'
            f'<div class="gl-grid">{"".join(figs)}</div></div>'
        )
    sprite = (
        '<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">'
        + "".join(syms)
        + "</svg>"
    )
    return (
        f'<h2>{esc(sa.get("title", "Shared weights"))}</h2><p class="note">{esc(para(sa["intro"]))}</p>'
        f'<p class="note muted">Each panel is shown enlarged, then at the size the poster prints it.</p>'
        + chips("sharing-all", [(o["key"], o["label"]) for o in opts], first)
        + "".join(views),
        sprite,
    )


# --------------------------------------------------------------------------
# Drawing variants: other ways to draw one kind of link, on every panel that has it
# --------------------------------------------------------------------------

_guide_gates = guide.gates


def gates_variant(d, src, dst, mode=True):
    """guide.gates, plus the ways a gate that also reads the previous state could be drawn:
    "hook" (now), "output" (a straight line from the previous step's output, h at t-1),
    "chevron" (up from the previous state at 45 degrees and down onto the dot at 45) and
    "single" (the input's line alone, the reading note saying the rest)."""
    if mode in (True, "recurrent", "hook", "single"):
        return _guide_gates(d, src, dst, "recurrent" if mode in ("recurrent", "hook") else True)
    _guide_gates(d, src, dst, True)
    ordered = sorted(dst, key=lambda k: d.cells[k].x)
    for idx in range(1, len(ordered)):
        A, B = d.cells[ordered[idx - 1]], d.cells[ordered[idx]]
        mx, my = (A.x + B.x) / 2, (A.y + B.y) / 2
        if mode == "output":
            above = [c for c in d.cells.values() if abs(c.x - A.x) < 0.5 and c.y < A.y - 1]
            O = max(above, key=lambda c: c.y)
            dx, dy = mx - O.x, my - O.y
            L = math.hypot(dx, dy)
            ux, uy = dx / L, dy / L
            s0 = guide.extent(O, ux, uy) + guide.GAP
            p0 = (O.x + ux * s0, O.y + uy * s0)
            p1 = (mx - ux * (guide.DOT + guide.GAP), my - uy * (guide.DOT + guide.GAP))
            d.segs.append(guide.Seg("gate", [p0, p1], ends=(O.key,), opacity=O.opacity))
        elif mode == "chevron":
            sx = 1 if B.x > A.x else -1
            s0 = guide.extent(A, sx * guide.DIAG, -guide.DIAG) + guide.GAP
            q0 = (A.x + sx * guide.DIAG * s0, A.y - guide.DIAG * s0)
            e = guide.DOT + guide.GAP
            q2 = (mx - sx * guide.DIAG * e, my - guide.DIAG * e)
            # the two 45-degree legs meet at the top
            px = (q0[0] + q2[0]) / 2 + sx * (q0[1] - q2[1]) / 2
            q1 = (px, q0[1] - abs(px - q0[0]))
            path, samples = guide.rounded_path([q0, q1, q2], guide.BEND / 2)
            d.segs.append(guide.Seg("gate", samples, d=path, ends=(A.key,), opacity=A.opacity))
        else:
            raise ValueError(f"unknown gate variant {mode!r}")


guide.gates = gates_variant


def variants_section(doc: dict, va: dict, panels: Panels) -> str:
    """One option per chip; each redraws the listed panels with every row's `gate` set to the
    option's mode, beside nothing: the first option is the chart as it is."""
    arch = doc["architectures"]
    opts = va["options"]
    first = next((o["key"] for o in opts if "recommended" in o["label"]), opts[0]["key"])
    views = []
    for o in opts:
        cards = []
        for pid in va["panels"]:
            a = arch[pid]
            spec = copy.deepcopy(a["diagram"])
            for r in spec["rows"]:
                if r.get("gate") and (r["gate"] == "recurrent" or o.get("all")):
                    r["gate"] = o["gate"]
            cards.append(card(panels, f'{pid}-{o["key"]}', dict(a, diagram=spec), spec=spec))
        hidden = "" if o["key"] == first else " hidden"
        views.append(
            f'<div class="variant" data-view="{va["key"]}" data-side="{o["key"]}"{hidden}>'
            f'<h3>{esc(o["label"])}</h3><p class="note">{esc(para(o["note"]))}</p>'
            f'<div class="vgrid">{"".join(cards)}</div></div>'
        )
    return (
        f'<h2>{esc(va["title"])}</h2><p class="note">{esc(para(va["intro"]))}</p>'
        + chips(va["key"], [(o["key"], o["label"]) for o in opts], first)
        + "".join(views)
    )


def lookalike_section(doc: dict, la: dict, panels: Panels) -> str:
    arch = doc["architectures"]
    pairs = []
    for p in la["pairs"]:
        a, b = arch[p["a"]], arch[p["b"]]
        word = "Fix" if p["verdict"] == "fix" else "Keep"
        pairs.append(
            f'<div class="pair">{card(panels, p["a"], a)}{card(panels, p["b"], b)}'
            f'<p class="v"><span class="verdict">{word}</span>{esc(p["note"])}</p></div>'
        )
    fx = la["fix"]
    install_cells(fx.get("cells"))
    pid, a = fx["panel"], arch[fx["panel"]]
    after = dict(a, diagram=fx["diagram"])
    legend = "".join(
        f'<span class="muted">Legend, under {esc(guide.GROUPS[c["group"]]["label"])}:</span> '
        f'<svg viewBox="-9 -9 18 18" width="22" height="22" style="vertical-align:middle">'
        f'{guide.cell_svg(k, 0, 0, ink="currentColor", marks=False)}</svg> {esc(c["label"])}'
        for k, c in (fx.get("cells") or {}).items()
    )
    fix = (
        f'<div class="change">{card(panels, pid, a, "Now")}{card(panels, pid + "-after", after, "Proposed", fx["diagram"])}'
        f'<div class="why"><h3>{esc(a["name"])}</h3><p>{esc(para(fx["note"]))}</p>'
        f'<p class="what">Reading note: {esc(para(fx["read"]))}</p><p class="what">{legend}</p></div></div>'
    )
    grid = (
        f'<div class="pairs">{"".join(pairs)}</div><p class="crop-label">The fix</p>'
        if pairs
        else ""
    )
    return f'<h2>Panels drawn alike</h2><p class="note">{esc(para(la["intro"]))}</p>{grid}{fix}'


def candidate_section(doc: dict, cands: list, panels: Panels) -> str:
    arch = doc["architectures"]
    out = []
    for c in cands:
        near = "".join(card(panels, n, arch[n], "Nearest on the chart") for n in c["nearest"][:2])
        word = {"include": "Include", "fold": "Fold in"}[c["verdict"]]
        cites = "; ".join(p["cite"] for p in c["papers"])
        out.append(
            f'<div class="cand">{card(panels, c["id"], c, "Draft")}{near}'
            f'<div class="why"><h3><span class="verdict">{word}</span>{esc(c["name"])}</h3>'
            f'<p>{esc(para(c["verdict_note"]))}</p>'
            f'<p class="what">{esc(para(c["summary"]))}</p>'
            f'<p class="what">Reading note: {esc(para(c["read"]))}</p>'
            f'<p class="what">{esc(cites)}</p></div></div>'
        )
    return (
        '<h2>Neural ODE</h2><p class="note">The draft, from its spec in '
        "<code>proofs/proposals.yaml</code>, beside the panels nearest to it.</p>" + "".join(out)
    )


def layout_section(doc: dict, layouts: list, cands: list, panels: Panels) -> str:
    arch = dict(doc["architectures"])
    new = {c["id"]: c for c in cands}
    arch.update(new)
    first = next((l["key"] for l in layouts if "recommended" in l["label"]), layouts[0]["key"])
    views = []
    for lay in layouts:
        rows = []
        for row in lay["rows"]:
            fams, slots, col = [], [], 1
            for title, pids in row:
                fams.append(
                    f'<div class="fam" style="grid-column:{col} / span {len(pids)}">{esc(title)}</div>'
                )
                for pid in pids:
                    a = arch[pid]
                    cls = (
                        "slot new"
                        if pid in new
                        else "slot moved" if pid in (lay.get("moved") or []) else "slot"
                    )
                    slots.append(
                        f'<div class="{cls}" style="grid-column:{col}">{panels.use(pid, a["diagram"], a["name"])}'
                        f'<span class="nm">{esc(a["name"])}</span></div>'
                    )
                    col += 1
            slots += [
                f'<div class="slot empty" style="grid-column:{k}" aria-label="Empty place"></div>'
                for k in range(col, 7)
            ]
            rows.append(f'<div class="lrow">{"".join(fams)}{"".join(slots)}</div>')
        folded = lay.get("folded") or {}
        fold = (
            (
                "Folded into an existing panel's papers: "
                + "; ".join(
                    f'{esc(", ".join(new[c]["name"] for c in cs))} into {esc(doc["architectures"][p]["name"])}'
                    for p, cs in folded.items()
                )
                + "."
            )
            if folded
            else ""
        )
        n = sum(len(p) for row in lay["rows"] for _, p in row)
        gone = lay.get("removed") or []
        if gone:
            fold += " Leaves the chart: " + ", ".join(esc(arch[g]["name"]) for g in gone) + "."
        hidden = "" if lay["key"] == first else " hidden"
        views.append(
            f'<div class="layout" data-view="layout" data-side="{lay["key"]}"{hidden}>'
            f'<h3>{esc(lay["label"])}</h3><p class="note">{esc(para(lay["note"]))}</p>'
            f'{"".join(rows)}<p class="folded">{fold} {n} panels, {len(lay["rows"])} rows.</p></div>'
        )
    return (
        '<h2>Making room at 42</h2><p class="note">The poster has six places to a row. A family of six fills a '
        "row and two families of three share one, so Neural ODE can only join Flows if a panel leaves. "
        "New panels are outlined; panels that change family are marked Moved.</p>"
        + chips("layout", [(l["key"], l["label"]) for l in layouts], first)
        + "".join(views)
    )


STARTER = """\
# Proposals for the field guide, drawn by proposals.py into proofs/proposals.html.
# Nothing here changes the chart: each proposal is drawn beside what it would replace.
# Adopting one means copying its values into guide.py or architectures.yaml; once a proposal
# is adopted or declined, its section leaves this file. Every section is optional:
# sizes, marks, glyphs, page, sharing, sharing_all, variants, lookalikes, candidates,
# layouts (each read by the *_section function of the same name in proposals.py).

date: {today}
intro: >-
  What this round of proposals is about.

# variants: other ways to draw one thing, on every panel that has it. Each option sets
# `gate` (hook, output, chevron or single) on the panels' gated rows.
# variants:
# - key: gates
#   title: A gate that also reads the previous state
#   intro: Each option redraws the LSTM and the GRU.
#   panels: [lstm, gru, mamba]
#   options:
#   - {{key: hook, gate: hook, label: Now, note: The chart as it is.}}
#   - {{key: chevron, gate: chevron, label: 1 · A 45-degree peak, note: Why it might be better.}}
"""


def build():
    doc = guide.load(os.path.join(HERE, "architectures.yaml"))
    path = os.path.join(HERE, "proofs", "proposals.yaml")
    if not os.path.exists(path):
        guide.write(path, STARTER.format(today=guide.datetime.date.today().isoformat()))
        print(f"wrote a starter {path}: add a proposal to it, then run this again")
        return
    prop = yaml.safe_load(open(path, encoding="utf-8"))
    panels = Panels()
    # every section is optional: a proposal leaves this file once it is adopted or declined
    tones_html, poster_sprite = (
        sizes_section(doc, prop["sizes"]) if prop.get("sizes") else ("", "")
    )
    marks_html = marks_section(doc, prop["marks"], panels) if prop.get("marks") else ""
    marks_html += glyphs_section(doc, prop["glyphs"], panels) if prop.get("glyphs") else ""
    marks_html += page_section(doc, prop["page"]) if prop.get("page") else ""
    if prop.get("sharing_all"):
        sa_html, sa_sprite = sharing_all_section(doc, prop["sharing_all"])
        marks_html += sa_html + sa_sprite
    if prop.get("sharing"):
        sh_html, sh_sprite = sharing_section(doc, prop["sharing"])
        marks_html += sh_html + sh_sprite
    for va in prop.get("variants") or []:
        marks_html += variants_section(doc, va, panels)
    look_html = (
        lookalike_section(doc, prop["lookalikes"], panels) if prop.get("lookalikes") else ""
    )
    cand_html = (
        candidate_section(doc, prop["candidates"], panels) if prop.get("candidates") else ""
    )
    lay_html = (
        layout_section(doc, prop["layouts"], prop.get("candidates") or [], panels)
        if prop.get("layouts")
        else ""
    )

    warns = list(getattr(panels, "lint", []))
    for c in prop.get("candidates") or []:
        if guide.text_width(c["caption"], "body", guide.TYPE["caption"][0]) > guide.W - 6:
            warns.append(f"{c['id']}: caption is too wide for the panel")
        if c["objective"] not in guide.OBJECTIVES:
            warns.append(f"{c['id']}: unknown objective '{c['objective']}'")

    date = prop.get("date")
    date_text = guide.date_text(date) if hasattr(date, "year") else str(date)
    body = f"""<div class="page">
<div class="topbar"><span class="eyebrow">Neural Network Field Guide · proofs</span>
<button class="icon-btn" type="button" id="theme" aria-label="Switch to dark mode" title="Switch to dark mode">{guide.THEME_ICONS}</button></div>
<h1>Field Guide Proposals, {esc(date_text)}</h1>
<p class="lede">{esc(para(prop["intro"]))}</p>
<p class="how">Nothing on this page has changed the chart. Every drawing is made by <code>guide.py</code> from the current specs, or from a proposal's spec in <code>proofs/proposals.yaml</code>.</p>
{poster_sprite}{tones_html}
{marks_html}
{look_html}
{cand_html}
{lay_html}
{panels.sprite()}
<footer>{esc(doc["copyright"])}. Drawn by <code>proposals.py</code> from <code>proofs/proposals.yaml</code> and the current specs.</footer>
</div>
<script>{revision.PAGE_JS}</script>"""
    head = (
        f'<meta charset="utf-8">\n<title>Field Guide Proposals</title>\n'
        f'<meta name="claude:viewport-fit-sides" content="cover">\n'
        f'<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'<link rel="stylesheet" href="{guide.FONT_LINK}">\n'
        f"<style>{revision.PAGE_CSS}{EXTRA_CSS}</style>\n"
        "<script>try{var t=localStorage.getItem('revision-theme');"
        "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>\n"
    )
    out = os.path.join(HERE, "proofs", "proposals.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(head + body)
    for w in warns:
        print(f"lint: {w}")
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    build()
