#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Reid Johnson
# SPDX-License-Identifier: MIT
"""
revision.py: a proof of what changed in the field guide, panel by panel.

Draws the chart twice, from a previous copy of guide.py and architectures.yaml (--before DIR,
normally proofs/before/) and from the current ones, and writes one page that puts each
changed panel's old and new drawing side by side, then the legend and the whole poster,
before and after:

  proofs/revision.html    the proof, as a fragment ready to publish

The changes are found by comparing the two specs; proofs/revision-notes.yaml, if present,
says why each was made (keys: date, intro, legend, panels.<id>).

proofs/ is a local working folder, not part of the repository. Before a round of changes,
`python revision.py --snapshot` copies the current guide.py and architectures.yaml into
proofs/before/; afterwards `python revision.py` draws what changed since.
Usage:  python revision.py [--snapshot] [--before proofs/before]
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import os
import shutil
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guide  # noqa: E402

FIELDS = ["name", "year", "caption", "note", "objective", "summary", "read", "papers", "diagram"]


def load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name while it loads
    spec.loader.exec_module(mod)
    return mod


def describe(old: dict, new: dict) -> list:
    """What differs between two entries, in words."""
    out = []
    for f in FIELDS:
        a, b = old.get(f), new.get(f)
        if a == b:
            continue
        if f == "year":
            out.append(f"year {a} → {b}")
        elif f == "papers":
            cites_a = {p.get("cite") for p in a or []}
            cites_b = {p.get("cite") for p in b or []}
            added = [p.get("cite") for p in b or [] if p.get("cite") not in cites_a]
            gone = [p.get("cite") for p in a or [] if p.get("cite") not in cites_b]
            bits = [f"+ {c}" for c in added] + [f"− {c}" for c in gone]
            out.append("papers: " + ", ".join(bits) if bits else "papers reworded")
        elif f == "note":
            out.append(
                "footnote added"
                if b and not a
                else "footnote removed" if a and not b else "footnote reworded"
            )
        elif f == "diagram":
            ra, rb = len((a or {}).get("rows", [])), len((b or {}).get("rows", []))
            out.append(f"diagram: {ra} → {rb} rows" if ra != rb else "diagram redrawn")
        elif f == "caption":
            out.append(f"caption “{a}” → “{b}”")
        elif f == "name":
            out.append(f"name “{a}” → “{b}”")
        elif f == "objective":
            out.append(f"objective {a} → {b}")
        else:
            out.append(f"{f} reworded")
    return out


def panel_svg(mod, d, mid: str, label: str) -> str:
    return (
        f'<svg viewBox="0 0 {mod.fmt(mod.W)} {mod.fmt(mod.H)}" role="img" aria-label="{html.escape(label)}">'
        f'<defs>{mod.marker_def(mid, "currentColor")}</defs>{mod.render_diagram(d, mid)}</svg>'
    )


def card(mod, doc, pid: str, side: str) -> str:
    a, d = doc["architectures"][pid], mod.build_diagram(pid, doc["architectures"][pid]["diagram"])
    notes = mod.footnotes(doc) if hasattr(mod, "footnotes") else {}
    caption = mod.caption_text(notes, pid, a) if hasattr(mod, "caption_text") else a["caption"]
    return (
        f'<figure class="panel"><figcaption class="side">{side}</figcaption>'
        f'<p class="name">{html.escape(a["name"])}</p>{panel_svg(mod, d, f"arr-{pid}-{side.lower()}", a["name"])}'
        f'<p class="cap">{mod.prose_html(caption) if hasattr(mod, "prose_html") else html.escape(caption)}</p><p class="meta">{a["year"]} · {html.escape(a["objective"])}</p></figure>'
    )


def legend_svg(mod, doc, diagrams, mid: str) -> str:
    cells, links = mod.used_kinds(diagrams)
    inner, h = mod.legend_columns(cells, links, 0.0, "currentColor", mid)
    w = mod.POSTER_COLS * mod.W + (mod.POSTER_COLS - 1) * mod.POSTER_GX
    return (
        f'<svg viewBox="0 0 {mod.fmt(w)} {mod.fmt(h + 6)}" role="img" aria-label="Legend">'
        f'<defs>{mod.marker_def(mid, "currentColor")}</defs>{inner}</svg>'
    )


PAGE_CSS = r"""
/* Layout: a proofing desk. Each change is one row, old drawing beside new, with the reason
   under it; then the legend and the full poster, each with a Before/After switch. */
:root {
  --bg: #FFFFFF; --surface: #F4F4F1; --ink: #1C1B19; --muted: #66655F; --rule: #E3E2DD;
  --focus: #0077CC; --on: #1C1B19; --on-ink: #FFFFFF;
  --display: 'Poppins', 'Avenir Next', 'Segoe UI', Helvetica, Arial, sans-serif;
  --body: 'Lato', 'Helvetica Neue', Helvetica, Arial, sans-serif;
  --mono: 'Noto Sans Mono', ui-monospace, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141413; --surface: #1F1F1D; --ink: #ECEBE6; --muted: #A3A19A; --rule: #302F2C;
    --focus: #4DB8FF; --on: #ECEBE6; --on-ink: #141413; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #141413; --surface: #1F1F1D; --ink: #ECEBE6; --muted: #A3A19A; --rule: #302F2C;
  --focus: #4DB8FF; --on: #ECEBE6; --on-ink: #141413; color-scheme: dark;
}
:root[data-theme="light"] { color-scheme: light; }
[hidden] { display: none !important; }
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
.change { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.1fr); gap: 18px 24px;
  align-items: start; padding: 18px 0; border-bottom: 1px solid var(--rule); }
.change:last-child { border-bottom: 0; }
.panel { margin: 0; min-width: 0; }
.panel .side { font: 500 11px/1.2 var(--display); letter-spacing: 0.13em; text-transform: uppercase; color: var(--muted); }
.panel .name { font: 500 15px/1.25 var(--display); margin: 8px 0 2px; }
.panel svg { width: 100%; height: auto; display: block; }
.panel .cap { margin: 4px 0 0; color: var(--muted); font-size: 13px; }
.panel .meta { margin: 2px 0 0; color: var(--muted); font: 11.5px/1.4 var(--mono); }
.why { min-width: 0; align-self: center; }
.why h3 { font: 500 17px/1.25 var(--display); margin: 0 0 6px; }
.why p { margin: 0; max-width: 56ch; text-wrap: pretty; }
.why .what { margin-top: 8px; color: var(--muted); font: 12px/1.6 var(--mono); }
.switch { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 2; background: var(--bg);
  border-bottom: 1px solid var(--rule); padding-block: 10px; display: flex; gap: 6px; align-items: center; }
.switch .eyebrow { margin-right: 6px; }
.chip { font: 500 12.5px/1 var(--display); padding: 8px 12px; border-radius: 999px; border: 1px solid var(--rule);
  background: transparent; color: var(--ink); cursor: pointer; }
.chip:hover { border-color: var(--muted); }
.chip[aria-pressed="true"] { background: var(--on); color: var(--on-ink); border-color: var(--on); }
.legend svg { width: 100%; height: auto; display: block; margin-top: 14px; }
.poster { margin-top: 14px; border: 1px solid var(--rule); border-radius: 12px; overflow: hidden; }
.poster svg { width: 100%; height: auto; display: block; }
.summary { margin: 10px 0 0; color: var(--muted); font-size: 14px; }
.text-only { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.text-only .t-name { font: 500 15px/1.3 var(--display); margin-right: 6px; }
footer { margin-top: 48px; border-top: 1px solid var(--rule); padding-top: 14px; color: var(--muted); font-size: 13px; }
footer code { font: 12px var(--mono); }
@media (max-width: 760px) {
  .change { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .why { grid-column: 1 / -1; }
}
"""

PAGE_JS = r"""
(() => {
  // Each Before/After switch shows one of two drawings in place, so a change can be flipped.
  document.querySelectorAll('[data-switch]').forEach(group => {
    const target = group.dataset.switch;
    const views = Array.from(document.querySelectorAll('[data-view="' + target + '"]'));
    const chips = Array.from(group.querySelectorAll('.chip'));
    chips.forEach(ch => ch.addEventListener('click', () => {
      chips.forEach(o => o.setAttribute('aria-pressed', String(o === ch)));
      views.forEach(v => { v.hidden = v.dataset.side !== ch.dataset.side; });
    }));
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
    try { localStorage.setItem('revision-theme', next); } catch (e) {}
    label();
  });
  if (mq && mq.addEventListener) mq.addEventListener('change', label);
  label();
})();
"""


def switch_html(target: str) -> str:
    return (
        f'<div class="switch" role="group" aria-label="Before or after" data-switch="{target}">'
        f'<span class="eyebrow">Show</span>'
        f'<button class="chip" type="button" data-side="after" aria-pressed="true">After</button>'
        f'<button class="chip" type="button" data-side="before" aria-pressed="false">Before</button></div>'
    )


def build(before_dir: str):
    old = load_module(os.path.join(before_dir, "guide.py"), "guide_before")
    old_doc = old.load(os.path.join(before_dir, "architectures.yaml"))
    new_doc = guide.load(os.path.join(HERE, "architectures.yaml"))
    old_diagrams, new_diagrams = old.build_all(old_doc), guide.build_all(new_doc)
    notes_path = os.path.join(HERE, "proofs", "revision-notes.yaml")
    notes = (
        yaml.safe_load(open(notes_path, encoding="utf-8")) if os.path.exists(notes_path) else {}
    )
    panel_notes = notes.get("panels") or {}

    order = [p for s in new_doc["sections"] for p in s["panels"]]
    rows, changed, text_only = [], [], []
    for pid in order:
        a, b = old_doc["architectures"].get(pid), new_doc["architectures"][pid]
        what = describe(a, b) if a else ["new panel"]
        # the drawing can change without its spec changing, when the renderer's styles do
        restyled = (
            bool(a)
            and a.get("diagram") == b.get("diagram")
            and (
                panel_svg(old, old_diagrams[pid], "arr-x", "")
                != panel_svg(guide, new_diagrams[pid], "arr-x", "")
            )
        )
        if restyled:
            what.append("drawing restyled")
        if not what:
            continue
        changed.append(pid)
        # anything the poster prints under or over a drawing gets a before-and-after row
        shown = (
            not a
            or restyled
            or any(
                a.get(f) != b.get(f)
                for f in ("diagram", "name", "caption", "year", "objective", "note")
            )
        )
        if not shown:  # only the page's text changed; listed below the drawings
            text_only.append((b["name"], what))
            continue
        before = (
            card(old, old_doc, pid, "Before")
            if a
            else '<figure class="panel"><figcaption class="side">Before</figcaption><p class="cap">Not in the chart.</p></figure>'
        )
        why = " ".join(str(panel_notes.get(pid, "")).split())
        rows.append(
            f'<div class="change" id="c-{pid}">{before}{card(guide, new_doc, pid, "After")}'
            f'<div class="why"><h3>{html.escape(b["name"])}</h3>'
            + (f"<p>{html.escape(why)}</p>" if why else "")
            + f'<p class="what">{html.escape("; ".join(what))}</p></div></div>'
        )
    for pid in old_doc["architectures"]:
        if pid not in new_doc["architectures"]:
            a = old_doc["architectures"][pid]
            rows.append(
                f'<div class="change" id="c-{pid}">{card(old, old_doc, pid, "Before")}'
                f'<figure class="panel"><figcaption class="side">After</figcaption><p class="cap">Removed.</p></figure>'
                f'<div class="why"><h3>{html.escape(a["name"])}</h3><p class="what">removed</p></div></div>'
            )
            changed.append(pid)
    text_html = ""
    if text_only:
        items = "".join(
            f'<li><span class="t-name">{html.escape(n)}</span> <span class="what">{html.escape("; ".join(w))}</span></li>'
            for n, w in text_only
        )
        text_html = f'<h2>Text changes on the page</h2><ul class="text-only">{items}</ul>'

    frame_changed = [
        k
        for k in ("title", "subtitle", "reading", "credit", "license")
        if old_doc.get(k) != new_doc.get(k)
    ]
    legend_before, legend_after = legend_svg(old, old_doc, old_diagrams, "arr-leg-b"), legend_svg(
        guide, new_doc, new_diagrams, "arr-leg-a"
    )
    # both posters name their arrowhead "arr"; on one page each needs its own
    poster_before = (
        old.poster_svg(old_doc, old_diagrams, "light")
        .replace('id="arr"', 'id="arr-pb"')
        .replace("url(#arr)", "url(#arr-pb)")
    )
    poster_after = (
        guide.poster_svg(new_doc, new_diagrams, "light")
        .replace('id="arr"', 'id="arr-pa"')
        .replace("url(#arr)", "url(#arr-pa)")
    )

    date = notes.get("date") or new_doc.get("updated")
    date_text = guide.date_text(date) if hasattr(date, "year") else str(date)
    intro = " ".join(str(notes.get("intro", "")).split())
    legend_note = " ".join(str(notes.get("legend", "")).split())
    n = len(changed)
    frame = (
        (
            ", and the chart's "
            + (
                " and ".join(frame_changed)
                if len(frame_changed) <= 2
                else ", ".join(frame_changed[:-1]) + " and " + frame_changed[-1]
            )
        )
        if frame_changed
        else ""
    )
    summary = (
        f'{n} panel{"s" if n != 1 else ""} changed{frame}. '
        f"Before: {guide.date_text(old.updated_date(old_doc))}. After: {date_text}."
    )

    body = f"""<div class="page">
<div class="topbar"><span class="eyebrow">Neural Network Field Guide · proofs</span>
<button class="icon-btn" type="button" id="theme" aria-label="Switch to dark mode" title="Switch to dark mode">{guide.THEME_ICONS}</button></div>
<h1>Field Guide Revision, {html.escape(date_text)}</h1>
<p class="lede">{html.escape(intro) if intro else "Every panel that changed, drawn before and after, with the legend and the whole poster."}</p>
<p class="how">{html.escape(summary)} Each row below is one panel, drawn from the previous specs on the left and the current ones on the right, with the reason for the change. The legend and the full poster follow, each with a switch to flip between the two.</p>
<h2>Changed panels</h2>
{"".join(rows) if rows else '<p class="summary">No panel changed.</p>'}
{text_html}
<h2>Legend</h2>
{(f'<p class="summary">{html.escape(legend_note)}</p>' if legend_note else "")}
{switch_html("legend")}
<div class="legend" data-view="legend" data-side="after">{legend_after}</div>
<div class="legend" data-view="legend" data-side="before" hidden>{legend_before}</div>
<h2>The poster</h2>
{switch_html("poster")}
<div class="poster" data-view="poster" data-side="after">{poster_after}</div>
<div class="poster" data-view="poster" data-side="before" hidden>{poster_before}</div>
<footer>{html.escape(new_doc["copyright"])}. Drawn by <code>revision.py</code> from <code>{html.escape(os.path.relpath(before_dir, HERE))}/</code> and the current specs.</footer>
</div>
<script>{PAGE_JS}</script>"""
    head = (
        f'<meta charset="utf-8">\n<title>Field Guide Revision</title>\n'
        f'<meta name="claude:viewport-fit-sides" content="cover">\n'
        f'<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'<link rel="stylesheet" href="{guide.FONT_LINK}">\n'
        f"<style>{PAGE_CSS}</style>\n"
        "<script>try{var t=localStorage.getItem('revision-theme');"
        "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>\n"
    )
    out = os.path.join(HERE, "proofs", "revision.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(head + body)
    for pid in changed:
        print(f"changed: {pid}")
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--before",
        default=os.path.join(HERE, "proofs", "before"),
        help="directory holding the previous guide.py and architectures.yaml",
    )
    ap.add_argument(
        "--snapshot",
        action="store_true",
        help="copy the current guide.py and architectures.yaml there, to compare with later",
    )
    args = ap.parse_args(argv)
    names = ("guide.py", "architectures.yaml")
    if args.snapshot:
        os.makedirs(args.before, exist_ok=True)
        for name in names:
            shutil.copy2(os.path.join(HERE, name), os.path.join(args.before, name))
        print(f"copied {' and '.join(names)} to {args.before}")
        return
    if not all(os.path.exists(os.path.join(args.before, n)) for n in names):
        sys.exit(
            f"nothing to compare with in {args.before}: run `python revision.py --snapshot`"
            " before a round of changes"
        )
    build(args.before)


if __name__ == "__main__":
    main()
