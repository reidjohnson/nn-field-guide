#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Reid Johnson
# SPDX-License-Identifier: MIT
"""
guide.py: draw the Neural Network Field Guide from declarative specs.

One YAML file (architectures.yaml) describes every architecture: what to draw
(rows of cells and the rules that connect them) plus its metadata (year,
caption, objective, summary, papers). This module lays every diagram out on
the same grid, checks it, and renders it:

  dist/panels/<id>.svg       one diagram per architecture
  dist/neural-network-field-guide.svg/.pdf/.png  the full chart, plus a dark PNG
  dist/web/index.html        interactive version, standalone file
  dist/web/artifact.html     the same page as a fragment, ready to publish

Usage:
  python guide.py build             lint, then render everything
  python guide.py build --strict    stop if lint finds anything
  python guide.py lint              lint only

Requires PyYAML. PDF/PNG export needs cairosvg; exact text measurement for
lint uses Pillow plus the fonts (Poppins, Lato, Noto Sans Mono) if installed.
"""

from __future__ import annotations

import argparse
import datetime
import html
import io
import json
import math
import os
import re
import subprocess
import sys
from collections import OrderedDict
from dataclasses import dataclass, field

import yaml

if sys.version_info < (3, 10):  # the oldest Python still getting fixes, and Black's floor
    sys.exit(f"The field guide needs Python 3.10 or newer; this is {sys.version.split()[0]}.")

# --------------------------------------------------------------------------
# Theme: every size, color, and font lives here so all panels stay consistent
# --------------------------------------------------------------------------

W, H = 208.0, 152.0  # diagram canvas in px
Y_BOTTOM, Y_TOP = 130.0, 14.0  # centre of the first and last level, before PAD_Y
PAD_Y = 4.0  # every y moves down this far, leaving room for the marks
R = 8.0  # cell radius; squares, diamonds, and inner marks scale with it
R0 = 7.0  # the radius the cell types' sizes (`half`) and inner marks are drawn for
RING_PAD = 2.3  # from a cell's outline to the state group's outer ring
STEM, EAR, MARK_GAP = 4.5, 3.5, 1.5  # input/output stem, mixing tick, gap from the outline
PITCH = 40.0  # default spacing between cells in a row
SIDE = 16.0  # min distance from canvas side to a cell centre
CLEARANCE = 2.0  # a line must clear cells it doesn't touch by this

# Lines: every link, arrow, skip connection, and return arrow is drawn at one weight and
# ends the same distance from the outline it meets; styles differ only by dash and head.
LINE = 0.8  # stroke width of every line, the same as the cell outlines
GAP = 0.9  # from a line's end (or an arrow's tip) to the outline it meets
HEAD = 5.4  # arrowhead size, the same on every arrow
DOT = 1.7  # radius of the dot where a gate meets a state arrow
BEND = 6.0  # radius of every bend in a skip connection or return arrow
REACH = 16.0  # from the cell centres to the straight run of every connector: one shape for all
DIAG = math.sqrt(0.5)  # connectors leave and meet cells at 45 degrees

PALETTE = {  # fill, outline: the Zoo's five hues, tuned so color-blind readers can tell them
    # apart (measured in palettes.py); each outline is 14 L* darker than its fill.
    # The Zoo's own were FFCC00, 66CC00, FF6600, 0099FF, FF99FF.
    "yellow": ("#E9DD03", "#BEB600"),
    "green": ("#3FBF0A", "#009800"),
    "red": ("#CE481E", "#A21F00"),  # the Zoo's orange, darkened toward red (see palettes.py)
    "blue": ("#578DE6", "#236ABE"),
    "pink": ("#F4A1FD", "#CB7BD5"),
}
GLYPH_INK = "#1A1A1A"  # the ink of a cell's inner mark
GLYPH_INK_ON = {
    "red": "#FFFFFF"
}  # color -> ink where black reads poorly: 4.6:1 on the red, not 3.8:1
GLYPH_STROKE = 0.9  # stroke width of inner marks, at R0
INK_LIGHT, BG_LIGHT = "#1C1B19", "#FFFFFF"
INK_DARK, BG_DARK = "#ECEBE6", "#161615"

# Poster type: each role's size in px and the opacity of the ink it is set in. The opacities are
# the print-safe tones (proofs/proposals.html): every text role at 7.5:1 or more on both posters.
TYPE = {
    "title": (48, 1.0),
    "subtitle": (22, 1.0),
    "reading": (16, 0.75),
    "legend_head": (15, 1.0),
    "legend": (13.5, 0.9),
    "section": (13, 0.78),
    "name": (16, 1.0),
    "caption": (13.5, 0.82),
    "meta": (11.5, 0.75),
    "credit": (13.5, 0.75),
    "notice": (13.5, 0.75),  # the same as the credit above it
}
DIM = 0.52  # faded cells and links: a row's dim_opacity, raised to at least this

FONTS = {
    "display": ("Poppins", "'Avenir Next', 'Segoe UI', Helvetica, Arial, sans-serif"),
    "body": ("Lato", "'Helvetica Neue', Helvetica, Arial, sans-serif"),
    "mono": ("Noto Sans Mono", "ui-monospace, Menlo, Consolas, monospace"),
}
# The PDF and PNG set regular text in these faces, named outright. cairosvg asks Cairo only
# for normal or bold; Cairo on Linux draws normal in a family's Medium face, but on macOS it
# looks the family up by name and gets Regular. Naming the face draws the same on both.
EXPORT_FACES = {"Poppins": "Poppins Medium", "Lato": "Lato Medium"}

# Color means function: every cell in a group shares one of the Neural Network Zoo's colors, and
# one mark, so the groups stay apart without color (under simulated color blindness each
# of the five colors nearly matches another, as palettes.py shows). in/out: a stem pointing away
# from the cell's links; sides: a tick each side; ring: an outer ring; None: no mark.
GROUPS = OrderedDict(
    [
        ("input", dict(color="yellow", label="Input", mark="in")),
        ("hidden", dict(color="green", label="Hidden", mark=None)),
        ("state", dict(color="blue", label="State", mark="ring")),
        ("mixing", dict(color="pink", label="Mixing", mark="sides")),
        ("output", dict(color="red", label="Output", mark="out")),
    ]
)
LINK_GROUP = "Links"

# Cell types, in legend order. The glyph is an inner mark that refines the group.
CELLS = OrderedDict(
    [
        ("input", dict(group="input", label="Input")),
        ("patch", dict(group="input", shape="square", half=6.0, label="Image patch")),
        ("noisy", dict(group="input", glyph="triangle", label="Noisy input")),
        ("noise", dict(group="input", glyph="wave", label="Random noise")),
        ("backfed", dict(group="input", glyph="ring", label="Visible unit (in and out)")),
        ("mask", dict(group="input", hollow=True, label="Masked token")),  # the hidden input, last
        ("hidden", dict(group="hidden", label="Hidden")),
        ("prob", dict(group="hidden", glyph="ring", label="Probabilistic hidden")),
        ("capsule", dict(group="hidden", glyph="square", label="Capsule (vector output)")),
        (
            "expert",
            dict(group="hidden", shape="square", glyph="layers", label="Expert (a whole network)"),
        ),
        ("router", dict(group="hidden", shape="diamond", half=5.5, label="Router")),
        ("recurrent", dict(group="state", label="Recurrent")),
        ("memory", dict(group="state", glyph="ring", label="Memory cell")),
        ("gated", dict(group="state", glyph="triangle", label="Gated recurrent")),
        ("selective", dict(group="state", glyph="diamond", label="Selective state (SSM)")),
        (
            "matrix",
            dict(group="state", shape="square", label="Matrix-valued state"),
        ),  # the one square, last
        ("attention", dict(group="mixing", label="Attention")),
        ("conv", dict(group="mixing", glyph="ring", label="Convolution or pooling")),
        ("output", dict(group="output", label="Output")),
        ("match", dict(group="output", glyph="ring", label="Reconstruction of input")),
        ("velocity", dict(group="output", glyph="arrow", label="Velocity")),
    ]
)

# Link styles, in legend order. All are drawn at LINE; a style changes only the dash,
# the opacity, the arrowhead or (skip, loop, func) the path. An arrowhead means one-way
# flow: the straight arrow carries state, a message, or data from one part to another, and
# the elbow arrow carries an output back to the input to run the network again. A gate is
# dotted and acts at a dot on a state arrow.
LINKS = OrderedDict(
    [
        # in legend order: plain lines, then arrows (with the gate after the arrow it acts on),
        # then dashed comparison, then KAN's learnable function
        ("link", dict(label="Connection")),
        ("frozen", dict(opacity=0.58, label="Fixed random weights")),
        ("skip", dict(label="Skip connection")),
        ("state", dict(arrow=True, label="Data passed on or fed back")),
        # the return arrow means the same as the straight one (one-way flow of data), so the
        # legend and the keys show it under the straight arrow's entry
        ("loop", dict(arrow=True, label="Data passed on or fed back", legend_as="state")),
        ("gate", dict(dash="1 2", label="Gate, acting at the dot")),
        ("cross", dict(dash="3 2", label="Cross-attention, comparison")),
        ("func", dict(label="Learnable function")),
    ]
)

OBJECTIVES = [
    "supervised",
    "next token",
    "masked token",
    "reconstruct",
    "denoise",
    "variational",
    "adversarial",
    "contrastive",
    "likelihood",
    "flow matching",
    "energy",
    "hebbian",
    "competitive",
]

# --------------------------------------------------------------------------
# Geometry model
# --------------------------------------------------------------------------


@dataclass
class Cell:
    key: str
    kind: str
    x: float
    y: float
    opacity: float = 1.0
    marks: bool = True  # draw the group mark
    mark_dir: tuple = None  # stem direction for input/output marks, set after the links


@dataclass
class Seg:
    style: str
    pts: list  # polyline used for drawing straight lines and for lint
    d: str = ""  # path data for connectors with bends
    ends: tuple = ()  # cells an end of this line meets (exactly GAP from the outline)
    opacity: float = 1.0
    arrow: bool = False
    near: tuple = ()  # cells of a group the line leaves from (exempt from clearance)


@dataclass
class Diagram:
    id: str
    cells: "OrderedDict[str, Cell]"
    groups: "OrderedDict[str, list]"
    segs: list = field(default_factory=list)
    dots: list = field(default_factory=list)


def fmt(v: float) -> str:
    s = f"{v:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return "0" if s == "-0" else s


def group_mark(cell: Cell):
    return GROUPS[CELLS[cell.kind]["group"]].get("mark") if cell.marks else None


def ring_pad(cell: Cell) -> float:
    """How far the state group's outer ring (and its stroke) sits outside the outline."""
    return RING_PAD + 0.4 if group_mark(cell) == "ring" else 0.0


def half_of(spec: dict, default: float) -> float:
    """Half the width of a square or diamond cell, scaled with R."""
    return spec.get("half", default) * R / R0


def extent(cell: Cell, ux: float = 1.0, uy: float = 0.0) -> float:
    """Distance from a cell's centre to its outline (or outer ring) in direction (ux, uy)."""
    spec = CELLS[cell.kind]
    shape = spec.get("shape", "circle")
    if shape == "square":
        return (half_of(spec, 7.0) + ring_pad(cell)) / max(abs(ux), abs(uy), 1e-9)
    if shape == "diamond":
        return half_of(spec, 5.0) / max(abs(ux) + abs(uy), 1e-9)
    return R + ring_pad(cell)


def radius(cell: Cell) -> float:
    """Radius-equivalent used for lint (the larger of the outline distances)."""
    spec = CELLS[cell.kind]
    if spec.get("shape") == "square":
        return (half_of(spec, 7.0) + ring_pad(cell)) * 1.12
    if spec.get("shape") == "diamond":
        return half_of(spec, 5.0)
    return R + ring_pad(cell)


def mark_lines(cell: Cell) -> list:
    """The group mark's stems or ticks as line segments (the ring is drawn separately)."""
    mark = group_mark(cell)
    if mark == "sides":
        dirs, length = [(1.0, 0.0), (-1.0, 0.0)], EAR
    elif mark in ("in", "out"):
        dirs, length = [cell.mark_dir or ((0.0, 1.0) if mark == "in" else (0.0, -1.0))], STEM
    else:
        return []
    out = []
    for ux, uy in dirs:
        s = extent(cell, ux, uy) + MARK_GAP
        out.append(
            (
                (cell.x + ux * s, cell.y + uy * s),
                (cell.x + ux * (s + length), cell.y + uy * (s + length)),
            )
        )
    return out


def assign_mark_dirs(d) -> None:
    """Inputs keep their stem below and outputs above whenever all of the cell's links lead
    the other way. Otherwise (a ring, or a link leaving on the stem's side) the stem points
    away from the links, snapped to the nearest of up, down, left, or right."""
    for c in d.cells.values():
        mark = group_mark(c)
        if mark not in ("in", "out"):
            continue
        default = (0.0, 1.0) if mark == "in" else (0.0, -1.0)
        vecs = []
        for s in d.segs:
            if c.key not in s.ends + s.near or len(s.pts) < 2:
                continue
            first, last = s.pts[0], s.pts[-1]
            near_first = math.hypot(first[0] - c.x, first[1] - c.y) <= math.hypot(
                last[0] - c.x, last[1] - c.y
            )
            q = s.pts[1] if near_first else s.pts[-2]
            vx, vy = q[0] - c.x, q[1] - c.y
            L = math.hypot(vx, vy)
            if L:
                vecs.append((vx / L, vy / L))
        if all(vx * default[0] + vy * default[1] < -0.05 for vx, vy in vecs):
            c.mark_dir = default
            continue
        sx, sy = sum(v[0] for v in vecs), sum(v[1] for v in vecs)
        if math.hypot(sx, sy) < 0.35:
            c.mark_dir = default
        elif abs(sx) > abs(sy):
            c.mark_dir = (-math.copysign(1.0, sx), 0.0)
        else:
            c.mark_dir = (0.0, -math.copysign(1.0, sy))


# --------------------------------------------------------------------------
# Layout: spec -> Diagram
# --------------------------------------------------------------------------


def level_ys(n_levels: int, span) -> list:
    lo, hi = span
    if n_levels <= 1:
        return [(lo + hi) / 2]
    step = (lo - hi) / (n_levels - 1)
    return [lo - i * step for i in range(n_levels)]


def centered_xs(n: int, pitch=None, center: float = W / 2) -> list:
    if n == 1:
        return [center]
    p = min(pitch or PITCH, (W - 2 * SIDE) / (n - 1))
    start = center - p * (n - 1) / 2
    return [start + i * p for i in range(n)]


def build_diagram(arch_id: str, spec: dict) -> Diagram:
    rows = spec.get("rows", [])
    span = spec.get("span", [Y_BOTTOM, Y_TOP])
    levels, auto = [], 0
    for r in rows:
        if any(k in r for k in ("y", "ys", "ring")):
            levels.append(None)
        else:
            lv = r.get("level", auto)
            levels.append(lv)
            auto = lv + 1
    used = [lv for lv in levels if lv is not None]
    ys = level_ys(spec.get("levels", (max(used) + 1) if used else 1), span)

    cells, groups, order = OrderedDict(), OrderedDict(), []
    for i, r in enumerate(rows):
        gid = r.get("id", f"r{i}")
        kinds = r["cell"] if isinstance(r["cell"], list) else None
        n = r.get("n", len(kinds) if kinds else 1)
        kinds = kinds or [r["cell"]] * n
        if len(kinds) != n:
            raise ValueError(f"{arch_id}.{gid}: {len(kinds)} cell types for n={n}")
        if "ring" in r:
            ring = r["ring"]
            cx, cy = ring.get("cx", W / 2), ring.get("cy", (span[0] + span[1]) / 2)
            a0 = math.radians(ring.get("start", -90))
            pos = [
                (
                    cx + ring["r"] * math.cos(a0 + 2 * math.pi * k / n),
                    cy + ring["r"] * math.sin(a0 + 2 * math.pi * k / n),
                )
                for k in range(n)
            ]
        else:
            xs = r.get("xs") or centered_xs(n, r.get("pitch"), r.get("center", W / 2))
            yy = r["ys"] if "ys" in r else [r["y"] if "y" in r else ys[levels[i]]] * n
            pos = list(zip(xs, yy))
        pos = [(x, y + PAD_Y) for x, y in pos]
        dim = set(r.get("dim", []))
        dim_op = max(r.get("dim_opacity", 0.32), DIM)
        keys = []
        for k, ((x, y), kind) in enumerate(zip(pos, kinds)):
            if kind not in CELLS:
                raise ValueError(f"{arch_id}.{gid}: unknown cell type '{kind}'")
            key = f"{gid}.{k}"
            cells[key] = Cell(
                key,
                kind,
                float(x),
                float(y),
                dim_op if k in dim else 1.0,
                marks=r.get("marks", True),
            )
            keys.append(key)
        groups[gid] = keys
        order.append((gid, r))

    d = Diagram(arch_id, cells, groups)
    prev = None
    for gid, r in order:
        if "connect" in r:
            src = r.get("from", prev)
            connect(d, groups[src], groups[gid], r["connect"], r.get("style", "link"), r)
        prev = gid
    for link in spec.get("links", []):
        connect(
            d,
            groups[link["from"]],
            groups[link["to"]],
            link.get("rule", "full"),
            link.get("style", "link"),
            link,
        )
    prev = None
    for gid, r in order:
        if "chain" in r:
            chain(d, groups[gid], r["chain"])
            if r.get("gate"):
                gates(d, groups[r.get("from", prev)], groups[gid], r["gate"])
        prev = gid
    for a in spec.get("arcs", []):
        arc(d, a)
    for lp in spec.get("loops", []):
        loop(d, lp)
    for ar in spec.get("arrows", []):
        arrow(d, ar)
    assign_mark_dirs(d)
    return d


def pairs_for(d: Diagram, rule: str, src: list, dst: list, params: dict) -> list:
    cells, ns, nd = d.cells, len(src), len(dst)
    name, k = rule, 1
    if rule.startswith("near:"):
        name, k = "near", int(rule.split(":")[1])
    if name == "full":
        return [(i, j) for j in range(nd) for i in range(ns)]
    if name == "causal":
        return [(i, j) for j in range(nd) for i in range(ns) if i <= j]
    if name == "pairs":
        return [tuple(p) for p in params["pairs"]]
    if name == "complete":
        return [(i, j) for i in range(ns) for j in range(i + 1, ns)]
    if name in ("one", "near"):
        if name == "one" and ns == nd:
            return [(i, i) for i in range(ns)]
        out = []
        for j in range(nd):
            xj = cells[dst[j]].x
            # nearest by x; ties go to the source farther from the centre (keeps it symmetric)
            ranked = sorted(
                range(ns),
                key=lambda i: (round(abs(cells[src[i]].x - xj), 3), -abs(cells[src[i]].x - W / 2)),
            )
            out += [(i, j) for i in sorted(ranked[:k])]
        return out
    raise ValueError(f"{d.id}: unknown rule '{rule}'")


def connect(d: Diagram, src: list, dst: list, rule: str, style: str, params: dict):
    if style not in LINKS:
        raise ValueError(f"{d.id}: unknown link style '{style}'")
    for i, j in pairs_for(d, rule, src, dst, params):
        add_line(d, src[i], dst[j], style, arrow=LINKS[style].get("arrow", False))


def add_line(d: Diagram, a: str, b: str, style: str, arrow=False, offset=0.0):
    """A straight line from cell to cell, aimed at the centres (or parallel to that line,
    `offset` to one side), stopping GAP short of each outline; an arrow's tip does the same."""
    A, B = d.cells[a], d.cells[b]
    dx, dy = B.x - A.x, B.y - A.y
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    nx, ny = -uy * offset, ux * offset
    s, e = extent(A, ux, uy) + GAP, extent(B, -ux, -uy) + GAP
    if offset and CELLS[A.kind].get("shape", "circle") == "circle":
        s = math.sqrt(max(s * s - offset * offset, 1.0))  # end exactly GAP outside the circle
    if offset and CELLS[B.kind].get("shape", "circle") == "circle":
        e = math.sqrt(max(e * e - offset * offset, 1.0))
    p0 = (A.x + ux * s + nx, A.y + uy * s + ny)
    p1 = (B.x - ux * e + nx, B.y - uy * e + ny)
    d.segs.append(
        Seg(style, [p0, p1], ends=(a, b), opacity=min(A.opacity, B.opacity), arrow=arrow)
    )


def chain(d: Diagram, keys: list, mode: str):
    ordered = sorted(keys, key=lambda k: d.cells[k].x)
    if mode == "left":
        ordered = ordered[::-1]
    for a, b in zip(ordered, ordered[1:]):
        if mode == "double":
            add_line(d, a, b, "state", arrow=True, offset=-2.3)
            add_line(d, a, b, "state", arrow=True, offset=2.3)
        else:
            add_line(d, a, b, "state", arrow=True)


def gates(d: Diagram, src: list, dst: list, mode=True):
    """Dotted line from each input to the state arrow arriving at its position. With mode
    "recurrent" (LSTM, GRU) the gate is also set by the previous state: a second dotted line
    leaves that cell at 45 degrees and comes down onto the dot from above, with BEND as its
    bend. A selective SSM's gate (mode true) reads the input alone."""
    ordered = sorted(dst, key=lambda k: d.cells[k].x)
    srcs = sorted(src, key=lambda k: d.cells[k].x)
    for idx in range(1, len(ordered)):
        A, B = d.cells[ordered[idx - 1]], d.cells[ordered[idx]]
        mx, my = (A.x + B.x) / 2, (A.y + B.y) / 2
        S = d.cells[srcs[min(idx, len(srcs) - 1)]]
        dx, dy = mx - S.x, my - S.y
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        s = extent(S, ux, uy) + GAP
        p0 = (S.x + ux * s, S.y + uy * s)
        p1 = (mx - ux * (DOT + GAP), my - uy * (DOT + GAP))
        d.segs.append(Seg("gate", [p0, p1], ends=(S.key,), opacity=S.opacity))
        d.dots.append((mx, my, 1.0))
        if mode == "recurrent":
            sx = 1 if B.x > A.x else -1
            s = extent(A, sx * DIAG, -DIAG) + GAP
            q0 = (A.x + sx * DIAG * s, A.y - DIAG * s)
            q2 = (mx, my - DOT - GAP)
            q1 = (mx, q0[1] - abs(mx - q0[0]))
            path, samples = rounded_path([q0, q1, q2], BEND)
            d.segs.append(Seg("gate", samples, d=path, ends=(A.key,), opacity=A.opacity))


def ref(d: Diagram, r) -> str:
    """A [group, index] reference to one cell's key, or a group id for a whole group."""
    if isinstance(r, str):
        if r in d.cells:
            return r
        if r not in d.groups:
            raise ValueError(f"{d.id}: unknown group '{r}'")
        return r
    key = f"{r[0]}.{r[1]}"
    if key not in d.cells:
        raise ValueError(f"{d.id}: unknown cell {key}")
    return key


def quad_points(p0, c, p1, n=8):
    return [
        (
            (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0],
            (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1],
        )
        for t in (k / n for k in range(n + 1))
    ]


def rounded_path(pts: list, r: float):
    """SVG path through the corner points, each corner rounded by a circular arc of
    radius r, plus a sampled polyline of the same path for lint."""
    d, samples = [f"M{fmt(pts[0][0])},{fmt(pts[0][1])}"], [pts[0]]
    for i in range(1, len(pts) - 1):
        p, q, n = pts[i - 1], pts[i], pts[i + 1]
        l1, l2 = math.hypot(q[0] - p[0], q[1] - p[1]), math.hypot(n[0] - q[0], n[1] - q[1])
        v1 = ((q[0] - p[0]) / l1, (q[1] - p[1]) / l1)
        v2 = ((n[0] - q[0]) / l2, (n[1] - q[1]) / l2)
        turn = math.acos(max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1])))
        if turn < 1e-6:
            continue
        t = min(r * math.tan(turn / 2), l1 / 2, l2 / 2)
        rr = t / math.tan(turn / 2)
        a = (q[0] - v1[0] * t, q[1] - v1[1] * t)
        b = (q[0] + v2[0] * t, q[1] + v2[1] * t)
        sweep = 1 if v1[0] * v2[1] - v1[1] * v2[0] > 0 else 0
        d.append(
            f"L{fmt(a[0])},{fmt(a[1])} A{fmt(rr)},{fmt(rr)} 0 0 {sweep} {fmt(b[0])},{fmt(b[1])}"
        )
        samples += quad_points(a, q, b)
    d.append(f"L{fmt(pts[-1][0])},{fmt(pts[-1][1])}")
    samples.append(pts[-1])
    return " ".join(d), samples


def elbow(d: Diagram, spec: dict, style: str, arrow: bool, default_side: str):
    """A connector beside the layers (skip connection or return arrow): it leaves one cell at
    45 degrees, runs straight up or down at REACH from the cells, and meets the other cell at
    45 degrees, with every bend rounded to BEND; both ends stop GAP from the outline."""
    ka, kb = ref(d, spec["from"]), ref(d, spec["to"])
    A, B = d.cells[ka], d.cells[kb]
    sgn = 1 if spec.get("side", default_side) == "right" else -1
    v = 1 if B.y > A.y else -1
    ua, ub = (sgn * DIAG, v * DIAG), (sgn * DIAG, -v * DIAG)
    sa, sb = extent(A, *ua) + GAP, extent(B, *ub) + GAP
    p0 = (A.x + ua[0] * sa, A.y + ua[1] * sa)
    p3 = (B.x + ub[0] * sb, B.y + ub[1] * sb)
    reach = spec.get("reach", REACH)
    while True:  # pull the run in until the two 45-degree legs fit in the height available
        x_run = (max(A.x, B.x) if sgn > 0 else min(A.x, B.x)) + sgn * reach
        c1 = (x_run, p0[1] + v * abs(x_run - p0[0]))
        c2 = (x_run, p3[1] - v * abs(x_run - p3[0]))
        if (c2[1] - c1[1]) * v >= 2 * BEND or reach <= 8.0:
            break
        reach -= 0.5
    path, samples = rounded_path([p0, c1, c2, p3], BEND)
    d.segs.append(
        Seg(style, samples, d=path, ends=(ka, kb), opacity=min(A.opacity, B.opacity), arrow=arrow)
    )


def arc(d: Diagram, a: dict):
    """Skip connection: an elbow beside the layers, with no arrowhead. Between two whole
    rows of the same size it is drawn once per column, cell i to cell i, since the skip
    carries the whole layer across (a ResNet adds it unit for unit, a U-Net copies it);
    like every elbow they run at REACH, so all skips and return arrows share one shape."""
    fa, fb = a["from"], a["to"]
    if isinstance(fa, str) and isinstance(fb, str):
        ga, gb = d.groups[ref(d, fa)], d.groups[ref(d, fb)]
        if len(ga) != len(gb):
            raise ValueError(
                f"{d.id}: a skip between rows {fa} and {fb} needs rows of the same size"
            )
        for ka, kb in zip(ga, gb):
            elbow(d, dict(a, **{"from": ka, "to": kb}), "skip", False, "left")
        return
    elbow(d, a, "skip", False, "left")


def loop(d: Diagram, lp: dict):
    """Return arrow, from a later cell back to an earlier one: an elbow with an arrowhead,
    in its own style (the output is fed back in and the network runs again)."""
    elbow(d, lp, "loop", True, "right")


def anchor(d: Diagram, key: str, toward) -> tuple:
    """Where a line toward `toward` leaves a cell or a whole group: the centre, the end point
    GAP outside the outline (a group's outline is its bounding box), and the cells involved."""
    if key in d.cells:
        c = d.cells[key]
        dx, dy = toward[0] - c.x, toward[1] - c.y
        L = math.hypot(dx, dy)
        ux, uy = dx / L, dy / L
        s = extent(c, ux, uy) + GAP
        return (c.x, c.y), (c.x + ux * s, c.y + uy * s), (key,), ()
    cells = [d.cells[k] for k in d.groups[key]]
    x0 = min(c.x - extent(c, -1.0, 0.0) for c in cells)
    x1 = max(c.x + extent(c, 1.0, 0.0) for c in cells)
    y0 = min(c.y - extent(c, 0.0, -1.0) for c in cells)
    y1 = max(c.y + extent(c, 0.0, 1.0) for c in cells)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    s = min((x1 - x0) / 2 / max(abs(ux), 1e-9), (y1 - y0) / 2 / max(abs(uy), 1e-9)) + GAP
    return (cx, cy), (cx + ux * s, cy + uy * s), (), tuple(d.groups[key])


def center_of(d: Diagram, key: str) -> tuple:
    if key in d.cells:
        return (d.cells[key].x, d.cells[key].y)
    return anchor(d, key, (0.0, -1e6))[0]


def arrow(d: Diagram, ar: dict):
    """Straight arrow between two cells, or from a whole group (its bounding box) to a cell:
    data handed from one part of the model to another, in the state arrow's style."""
    a, b = ref(d, ar["from"]), ref(d, ar["to"])
    ca, cb = center_of(d, a), center_of(d, b)
    _, p0, ends_a, near_a = anchor(d, a, cb)
    _, p1, ends_b, near_b = anchor(d, b, ca)
    d.segs.append(
        Seg(
            ar.get("style", "state"),
            [p0, p1],
            ends=ends_a + ends_b,
            near=near_a + near_b,
            arrow=ar.get("head", True),
        )
    )


# --------------------------------------------------------------------------
# Lint
# --------------------------------------------------------------------------


def point_seg_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def lint_diagram(d: Diagram) -> list:
    warns = []
    cells = list(d.cells.values())
    for i, c in enumerate(cells):
        r = radius(c)
        if c.x - r < 2 or c.x + r > W - 2 or c.y - r < 2 or c.y + r > H - 2:
            warns.append(f"{d.id}: cell {c.key} sits outside the canvas")
        for c2 in cells[i + 1 :]:
            if math.hypot(c.x - c2.x, c.y - c2.y) < r + radius(c2) + 1:
                warns.append(f"{d.id}: cells {c.key} and {c2.key} overlap")
    for s in d.segs:
        for c in cells:
            if c.key in s.ends or c.key in s.near:
                continue
            dist = min(point_seg_dist((c.x, c.y), p, q) for p, q in zip(s.pts, s.pts[1:]))
            gap = dist - radius(c)
            if gap < CLEARANCE:
                where = " to ".join(s.ends) or "point"
                warns.append(f"{d.id}: {s.style} line ({where}) passes {gap:.1f}px from {c.key}")
    for s in d.segs:  # every end (and every arrow tip) stops exactly GAP from its outline
        for k in s.ends:
            c = d.cells[k]
            e = min((s.pts[0], s.pts[-1]), key=lambda p: math.hypot(p[0] - c.x, p[1] - c.y))
            L = math.hypot(e[0] - c.x, e[1] - c.y)
            want = extent(c, (e[0] - c.x) / L, (e[1] - c.y) / L) + GAP
            if abs(L - want) > 0.05:
                warns.append(
                    f"{d.id}: a {s.style} line ends {L - want + GAP:.2f}px from {k} (expected {GAP})"
                )
    for s in d.segs:
        for x, y in s.pts:
            if x < 0 or x > W or y < 0 or y > H:
                warns.append(f"{d.id}: {s.style} line leaves the canvas")
                break
    for c in cells:
        for p, q in mark_lines(c):
            if (
                min(p[0], q[0]) < 1
                or max(p[0], q[0]) > W - 1
                or min(p[1], q[1]) < 1
                or max(p[1], q[1]) > H - 1
            ):
                warns.append(f"{d.id}: the mark on {c.key} leaves the canvas")
            for s in d.segs:
                if min(seg_seg_dist(p, q, a, b) for a, b in zip(s.pts, s.pts[1:])) < 1.2:
                    warns.append(f"{d.id}: the mark on {c.key} touches a {s.style} line")
                    break
            for c2 in cells:
                if c2 is not c and point_seg_dist((c2.x, c2.y), p, q) - radius(c2) < 1.5:
                    warns.append(f"{d.id}: the mark on {c.key} runs into {c2.key}")
    return warns


def seg_seg_dist(p, q, a, b) -> float:
    def side(o, s, t):
        return (t[0] - o[0]) * (s[1] - o[1]) - (t[1] - o[1]) * (s[0] - o[0])

    if side(a, b, p) * side(a, b, q) < 0 and side(p, q, a) * side(p, q, b) < 0:
        return 0.0
    return min(
        point_seg_dist(p, a, b),
        point_seg_dist(q, a, b),
        point_seg_dist(a, p, q),
        point_seg_dist(b, p, q),
    )


_FONT_FILES = {}


def text_width(text: str, role: str, size: float, weight: int = 400) -> float:
    """Measure with the real font when Pillow and the font are available."""
    style = {300: "Light", 400: "Regular", 500: "Medium", 700: "Bold"}[weight]
    key = (role, style)
    if key not in _FONT_FILES:
        path = None
        try:
            out = subprocess.run(
                ["fc-match", "-f", "%{file}", f"{FONTS[role][0]}:style={style}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if FONTS[role][0].split()[0].lower() in out.stdout.lower():
                path = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        _FONT_FILES[key] = path
    path = _FONT_FILES[key]
    if path:
        try:
            from PIL import ImageFont

            return ImageFont.truetype(path, size=int(size * 8)).getlength(text) / 8
        except Exception:
            pass
    return len(text) * size * (0.6 if role == "mono" else 0.52)


MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def updated_date(doc: dict):
    """The day the chart last changed. The build sets it (see chart_date); an `updated` in
    architectures.yaml overrides that, and without either it is today."""
    u = doc.get("updated")
    if u is None:
        return datetime.date.today()
    if isinstance(u, datetime.date):
        return u
    try:
        return datetime.date.fromisoformat(str(u))
    except ValueError:
        return None


def date_text(u) -> str:
    return f"{u.day} {MONTHS[u.month - 1]} {u.year}"


def updated_text(doc: dict) -> str:
    return "Last updated " + date_text(updated_date(doc))


def notice(doc: dict) -> str:
    """The copyright line; while the chart is a draft, `status` goes first."""
    status = str(doc.get("status") or "").strip()
    return f"{status} {doc['copyright']}" if status else doc["copyright"]


def license_of(doc: dict):
    """The chart's license, {text, url}, printed after the copyright; None when there is none."""
    lic = doc.get("license")
    return lic if isinstance(lic, dict) and lic.get("text") else None


def notice_text(doc: dict) -> str:
    """The copyright line with the license after it."""
    lic = license_of(doc)
    return notice(doc) + (f" · {lic['text']}" if lic else "")


MARKERS = "¹²³⁴⁵⁶⁷⁸⁹"  # footnote markers, in poster order


def footnotes(doc: dict) -> "OrderedDict[str, tuple]":
    """The architectures that carry a `note`, in poster order: id -> (marker, text). The note
    is printed as a footnote on the poster and under the caption on the page."""
    out = OrderedDict()
    for s in doc["sections"]:
        for pid in s["panels"]:
            a = doc["architectures"].get(pid) or {}
            if a.get("note"):
                out[pid] = (
                    MARKERS[min(len(out), len(MARKERS) - 1)],
                    " ".join(str(a["note"]).split()),
                )
    return out


def caption_text(notes, pid: str, a: dict) -> str:
    """The caption, with its footnote marker after it when the architecture has a note."""
    return str(a["caption"]) + (notes[pid][0] if pid in notes else "")


def lint_doc(doc: dict, diagrams: dict) -> list:
    warns = []
    arch = doc["architectures"]
    u = updated_date(doc)
    if u is None:
        warns.append("'updated', when set, must be a date such as 2026-10-15")
    elif u > datetime.date.today():
        warns.append(f"'updated' ({u}) is in the future")
    lic = doc.get("license")
    if lic is not None and not (
        isinstance(lic, dict)
        and lic.get("text")
        and str(lic.get("url", "")).startswith("https://")
    ):
        warns.append("'license' needs a text and an https url")
    site = doc.get("site")
    if site is not None and not (
        isinstance(site, dict)
        and str(site.get("url", "")).startswith("https://")
        and str(site["url"]).endswith("/")
    ):
        warns.append("'site' needs an https url ending in /")
    repo = doc.get("repo")
    if repo is not None and not (
        isinstance(repo, dict)
        and repo.get("text")
        and str(repo.get("url", "")).startswith("https://")
    ):
        warns.append("'repo' needs a text and an https url")
    text_col = POSTER_TEXT * POSTER_COLS * W + (POSTER_COLS - 1) * POSTER_GX
    for key, role, size, weight in (
        ("title", "display", TYPE["title"][0], 500),
        ("subtitle", "body", TYPE["subtitle"][0], 400),
    ):
        if text_width(str(doc.get(key, "")), role, size, weight) > text_col:
            warns.append(f"poster: the {key} is too wide for one line")
    placed = [p for s in doc["sections"] for p in s["panels"]]
    for p in placed:
        if p not in arch:
            warns.append(f"section lists unknown architecture '{p}'")
    for aid in arch:
        if placed.count(aid) != 1:
            warns.append(f"{aid}: appears {placed.count(aid)} times in sections (expected once)")
    notes = footnotes(doc)
    if len(notes) > len(MARKERS):
        warns.append(f"{len(notes)} footnotes, but there are only {len(MARKERS)} markers")
    for aid, a in arch.items():
        for key in ("name", "year", "caption", "objective", "summary", "papers"):
            if not a.get(key):
                warns.append(f"{aid}: missing '{key}'")
        if a.get("objective") not in OBJECTIVES:
            warns.append(f"{aid}: objective '{a.get('objective')}' is not one of {OBJECTIVES}")
        if (
            text_width(
                caption_text(notes, aid, a) if a.get("caption") else "", "body", TYPE["caption"][0]
            )
            > W - 6
        ):
            warns.append(f"{aid}: caption is too wide for the panel")
        if text_width(a.get("name", ""), "display", TYPE["name"][0]) > W - 6:
            warns.append(f"{aid}: name is too wide for the panel")
        for p in a.get("papers", []):
            if not str(p.get("url", "")).startswith("https://"):
                warns.append(f"{aid}: paper link must be an https URL")
            if set(p) - {"source"} != {
                "cite",
                "title",
                "url",
            }:  # an unquoted comma splits a {...} entry
                warns.append(
                    f"{aid}: paper '{p.get('cite')}' has keys {sorted(map(str, p))}; "
                    f"quote a cite or title that contains a comma"
                )
            if not re.search(r"\d{4}$", str(p.get("cite", ""))):
                warns.append(f"{aid}: cite '{p.get('cite')}' should end with the year")
    for k, c in CELLS.items():
        if LEGEND_CELL_X + text_width(c["label"], "body", TYPE["legend"][0]) > W - 2:
            warns.append(f"legend: '{c['label']}' is too wide for its column")
    for k, l in LINKS.items():
        if LEGEND_LINK_X + text_width(l["label"], "body", TYPE["legend"][0]) > W - 2:
            warns.append(f"legend: '{l['label']}' is too wide for its column")
    for d in diagrams.values():
        warns += lint_diagram(d)
    if (
        not fonts_installed()
    ):  # without the real fonts a width is only a guess, so don't fail on it
        skipped = [w for w in warns if "too wide" in w]
        if skipped:
            print(
                f"lint: {len(skipped)} width check(s) skipped: Poppins, Lato, or Noto Sans Mono "
                f"isn't installed, so text widths can't be measured",
                file=sys.stderr,
            )
        warns = [w for w in warns if "too wide" not in w]
    return warns


def fonts_installed() -> bool:
    """Whether text_width can measure every face lint uses with the real font."""
    for role, weight in (("display", 400), ("display", 500), ("body", 400), ("mono", 400)):
        text_width("x", role, 10, weight)
        if not _FONT_FILES[
            (role, {300: "Light", 400: "Regular", 500: "Medium", 700: "Bold"}[weight])
        ]:
            return False
    return True


# --------------------------------------------------------------------------
# SVG rendering
# --------------------------------------------------------------------------


def marker_def(mid: str, ink: str) -> str:
    """The one arrowhead: sized in user units, so it is the same on every arrow, and stroked
    at LINE like the lines; its tip sits exactly on the end of the line."""
    k = HEAD / 10
    return (
        f'<marker id="{mid}" viewBox="0 0 10 10" refX="8" refY="5" markerUnits="userSpaceOnUse" '
        f'markerWidth="{fmt(HEAD)}" markerHeight="{fmt(HEAD)}" orient="auto-start-reverse">'
        f'<path d="M2 1L8 5L2 9" fill="none" stroke="{ink}" stroke-width="{LINE / k:.3f}" '
        f'stroke-linecap="round" stroke-linejoin="round"/></marker>'
    )


def squiggle(p0, p1, amp=1.7, wavelength=12.0) -> str:
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    nx, ny = -dy / L, dx / L
    waves = max(2, round(L / wavelength))
    steps = waves * 8
    pts = []
    for k in range(steps + 1):
        t = k / steps
        off = amp * math.sin(math.pi * t) * math.sin(2 * math.pi * waves * t)
        pts.append(f"{fmt(x0 + dx * t + nx * off)},{fmt(y0 + dy * t + ny * off)}")
    return "M" + " L".join(pts)


def glyph_svg(glyph: str, x: float, y: float) -> str:
    if glyph == "ring":
        return f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="3.3"/>'
    if glyph == "triangle":
        return f'<polygon points="{fmt(x)},{fmt(y - 3.9)} {fmt(x + 3.7)},{fmt(y + 2.5)} {fmt(x - 3.7)},{fmt(y + 2.5)}"/>'
    if glyph == "square":
        return f'<rect x="{fmt(x - 3)}" y="{fmt(y - 3)}" width="6" height="6"/>'
    if glyph == "diamond":
        return f'<polygon points="{fmt(x)},{fmt(y - 3.7)} {fmt(x + 3.7)},{fmt(y)} {fmt(x)},{fmt(y + 3.7)} {fmt(x - 3.7)},{fmt(y)}"/>'
    if glyph == "layers":  # three short bars, a stack of layers: a network of its own
        return "".join(
            f'<path d="M{fmt(x - 3.6)},{fmt(y + dy)} L{fmt(x + 3.6)},{fmt(y + dy)}"/>'
            for dy in (-2.8, 0, 2.8)
        )
    if glyph == "arrow":  # a short arrow, up and to the right: a direction and a speed
        return (
            f'<path d="M{fmt(x - 3.4)},{fmt(y + 3.0)} L{fmt(x + 3.0)},{fmt(y - 3.4)}"/>'
            f'<path d="M{fmt(x - 0.6)},{fmt(y - 3.6)} L{fmt(x + 3.2)},{fmt(y - 3.6)} L{fmt(x + 3.2)},{fmt(y + 0.2)}"/>'
        )
    if glyph == "wave":  # one period of a sine wave across the cell: a random signal
        half, amp, n = 3.9, 2.1, 16
        pts = [
            f"{fmt(x - half + 2 * half * k / n)},{fmt(y - amp * math.sin(2 * math.pi * k / n))}"
            for k in range(n + 1)
        ]
        return f'<path d="M{" L".join(pts)}"/>'
    raise ValueError(glyph)


def mark_svg(cell: Cell, ink: str) -> str:
    """The group mark: an outer ring for state, stems or side ticks for the rest."""
    mark = group_mark(cell)
    if mark == "ring":
        spec = CELLS[cell.kind]
        if spec.get("shape") == "square":
            h = half_of(spec, 7.0) + RING_PAD
            return (
                f'<rect x="{fmt(cell.x - h)}" y="{fmt(cell.y - h)}" width="{fmt(2 * h)}" height="{fmt(2 * h)}" '
                f'rx="4" fill="none" stroke="{ink}" stroke-width="0.8"/>'
            )
        return f'<circle cx="{fmt(cell.x)}" cy="{fmt(cell.y)}" r="{fmt(R + RING_PAD)}" fill="none" stroke="{ink}" stroke-width="0.8"/>'
    lines = "".join(
        f'<line x1="{fmt(p[0])}" y1="{fmt(p[1])}" x2="{fmt(q[0])}" y2="{fmt(q[1])}"/>'
        for p, q in mark_lines(cell)
    )
    return (
        f'<g stroke="{ink}" stroke-width="1.1" stroke-linecap="round">{lines}</g>' if lines else ""
    )


def cell_svg(
    kind: str,
    x: float,
    y: float,
    opacity: float = 1.0,
    ink: str = "currentColor",
    mark_dir=None,
    marks: bool = True,
) -> str:
    spec = CELLS[kind]
    op = "" if opacity == 1 else f' opacity="{fmt(opacity) if opacity >= 0.1 else opacity}"'
    fill, stroke = PALETTE[GROUPS[spec["group"]]["color"]]
    shape = spec.get("shape", "circle")
    mark = mark_svg(Cell("", kind, x, y, marks=marks, mark_dir=mark_dir), ink)
    if spec.get("hollow"):
        body = (
            f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(R)}" fill="none" stroke="{stroke}" '
            f'stroke-width="1.1" stroke-dasharray="2.2 1.7"/>{mark}'
        )
        return f"<g{op}>{body}</g>" if op else body
    if shape == "diamond":
        h = half_of(spec, 5.0)
        pts = f"{fmt(x)},{fmt(y - h)} {fmt(x + h)},{fmt(y)} {fmt(x)},{fmt(y + h)} {fmt(x - h)},{fmt(y)}"
        body = (
            f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="0.8" '
            f'stroke-linejoin="round"/>'
        )
    elif shape == "square":
        h = half_of(spec, 7.0)
        body = (
            f'<rect x="{fmt(x - h)}" y="{fmt(y - h)}" width="{fmt(2 * h)}" height="{fmt(2 * h)}" '
            f'rx="2.5" fill="{fill}" stroke="{stroke}" stroke-width="0.8"/>'
        )
    else:
        body = (
            f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(R)}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="0.8"/>'
        )
    if spec.get("glyph"):
        k = R / R0  # inner marks are drawn for R0 and scaled about the centre
        tf = (
            ""
            if k == 1
            else f' transform="translate({fmt(x)} {fmt(y)}) scale({fmt(k)}) translate({fmt(-x)} {fmt(-y)})"'
        )
        gink = GLYPH_INK_ON.get(GROUPS[spec["group"]]["color"], GLYPH_INK)
        body += (
            f'<g fill="none" stroke="{gink}" stroke-width="{fmt(GLYPH_STROKE / k)}" stroke-linejoin="round"{tf}>'
            f'{glyph_svg(spec["glyph"], x, y)}</g>'
        )
    body += mark
    return f"<g{op}>{body}</g>" if op else body


STYLE_ORDER = ["frozen", "link", "skip", "func", "cross", "gate", "state", "loop"]


def render_diagram(d: Diagram, marker_id: str, ink: str = "currentColor") -> str:
    out = []
    for style in STYLE_ORDER:
        segs = [s for s in d.segs if s.style == style]
        if not segs:
            continue
        st = LINKS[style]
        attrs = f'fill="none" stroke="{ink}" stroke-width="{LINE}" stroke-linecap="round" stroke-linejoin="round"'
        if "dash" in st:
            attrs += f' stroke-dasharray="{st["dash"]}"'
        if "opacity" in st:
            attrs += f' stroke-opacity="{st["opacity"]}"'
        items = []
        for s in segs:
            op = (
                ""
                if s.opacity == 1
                else f' opacity="{fmt(s.opacity) if s.opacity >= 0.1 else s.opacity}"'
            )
            mk = f' marker-end="url(#{marker_id})"' if s.arrow else ""
            if style == "func":
                items.append(f'<path d="{squiggle(s.pts[0], s.pts[-1])}"{op}/>')
            elif s.d:
                items.append(f'<path d="{s.d}"{op}{mk}/>')
            else:
                (x0, y0), (x1, y1) = s.pts[0], s.pts[-1]
                items.append(
                    f'<line x1="{fmt(x0)}" y1="{fmt(y0)}" x2="{fmt(x1)}" y2="{fmt(y1)}"{op}{mk}/>'
                )
        out.append(f'<g {attrs}>{"".join(items)}</g>')
    if d.dots:
        dots = "".join(
            f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(DOT)}"/>' for x, y, _ in d.dots
        )
        out.append(f'<g fill="{ink}">{dots}</g>')
    for kind in CELLS:
        for c in d.cells.values():
            if c.kind == kind:
                out.append(cell_svg(kind, c.x, c.y, c.opacity, ink, c.mark_dir, c.marks))
    return "".join(out)


def panel_svg(d: Diagram, title: str, ink: str = INK_LIGHT, bg: str | None = None) -> str:
    mid = f"arr-{d.id}"
    bgrect = f'<rect width="{fmt(W)}" height="{fmt(H)}" fill="{bg}"/>' if bg else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {fmt(W)} {fmt(H)}" '
        f'width="{fmt(W * 2)}" height="{fmt(H * 2)}" role="img"><title>{html.escape(title)}</title>'
        f"<defs>{marker_def(mid, ink)}</defs>{bgrect}{render_diagram(d, mid, ink)}</svg>"
    )


def legend_links(styles) -> list:
    """The link styles a legend or key lists for these styles, in legend order: a style that
    shares another's entry (legend_as) is listed as that one."""
    shown = {LINKS[k].get("legend_as", k) for k in styles}
    return [k for k in LINKS if k in shown]


def used_kinds(diagrams: dict):
    cells = {c.kind for d in diagrams.values() for c in d.cells.values()}
    links = {s.style for d in diagrams.values() for s in d.segs}
    return [k for k in CELLS if k in cells], legend_links(links)


def link_sample(
    style: str, x: float, y: float, ink: str, marker_id: str, length: float = 22.0
) -> str:
    """A legend sample, drawn exactly as the style is drawn in the panels."""
    st = LINKS[style]
    attrs = f'fill="none" stroke="{ink}" stroke-width="{LINE}" stroke-linecap="round" stroke-linejoin="round"'
    if "dash" in st:
        attrs += f' stroke-dasharray="{st["dash"]}"'
    if "opacity" in st:
        attrs += f' stroke-opacity="{st["opacity"]}"'
    mk = f' marker-end="url(#{marker_id})"' if st.get("arrow") else ""
    if style == "func":
        return f'<path d="{squiggle((x, y), (x + length, y))}" {attrs}/>'
    if style in (
        "skip",
        "loop",
    ):  # the panels' elbow, turned on its side: 45-degree legs, rounded bends
        leg = 5.0
        path, _ = rounded_path(
            [
                (x, y + 4),
                (x + leg, y + 4 - leg),
                (x + length - leg, y + 4 - leg),
                (x + length, y + 4),
            ],
            BEND,
        )
        return f'<path d="{path}" {attrs}{mk}/>'
    if (
        style == "gate"
    ):  # as in the panels: a dotted line from the input below meets a dot on a state arrow
        mx, (gx, gy) = x + length / 2, (x + length / 2 + 4.5, y + 9.0)
        L = math.hypot(mx - gx, y - gy)
        ux, uy = (mx - gx) / L, (y - gy) / L
        end = (mx - ux * (DOT + GAP), y - uy * (DOT + GAP))
        return (
            link_sample("state", x, y, ink, marker_id, length)
            + f'<circle cx="{fmt(mx)}" cy="{fmt(y)}" r="{fmt(DOT)}" fill="{ink}"/>'
            + f'<line x1="{fmt(gx)}" y1="{fmt(gy)}" x2="{fmt(end[0])}" y2="{fmt(end[1])}" {attrs}/>'
        )
    return f'<line x1="{fmt(x)}" y1="{fmt(y)}" x2="{fmt(x + length)}" y2="{fmt(y)}" {attrs}{mk}/>'


# --------------------------------------------------------------------------
# Poster
# --------------------------------------------------------------------------

POSTER_M, POSTER_GX, POSTER_COLS = 64.0, 24.0, 6


def title_h() -> float:
    """Height of a panel's name above its drawing."""
    return TYPE["name"][0] + 7.0


def caption_dy() -> tuple:
    """Baselines of a panel's caption and its year · objective line, below the drawing."""
    cap = TYPE["caption"][0] + 3.5
    return cap, cap + TYPE["meta"][0] + 5.5


def block_h() -> float:
    """A panel's whole height on the poster: name, drawing, caption, and year line."""
    return title_h() + H + caption_dy()[1] + 6.0


def svg_text(x, y, s, role, size, ink, weight=400, opacity=1.0, anchor="start", spacing=0.0):
    fam, fallback = FONTS[role]
    attrs = (
        f'x="{fmt(x)}" y="{fmt(y)}" font-family="{fam}, {fallback}" font-size="{size}" '
        f'font-weight="{weight}" fill="{ink}"'
    )
    if opacity != 1:
        attrs += f' fill-opacity="{opacity}"'
    if anchor != "start":
        attrs += f' text-anchor="{anchor}"'
    if spacing:
        attrs += f' letter-spacing="{spacing}"'
    return f"<text {attrs}>{html.escape(str(s))}</text>"


def type_text(x, y, s, role, font, ink, weight=400, **kw):
    """Text in one of the poster's TYPE roles: its size and its ink's opacity."""
    size, opacity = TYPE[role]
    return svg_text(x, y, s, font, size, ink, weight, opacity, **kw)


def pack_rows(sections: list) -> list:
    """Sections fill rows in order: a section joins the row before it when the two add up to
    POSTER_COLS or fewer (three and three, two and four), and otherwise starts a new row."""
    rows, used = [], POSTER_COLS
    for s in sections:
        n = len(s["panels"])
        if used + n <= POSTER_COLS:
            rows[-1].append((s, used))
            used += n
        else:
            rows.append([(s, 0)])
            used = n
    return rows


def legend_groups(cells: list, links: list) -> list:
    """Legend columns: one per color group (in GROUPS order), then the link styles."""
    cols = []
    for gid, g in GROUPS.items():
        items = [("cell", k) for k in cells if CELLS[k]["group"] == gid]
        if items:
            cols.append((g["label"], items))
    if links:
        cols.append((LINK_GROUP, [("link", k) for k in links]))
    return cols


LEGEND_CELL_X, LEGEND_LINK_X, LEGEND_ROW = 23.0, 31.0, 27.0


def legend_columns(cells: list, links: list, x0: float, ink: str, mid: str):
    """Poster legend: each group sits in its own column, aligned with the panel columns."""
    out, height = [], 0.0
    for ci, (label, items) in enumerate(legend_groups(cells, links)):
        x = x0 + (ci % POSTER_COLS) * (W + POSTER_GX)
        top = (ci // POSTER_COLS) * 230.0
        out.append(
            svg_text(
                x,
                top + 14,
                label,
                "display",
                TYPE["legend_head"][0],
                ink,
                500,
                TYPE["legend_head"][1],
            )
        )
        y = top + 40  # each group's name, then its cells or links; the names carry the meaning
        for kind, it in items:
            if kind == "cell":
                out.append(cell_svg(it, x + 8, y, ink=ink))
                tx, text = x + LEGEND_CELL_X, CELLS[it]["label"]
            else:
                out.append(link_sample(it, x, y, ink, mid))
                tx, text = x + LEGEND_LINK_X, LINKS[it]["label"]
            out.append(type_text(tx, y + 4.5, text, "legend", "body", ink))
            y += LEGEND_ROW
        height = max(height, y - 10)
    return "".join(out), height


READING_LH = 22.0  # line height of the poster's reading text
POSTER_TEXT = 0.94  # wrap at this share of the text column (cairo sets regular text a little wide)


def wrap_text(text: str, role: str, size: float, width: float, weight: int = 400) -> list:
    """Balanced lines no wider than `width` (like CSS text-wrap: balance): as few lines as a
    plain wrap needs, with the breaks chosen so the lines come out as even as possible."""
    words = text.split()

    def greedy(limit):
        lines, cur = [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if cur and text_width(trial, role, size, weight) > limit:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        return lines + ([cur] if cur else [])

    n = len(greedy(width))
    lo, hi = 0.0, width  # the narrowest limit that still fits in n lines
    for _ in range(32):
        mid = (lo + hi) / 2
        if len(greedy(mid)) <= n:
            hi = mid
        else:
            lo = mid
    return greedy(hi)


def link_box(url: str, x: float, y: float, line: str, part: str, role: str) -> str:
    """A clear box over `part` of a line of poster text set at (x, y), linked to `url`. SVG
    viewers follow it as it is; Cairo writes no links, so add_pdf_links puts each box in the
    PDF as a link annotation."""
    size = TYPE[role][0]
    i = line.index(part)
    bx = x + text_width(line[:i], "body", size)
    u = html.escape(url)
    return (
        f'<a href="{u}" xlink:href="{u}"><rect class="link" x="{fmt(bx)}" y="{fmt(y - size)}" '
        f'width="{fmt(text_width(part, "body", size))}" height="{fmt(size * 1.3)}" '
        f'fill="#000000" fill-opacity="0" data-text="{html.escape(part)}"/></a>'
    )


def add_pdf_links(pdf_path: str, svg: str):
    """Turn the poster's link boxes into link annotations in its PDF; needs pypdf. Cairo sets
    the text a little wider than Pillow measures it, so with pypdfium2 each link is fitted to
    where its text actually landed in the PDF; without it, to the box in the SVG."""
    try:
        from pypdf import PdfWriter
        from pypdf.annotations import Link
    except ImportError:
        print("pypdf is not installed, so the PDF's links were skipped")
        return
    svg_w = float(re.search(r'viewBox="0 0 ([\d.]+)', svg).group(1))
    boxes = re.findall(
        r'<a href="([^"]+)"[^>]*><rect class="link" x="([\d.]+)" y="([\d.]+)" '
        r'width="([\d.]+)" height="([\d.]+)"[^>]*data-text="([^"]*)"',
        svg,
    )
    found = pdf_text_boxes(pdf_path, [html.unescape(t) for *_, t in boxes])
    writer = PdfWriter(clone_from=pdf_path)
    page = writer.pages[0]
    k, top = float(page.mediabox.width) / svg_w, float(page.mediabox.height)
    for url, x, y, w, h, text in boxes:
        x, y, w, h = (float(v) for v in (x, y, w, h))
        rect = (x * k, top - (y + h) * k, (x + w) * k, top - y * k)
        near = [
            b
            for b in found.get(html.unescape(text), [])
            if abs((b[1] + b[3]) / 2 - (rect[1] + rect[3]) / 2) < h * k
        ]
        if near:
            l, b_, r, t = min(near, key=lambda b: abs(b[0] - rect[0]))
            rect = (l - 1, b_ - 2, r + 1, t + 2)
        writer.add_annotation(0, Link(rect=rect, url=html.unescape(url), border=[0, 0, 0]))
    with open(pdf_path, "wb") as fh:
        writer.write(fh)


def pdf_text_boxes(pdf_path: str, texts: list) -> dict:
    """Where each text sits on the PDF's first page, as (left, bottom, right, top) boxes in PDF
    points, one per occurrence; empty without pypdfium2."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return {}
    pdf = pdfium.PdfDocument(pdf_path)
    tp = pdf[0].get_textpage()
    out = {}
    for text in texts:
        search = tp.search(text, match_case=True)
        while hit := search.get_next():
            start, count = hit
            chars = [tp.get_charbox(i) for i in range(start, start + count)]
            chars = [c for c in chars if c[2] > c[0]]
            if chars:
                out.setdefault(text, []).append(
                    (
                        min(c[0] for c in chars),
                        min(c[1] for c in chars),
                        max(c[2] for c in chars),
                        max(c[3] for c in chars),
                    )
                )
        search.close()
    tp.close()
    pdf.close()
    return out


def poster_svg(doc: dict, diagrams: dict, theme: str = "light") -> str:
    ink, bg = (INK_LIGHT, BG_LIGHT) if theme == "light" else (INK_DARK, BG_DARK)
    arch = doc["architectures"]
    M, GX, COLS = POSTER_M, POSTER_GX, POSTER_COLS
    width = 2 * M + COLS * W + (COLS - 1) * GX
    parts, y = [], M
    parts.append(type_text(M, y + 38, doc["title"], "title", "display", ink, 500))
    parts.append(type_text(M, y + 78, doc["subtitle"], "subtitle", "body", ink))
    reading = wrap_text(doc["reading"], "body", TYPE["reading"][0], POSTER_TEXT * (width - 2 * M))
    lh = READING_LH * TYPE["reading"][0] / 15
    for i, line in enumerate(reading):
        parts.append(type_text(M, y + 110 + i * lh, line, "reading", "body", ink))
    y += 150 + (len(reading) - 1) * lh
    cells, links = used_kinds(diagrams)
    leg, leg_h = legend_columns(cells, links, M, ink, "arr")
    parts.append(f'<g transform="translate(0,{fmt(y)})">{leg}</g>')
    y += leg_h + 40
    notes = footnotes(doc)
    for row in pack_rows(doc["sections"]):
        for section, col0 in row:
            n = len(section["panels"])
            x0 = M + col0 * (W + GX)
            x1 = x0 + n * W + (n - 1) * GX
            parts.append(
                type_text(
                    x0,
                    y + 12,
                    section["title"].upper(),
                    "section",
                    "display",
                    ink,
                    500,
                    spacing=1.4,
                )
            )
            parts.append(
                f'<line x1="{fmt(x0)}" y1="{fmt(y + 22)}" x2="{fmt(x1)}" y2="{fmt(y + 22)}" '
                f'stroke="{ink}" stroke-opacity="0.16" stroke-width="1"/>'
            )
            for k, pid in enumerate(section["panels"]):
                a, px, py = arch[pid], x0 + k * (W + GX), y + 34
                th, (dc, dm) = title_h(), caption_dy()
                parts.append(
                    type_text(
                        px + W / 2, py + th - 8, a["name"], "name", "display", ink, anchor="middle"
                    )
                )
                parts.append(
                    f'<g transform="translate({fmt(px)},{fmt(py + th)})">'
                    f'{render_diagram(diagrams[pid], "arr", ink)}</g>'
                )
                parts.append(
                    type_text(
                        px + W / 2,
                        py + th + H + dc,
                        caption_text(notes, pid, a),
                        "caption",
                        "body",
                        ink,
                        anchor="middle",
                    )
                )
                parts.append(
                    type_text(
                        px + W / 2,
                        py + th + H + dm,
                        f'{a["year"]} · {a["objective"]}',
                        "meta",
                        "mono",
                        ink,
                        anchor="middle",
                    )
                )
        y += 34 + block_h() + 30
    parts.append(
        f'<line x1="{fmt(M)}" y1="{fmt(y)}" x2="{fmt(width - M)}" y2="{fmt(y)}" '
        f'stroke="{ink}" stroke-opacity="0.16" stroke-width="1"/>'
    )
    y += 26
    for marker, text in notes.values():  # footnotes first, then the credit line closes the poster
        for line in wrap_text(
            f"{marker} {text}", "body", TYPE["credit"][0], POSTER_TEXT * (width - 2 * M)
        ):
            parts.append(type_text(M, y, line, "credit", "body", ink))
            y += 18
    if notes:
        y += 4
    link = doc.get("credit_link")
    address = link["url"].replace("https://", "").rstrip("/") if link else ""
    credit = doc["credit"] + (" " + address if link else "")
    parts.append(type_text(M, y, credit, "credit", "body", ink))
    if link:  # the address links to the Zoo
        parts.append(link_box(link["url"], M, y, credit, address, "credit"))
    y += TYPE["credit"][0] + 6  # the notice sits under the credit, left-aligned with it
    repo, lic = doc.get("repo"), license_of(doc)
    lead = notice_text(doc) + " · "  # © and license, then the repository, then the date
    line = lead + (f'{repo["text"]} · ' if repo else "") + updated_text(doc)
    parts.append(type_text(M, y, line, "notice", "body", ink))
    if lic:
        parts.append(link_box(lic["url"], M, y, line, lic["text"], "notice"))
    if repo:
        parts.append(link_box(repo["url"], M, y, line, repo["text"], "notice"))
    height = y + M - 12
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {fmt(width)} {fmt(height)}" '
        f'width="{fmt(width)}" height="{fmt(height)}" role="img">'
        f'<title>{html.escape(doc["title"])}</title><defs>{marker_def("arr", ink)}</defs>'
        f'<rect width="100%" height="100%" fill="{bg}"/>{"".join(parts)}</svg>'
    )


# --------------------------------------------------------------------------
# Interactive web page
# --------------------------------------------------------------------------

FONT_LINK = (
    "https://fonts.googleapis.com/css2?family=Lato:wght@400;700&family=Noto+Sans+Mono:wght@400;500"
    "&family=Poppins:wght@400;500&display=swap"
)

# The theme button's two icons, drawn here: a crescent moon and a sun with eight rays.
# The source link beside the theme button: a pair of angle brackets, the plain sign for code.
CODE_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 6v-3.9'
    "a3.4 3.4 0 0 0-.9-2.7c3-.3 6.2-1.5 6.2-6.7"
    "a5.2 5.2 0 0 0-1.4-3.6 4.8 4.8 0 0 0-.1-3.6"
    "s-1.1-.3-3.6 1.4a12.3 12.3 0 0 0-6.5 0"
    "C6.2 1.2 5.1 1.5 5.1 1.5A4.8 4.8 0 0 0 5 5.1"
    "a5.2 5.2 0 0 0-1.4 3.6c0 5.2 3.2 6.4 6.2 6.7"
    'a3.4 3.4 0 0 0-.9 2.7V22"/></svg>'
)

THEME_ICONS = (
    '<svg class="i-moon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6z"/></svg>'
    '<svg class="i-sun" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2'
    'M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/></svg>'
)

# The page's order menu; the first is the poster's order and the default.
ORDER_OPTIONS = "".join(
    f'<option value="{v}">{t}</option>'
    for v, t in (
        ("family", "By family"),
        ("year", "By year, oldest first"),
        ("year-desc", "By year, newest first"),
        ("name", "By name, A to Z"),
        ("objective", "By objective"),
    )
)

# Published next to the web page; its Save buttons hand these files to the viewer.
POSTER = "neural-network-field-guide"  # the posters' file name in dist/, before .svg, .pdf, .png
POSTER_FILES = [
    (f"{POSTER}.pdf", "PDF"),
    (f"{POSTER}.png", "PNG"),
    (f"{POSTER}-dark.png", "PNG, dark"),
]

PAGE_CSS = r"""
/* Layout: a specimen catalogue. Masthead with poster downloads and a legend grouped by
   color, a sticky filter bar, then one labelled grid per family. Diagrams carry the page. */
:root {
  --bg: #FFFFFF; --surface: #F4F4F1; --ink: #1C1B19; --muted: #66655F;
  --rule: #E3E2DD; --focus: #0077CC; --chip-on-bg: #1C1B19; --chip-on-ink: #FFFFFF;
  --scrim: rgba(28, 27, 25, 0.5);
  --display: 'Poppins', 'Avenir Next', 'Segoe UI', Helvetica, Arial, sans-serif;
  --body: 'Lato', 'Helvetica Neue', Helvetica, Arial, sans-serif;
  --mono: 'Noto Sans Mono', ui-monospace, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141413; --surface: #1F1F1D; --ink: #ECEBE6; --muted: #A3A19A;
    --rule: #302F2C; --focus: #4DB8FF; --chip-on-bg: #ECEBE6; --chip-on-ink: #141413;
    --scrim: rgba(0, 0, 0, 0.62); color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #141413; --surface: #1F1F1D; --ink: #ECEBE6; --muted: #A3A19A;
  --rule: #302F2C; --focus: #4DB8FF; --chip-on-bg: #ECEBE6; --chip-on-ink: #141413;
  --scrim: rgba(0, 0, 0, 0.62); color-scheme: dark;
}
:root[data-theme="light"] { color-scheme: light; }
[hidden] { display: none !important; }
.icon-btn { display: inline-grid; place-items: center; box-sizing: border-box; width: 40px; height: 40px;
  padding: 0; text-decoration: none; border-radius: 10px; border: 1px solid var(--rule);
  background: var(--bg); color: var(--ink); cursor: pointer; }
.icon-btn:hover { border-color: var(--muted); }
.icon-btn svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8;
  stroke-linecap: round; stroke-linejoin: round; }
/* The icon shows the theme a click switches to: a moon on light, a sun on dark. */
.icon-btn .i-sun { display: none; }
:root[data-theme="dark"] .icon-btn .i-sun { display: block; }
:root[data-theme="dark"] .icon-btn .i-moon { display: none; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .icon-btn .i-sun { display: block; }
  :root:not([data-theme="light"]) .icon-btn .i-moon { display: none; }
}
body { background: var(--bg); color: var(--ink); font-family: var(--body); font-size: 15px; line-height: 1.55; margin: 0; }
.page { max-width: 1240px; margin: 0 auto; box-sizing: border-box;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 40px 56px; }
.masthead { --title-size: clamp(30px, 5.2vw, 48px); display: grid; gap: 12px; margin-bottom: 22px; }
.masthead h1 { font-family: var(--display); font-weight: 500; font-size: var(--title-size);
  line-height: 1.06; letter-spacing: -0.01em; margin: 0; text-wrap: balance; }
/* The title and the theme button share a row; the button sits centered on the title's first line. */
/* The title row: the title, then the poster's save buttons and the theme button, both
   centered on the title's first line. On a phone the save buttons drop below the title. */
.titlebar { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px 16px; }
.titlebar h1 { flex: 1; min-width: 0; }
.titlebar .icon-btn, .titlebar .save { margin-top: calc((var(--title-size) * 1.06 - 40px) / 2); }
/* As on the poster: the subtitle on one line where the page is wide enough, and the reading
   text across the full width, balanced like the poster's (guide.POSTER_TEXT of the column). */
.lede { margin: 0; font-size: 20px; line-height: 1.4; text-wrap: balance; }
.reading { max-width: 94%; margin: 12px 0 0; color: var(--muted); text-wrap: balance; }
.eyebrow { font-family: var(--display); font-weight: 500; font-size: 11px; letter-spacing: 0.13em;
  text-transform: uppercase; color: var(--muted); }
.save { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.save .eyebrow { margin-right: 4px; }
.save-status { font-size: 13px; color: var(--muted); }
.legend { margin-top: 8px; border-top: 1px solid var(--rule); padding-top: 12px; }
.legend summary { cursor: pointer; width: max-content; }
.legend-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 10px 14px; margin-top: 8px; }
.lg-group { min-width: 0; }
.lg-title { font-family: var(--display); font-weight: 500; font-size: 14px; margin: 0 0 4px; }
.lg-group ul, .d-key ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; align-content: start; }
.lg-group li, .d-key li { display: flex; align-items: center; gap: 8px; font-size: 13px; line-height: 1.3; }
.lg-group svg, .d-key svg { width: 26px; height: 26px; flex: none; overflow: visible; }
/* the page's legend sits tighter than an explanation's key */
.lg-group ul { gap: 0; }
.lg-group li { line-height: 1.2; gap: 6px; white-space: nowrap; }   /* every label fits in Lato; a fallback font may run into the gap */
.lg-group svg { width: 20px; height: 20px; }   /* leaves the longest label, Cross-attention, comparison, one line */
.d-key { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 2px 14px; }
.filters { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--bg);
  border-bottom: 1px solid var(--rule); padding-block: 12px; display: flex; flex-wrap: wrap;
  gap: 10px 14px; align-items: center; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.controls select, .controls input { font: 14px var(--body); color: var(--ink); background: var(--bg);
  border: 1px solid var(--rule); border-radius: 8px; padding: 7px 10px; min-width: 0; }
.controls input { width: 12rem; max-width: 100%; }
.count { font: 12px var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.family { margin-top: 30px; }
.family h2 { font-family: var(--display); font-weight: 500; font-size: 12px; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--muted); margin: 0; padding-bottom: 8px;
  border-bottom: 1px solid var(--rule); }
.family h2.plain { text-transform: none; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 6px 14px; margin-top: 10px; }
.card { appearance: none; font: inherit; text-align: left; color: var(--ink); background: transparent;
  border: 1px solid transparent; border-radius: 10px; padding: 10px 10px 12px; cursor: pointer;
  display: grid; gap: 3px; align-content: start; min-width: 0; }
.card:hover { background: var(--surface); border-color: var(--rule); }
.card svg { width: 100%; height: auto; display: block; }
.card .row { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-top: 6px; }
.card .name { font-family: var(--display); font-size: 15px; font-weight: 500; line-height: 1.25; }
.card .year { font-family: var(--mono); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.card .cap { font-size: 13px; color: var(--muted); line-height: 1.35; }
.card .tag { justify-self: start; font-family: var(--mono); font-size: 11px; color: var(--muted);
  border: 1px solid var(--rule); border-radius: 999px; padding: 1px 8px; margin-top: 5px; }
.empty { margin-top: 40px; color: var(--muted); display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.btn { font: 500 13px/1 var(--display); padding: 9px 14px; border-radius: 8px; border: 1px solid var(--rule);
  background: var(--bg); color: var(--ink); cursor: pointer; }
.btn:hover { border-color: var(--muted); }
.btn:disabled { opacity: 0.55; cursor: progress; }
a.btn { display: inline-block; text-decoration: none; }
footer { margin-top: 56px; border-top: 1px solid var(--rule); padding-top: 16px; color: var(--muted);
  font-size: 13px; display: grid; gap: 4px; }
footer a { color: inherit; text-underline-offset: 2px; }
dialog { border: 1px solid var(--rule); border-radius: 14px; padding: 0; width: min(940px, calc(100vw - 32px));
  max-height: calc(100vh - 48px); max-height: calc(100dvh - 32px); overflow: auto; background: var(--bg);
  color: var(--ink); }
dialog::backdrop { background: var(--scrim); }
.d-wrap { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr); gap: 28px; padding: 28px; }
.d-side { min-width: 0; align-self: start; display: grid; gap: 14px; }
.d-label { font-family: var(--display); font-weight: 500; font-size: 11px; letter-spacing: 0.13em;
  text-transform: uppercase; color: var(--muted); margin: 0 0 6px; }
.d-fig { min-width: 0; }
.d-fig svg { width: 100%; height: auto; display: block; }
.d-text { min-width: 0; }
.d-kicker { font-family: var(--mono); font-size: 12px; color: var(--muted); margin: 0; }
.d-text h2 { font-family: var(--display); font-weight: 500; font-size: 28px; line-height: 1.15; margin: 4px 0 2px; }
.d-full { font-family: var(--display); font-size: 15px; margin: 0 0 2px; }
/* .d-text p would zero these margins, so they are set with .d-text's weight */
.d-text .d-cap { color: var(--muted); margin: 0 0 16px; }
.d-text .d-note { font-size: 13px; color: var(--muted); margin: -10px 0 16px; max-width: 62ch; }
#d-name:focus { outline: none; }
.d-text h3 { font-family: var(--display); font-weight: 500; font-size: 11px; letter-spacing: 0.13em;
  text-transform: uppercase; color: var(--muted); margin: 18px 0 4px; }
.d-text p { margin: 0; max-width: 62ch; }
/* Papers as a reference list: authors (year). Title, linked. Where it appeared. No bullets;
   a hanging indent, and a quiet underline that darkens on hover. */
.d-papers { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; max-width: 62ch; }
.d-papers li { padding-left: 1.5em; text-indent: -1.5em; }
.d-papers a { color: inherit; text-decoration-line: underline; text-decoration-thickness: 1px;
  text-underline-offset: 3px; text-decoration-color: color-mix(in srgb, currentColor 30%, transparent); }
.d-papers a:hover { text-decoration-color: currentColor; }
.d-papers .src { color: var(--muted); }
/* The buttons stay at the bottom of the dialog while its content scrolls, on phones especially */
.d-nav { grid-column: 1 / -1; display: flex; gap: 8px; justify-content: flex-end; border-top: 1px solid var(--rule);
  position: sticky; bottom: 0; background: var(--bg); padding: 14px 0 calc(14px + env(safe-area-inset-bottom, 0px)); }
@media (max-width: 760px) {
  .titlebar .save { order: 3; flex-basis: 100%; margin-top: 0; }
  .d-wrap { grid-template-columns: 1fr; padding: 20px; gap: 16px; }
  .controls { margin-left: 0; }
}
@media (prefers-reduced-motion: no-preference) {
  .card { transition: background-color 120ms ease, border-color 120ms ease; }
}
"""

PAGE_JS = r"""
(() => {
  const DATA = JSON.parse(document.getElementById('guide-data').textContent);
  const SYMBOLS = JSON.parse(document.getElementById('guide-symbols').textContent);
  const ORDER = Object.keys(DATA);
  const cards = Array.from(document.querySelectorAll('.card'));
  const famSel = document.getElementById('f-family');
  const objSel = document.getElementById('f-objective');
  const search = document.getElementById('f-search');
  const count = document.getElementById('f-count');
  const empty = document.getElementById('empty');
  const groupsEl = document.getElementById('groups');
  const orderSel = document.getElementById('f-order');
  let sections = Array.from(groupsEl.querySelectorAll('section.family'));
  const dlg = document.getElementById('detail');
  let family = 'all';
  let current = null;

  // in the order the page shows them now, so Previous and Next follow the chosen order
  function visibleIds() {
    return Array.from(groupsEl.querySelectorAll('.card')).filter(c => !c.hidden).map(c => c.dataset.id);
  }

  function apply() {
    const term = search.value.trim().toLowerCase();
    const obj = objSel.value;
    let shown = 0;
    for (const c of cards) {
      const d = DATA[c.dataset.id];
      const ok = (family === 'all' || d.family === family) && (obj === 'all' || d.objective === obj) &&
        (!term || d.search.includes(term));
      c.hidden = !ok;
      if (ok) shown++;
    }
    for (const s of sections) s.hidden = !s.querySelector('.card:not([hidden])');
    count.textContent = shown + (shown === 1 ? ' architecture' : ' architectures');
    empty.hidden = shown > 0;
  }

  famSel.addEventListener('change', () => { family = famSel.value; apply(); });
  objSel.addEventListener('change', apply);
  search.addEventListener('input', apply);
  document.getElementById('clear').addEventListener('click', () => {
    family = 'all';
    famSel.value = 'all';
    objSel.value = 'all';
    search.value = '';
    apply();
  });

  // The order: by family (as on the poster), by year in decades, by name, or by objective.
  // The cards move between sections built here; the choice is remembered for this reader.
  const cardById = {};
  cards.forEach(c => { cardById[c.dataset.id] = c; });
  const rank = id => ORDER.indexOf(id);
  const FAMILIES = [];
  ORDER.forEach(id => {
    const d = DATA[id];
    if (!FAMILIES.some(f => f.id === d.family)) FAMILIES.push({ id: d.family, title: d.familyTitle });
  });
  const OBJECTIVES = Array.from(objSel.options).filter(o => o.value !== 'all')
    .map(o => ({ id: o.value, title: o.textContent }));
  const byYear = sign => (a, b) => sign * (DATA[a].year - DATA[b].year) || rank(a) - rank(b);

  function groupsFor(order) {
    if (order === 'name') {
      return [{ id: 'az', title: 'A to Z', ids: ORDER.slice().sort((a, b) =>
        DATA[a].name.localeCompare(DATA[b].name, 'en', { sensitivity: 'base' })) }];
    }
    if (order === 'year' || order === 'year-desc') {
      const out = [];
      for (const id of ORDER.slice().sort(byYear(order === 'year' ? 1 : -1))) {
        const decade = Math.floor(DATA[id].year / 10) * 10;
        let g = out[out.length - 1];
        if (!g || g.decade !== decade) {
          g = { id: 'd' + decade, title: decade + 's', decade, ids: [], plain: true };
          out.push(g);
        }
        g.ids.push(id);
      }
      return out;
    }
    if (order === 'objective') {
      return OBJECTIVES.map(o => ({ id: 'o-' + o.id.replace(/\s+/g, '-'), title: o.title,
        ids: ORDER.filter(id => DATA[id].objective === o.id).sort(byYear(1)) })).filter(g => g.ids.length);
    }
    return FAMILIES.map(f => ({ id: f.id, title: f.title, ids: ORDER.filter(id => DATA[id].family === f.id) }));
  }

  function arrange(order) {
    groupsEl.textContent = '';
    for (const g of groupsFor(order)) {
      const sec = document.createElement('section');
      sec.className = 'family';
      sec.id = 'g-' + g.id;
      sec.setAttribute('aria-labelledby', 'h-' + g.id);
      const h = document.createElement('h2');
      h.id = 'h-' + g.id;
      h.textContent = g.title;
      if (g.plain) h.className = 'plain';   // "1990s", not "1990S"
      const grid = document.createElement('div');
      grid.className = 'grid';
      for (const id of g.ids) grid.append(cardById[id]);
      sec.append(h, grid);
      groupsEl.append(sec);
    }
    sections = Array.from(groupsEl.querySelectorAll('section.family'));
    apply();
  }

  orderSel.addEventListener('change', () => {
    arrange(orderSel.value);
    try { localStorage.setItem('field-guide-order', orderSel.value); } catch (e) {}
  });
  let savedOrder = null;
  try { savedOrder = localStorage.getItem('field-guide-order'); } catch (e) {}
  if (savedOrder && savedOrder !== 'family' && Array.from(orderSel.options).some(o => o.value === savedOrder)) {
    orderSel.value = savedOrder;
    arrange(savedOrder);
  }

  function setText(id, text) {
    document.getElementById(id).textContent = text || '';
  }

  function open(id) {
    const d = DATA[id];
    const card = document.querySelector('.card[data-id="' + id + '"]');
    if (!d || !card) return;
    current = id;
    const svg = card.querySelector('svg').outerHTML.split('arr-' + id).join('arr-' + id + '-big');
    document.getElementById('d-fig').innerHTML = svg;
    setText('d-kicker', d.familyTitle + ' · ' + d.year);
    setText('d-name', d.name);
    setText('d-full', d.full);
    document.getElementById('d-full').hidden = !d.full;
    setText('d-cap', d.caption);
    setText('d-note', d.note);
    document.getElementById('d-note').hidden = !d.note;
    setText('d-summary', d.summary);
    setText('d-read', d.read);
    setText('d-objective', d.objective);
    document.getElementById('d-read-wrap').hidden = !d.read;
    // the key: only the cells and links this diagram uses
    const key = document.getElementById('d-key');
    key.textContent = '';
    // cells in one column, links in the other
    for (const [kind, list] of [['cells', d.key.cells], ['links', d.key.links]]) {
      if (!list.length) continue;
      const col = document.createElement('ul');
      col.setAttribute('aria-label', kind === 'cells' ? 'Cells' : 'Links');
      for (const k of list) {
        const [svg, label] = SYMBOLS[kind][k];
        const li = document.createElement('li');
        li.innerHTML = svg;
        li.append(label);
        col.append(li);
      }
      key.append(col);
    }
    const ul = document.getElementById('d-papers');
    ul.textContent = '';
    for (const p of d.papers) {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = p.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = p.title;
      const src = document.createElement('span');
      src.className = 'src';
      src.textContent = p.source;
      // Authors (year). Title. Source.
      li.append(p.authors + ' (' + p.year + '). ', a, /[.?!]$/.test(p.title) ? ' ' : '. ', src);
      ul.append(li);
    }
    const opening = !dlg.open;
    if (opening) {
      if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
      // the dialog would focus its first link, a paper far down, and scroll to it; start at the
      // title instead (Previous and Next keep focus on their button)
      document.getElementById('d-name').focus({ preventScroll: true });
    }
    dlg.scrollTop = 0;  // every architecture opens at its top
    try { history.replaceState(null, '', '#' + id); } catch (e) {}
  }

  function close() {
    if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open');
  }

  function step(delta) {
    const ids = visibleIds();
    const list = ids.length ? ids : ORDER;
    const i = list.indexOf(current);
    open(list[(i + delta + list.length) % list.length]);
  }

  dlg.addEventListener('close', () => {
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    const card = current && document.querySelector('.card[data-id="' + current + '"]');
    if (card) card.focus();
  });
  dlg.addEventListener('click', e => { if (e.target === dlg) close(); });
  dlg.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
  });
  document.getElementById('d-prev').addEventListener('click', () => step(-1));
  document.getElementById('d-next').addEventListener('click', () => step(1));
  document.getElementById('d-close').addEventListener('click', close);
  cards.forEach(c => c.addEventListener('click', () => open(c.dataset.id)));

  // The theme: the page follows the reader's setting until the button is used, then
  // remembers the choice for that reader.
  const root = document.documentElement;
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const themeBtn = document.getElementById('theme');
  function themeNow() {
    const t = root.getAttribute('data-theme');
    return t === 'light' || t === 'dark' ? t : (mq && mq.matches ? 'dark' : 'light');
  }
  function labelTheme() {
    const label = themeNow() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
    themeBtn.setAttribute('aria-label', label);
    themeBtn.title = label;
  }
  themeBtn.addEventListener('click', () => {
    const next = themeNow() === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('field-guide-theme', next); } catch (e) {}
    labelTheme();
  });
  if (mq && mq.addEventListener) mq.addEventListener('change', labelTheme);
  new MutationObserver(labelTheme).observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  labelTheme();

  apply();
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash && DATA[hash]) open(hash);

  // Save the poster. On claude.ai the page asks the viewer's downloads capability, which
  // shows its own confirmation; the buttons stay hidden wherever that isn't available.
  (async () => {
    const box = document.getElementById('save');
    if (!box || box.dataset.mode !== 'artifact') return;
    const host = window.claude;
    if (!host || typeof host.use !== 'function') return;
    let downloads = null;
    try { downloads = await host.use('downloads'); } catch (e) { downloads = null; }
    if (!downloads) return;
    const status = document.getElementById('save-status');
    const codeOf = e => String((e && (e.code || e.name)) || '');
    const declined = e => /cancel|declin|abort|denied|reject|not_granted/i.test(codeOf(e));
    const malformed = e => /invalid|type|argument|unsupported/i.test(codeOf(e));
    box.hidden = false;
    box.querySelectorAll('button[data-file]').forEach(btn => btn.addEventListener('click', async () => {
      const name = btn.dataset.name;
      status.textContent = 'Preparing ' + name + '…';
      btn.disabled = true;
      try {
        const res = await fetch(btn.dataset.file);
        if (!res.ok) throw Object.assign(new Error('load failed'), { code: 'load_failed' });
        const blob = await res.blob();
        try {
          await downloads.save({ filename: name, data: blob });
        } catch (e) {
          if (declined(e) || !malformed(e)) throw e;
          await downloads.save({ filename: name, data: new Uint8Array(await blob.arrayBuffer()) });
        }
        status.textContent = 'Saved ' + name;
      } catch (e) {
        if (codeOf(e) === 'load_failed') status.textContent = 'Couldn’t load the poster file. Reload the page and try again.';
        else if (declined(e)) status.textContent = 'Not saved.';
        else status.textContent = 'Couldn’t save the file. Try again in a moment.';
      } finally {
        btn.disabled = false;
      }
    }));
  })();
})();
"""


def symbol_svg(kind: str, name: str) -> str:
    """A legend or key icon: one cell with its mark, or a sample of one link style."""
    inner = (
        cell_svg(name, 12, 0)
        if kind == "cell"
        else link_sample(name, 1, 0, "currentColor", "arr-legend")
    )
    return f'<svg viewBox="-2 -13 28 26" aria-hidden="true" focusable="false">{inner}</svg>'


def legend_html(cells: list, links: list) -> str:
    def li(kind, name, label):
        return f"<li>{symbol_svg(kind, name)}{html.escape(label)}</li>"

    groups = []
    for label, items in legend_groups(cells, links):
        lis = "".join(
            li(kind, k, CELLS[k]["label"] if kind == "cell" else LINKS[k]["label"])
            for kind, k in items
        )
        groups.append(
            f'<div class="lg-group"><p class="lg-title">{html.escape(label)}</p>'
            f"<ul>{lis}</ul></div>"
        )
    return (
        f'<details class="legend" open><summary class="eyebrow">How to read the diagrams</summary>'
        f'<div class="legend-grid">{"".join(groups)}</div></details>'
    )


def credit_html(doc: dict) -> str:
    text = html.escape(doc["credit"])
    link = doc.get("credit_link")
    if link and html.escape(link["text"]) in text:
        anchor = (
            f'<a href="{html.escape(link["url"])}" target="_blank" rel="noopener">'
            f'{html.escape(link["text"])}</a>'
        )
        text = text.replace(html.escape(link["text"]), anchor, 1)
    return text


def repo_html(doc: dict) -> str:
    """ " · github.com/…" for the page's footer, linked; empty without a repo."""
    repo = doc.get("repo")
    if not repo:
        return ""
    return f' · <a href="{html.escape(repo["url"])}" target="_blank" rel="noopener">{html.escape(repo["text"])}</a>'


def source_button(doc: dict) -> str:
    """The title row's link to the repository, an icon button like the theme button."""
    repo = doc.get("repo")
    if not repo:
        return ""
    return (
        f'<a class="icon-btn" href="{html.escape(repo["url"])}" target="_blank" rel="noopener" '
        f'aria-label="Source on GitHub" title="Source on GitHub">{CODE_ICON}</a>\n'
    )


def license_html(doc: dict) -> str:
    """ " · CC BY-SA 4.0" for the page's footer, linked to the license; empty without one."""
    lic = license_of(doc)
    if not lic:
        return ""
    return (
        f' · <a href="{html.escape(lic["url"])}" target="_blank" rel="license noopener">'
        f'{html.escape(lic["text"])}</a>'
    )


def save_html(mode: str) -> str:
    label = '<span class="eyebrow">Poster</span>'
    if mode == "standalone":
        links = "".join(  # opened in a new tab, where the browser shows them; saved from there
            f'<a class="btn" href="../{src}" target="_blank" rel="noopener">{text}</a>'
            for src, text in POSTER_FILES
        )
        return f'<div class="save" id="save" data-mode="standalone">{label}{links}</div>'
    buttons = "".join(
        f'<button class="btn" type="button" data-file="{src}" data-name="{src}">{text}</button>'
        for src, text in POSTER_FILES
    )
    return (
        f'<div class="save" id="save" data-mode="artifact" hidden>{label}{buttons}'
        f'<span class="save-status" id="save-status" role="status" aria-live="polite"></span></div>'
    )


def paper_ref(p: dict) -> dict:
    """A paper's authors, year, and where it appeared, for the page's reference list. The
    source is the paper's `source` if it has one, else read from the link: an arXiv id, a
    DOI, or the site's name."""
    m = re.match(r"(.*\S)\s+(\d{4})$", str(p["cite"]))
    authors, year = (m.group(1), m.group(2)) if m else (str(p["cite"]), "")
    url = p["url"]
    if p.get("source"):  # where the link can't say it, e.g. a book chapter
        source = p["source"]
    elif m := re.match(r"https://arxiv\.org/abs/(.+)", url):
        source = f"arXiv:{m.group(1)}"
    elif m := re.match(r"https://doi\.org/(.+)", url):
        source = f"doi:{m.group(1)}"
    else:
        host = re.sub(r"^www\.", "", url.split("/")[2])
        source = SOURCES.get(host, host)
    return dict(authors=authors, year=year, source=source)


SOURCES = {
    "papers.nips.cc": "NeurIPS",
    "transformer-circuits.pub": "Transformer Circuits Thread",
    "cdn.openai.com": "OpenAI",
    "complex-systems.com": "Complex Systems",
}


def favicon_svg(background: str = "#FFFFFF") -> str:
    """The page's icon: five cells in a ring, one in each of the chart's colors, every cell
    linked to every other (a Hopfield net), on a light rounded square so the links read on
    light and dark tab bars."""
    r, gap = 3.5, 0.9
    pts = [
        (16 + 10.5 * math.sin(2 * math.pi * k / 5), 16.8 - 10.5 * math.cos(2 * math.pi * k / 5))
        for k in range(5)
    ]
    hues = ("red", "blue", "yellow", "green", "pink")
    lines = []
    for i in range(5):
        for j in range(i + 1, 5):
            (x1, y1), (x2, y2) = pts[i], pts[j]
            d = math.hypot(x2 - x1, y2 - y1)
            ux, uy = (x2 - x1) / d * (r + gap), (y2 - y1) / d * (r + gap)
            lines.append(
                f'<line x1="{x1 + ux:.2f}" y1="{y1 + uy:.2f}" x2="{x2 - ux:.2f}" y2="{y2 - uy:.2f}"/>'
            )
    cells = "".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r}" fill="{PALETTE[h][0]}" stroke="{PALETTE[h][1]}" '
        f'stroke-width="1"/>'
        for (x, y), h in zip(pts, hues)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        f'<rect width="32" height="32" rx="7" fill="{background}"/>'
        f'<g stroke="{INK_LIGHT}" stroke-width="1.1" stroke-linecap="round">{"".join(lines)}</g>{cells}</svg>'
    )


def favicon_data_uri() -> str:
    from urllib.parse import quote

    return "data:image/svg+xml," + quote(favicon_svg(), safe=" =:/,;()'")


def write_icons(out: str):
    """favicon.ico (16, 32 and 48 px) and apple-touch-icon.png (180 px) beside the page, and
    icon.svg; the PNG and ICO need cairosvg and Pillow."""
    write(os.path.join(out, "icon.svg"), favicon_svg())
    try:
        import cairosvg
        from PIL import Image
    except ImportError:
        print(
            "cairosvg or Pillow is not installed, so favicon.ico and apple-touch-icon.png were skipped"
        )
        return
    big = Image.open(
        io.BytesIO(
            cairosvg.svg2png(
                bytestring=favicon_svg().encode(), output_width=256, output_height=256
            )
        )
    )
    big.save(os.path.join(out, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
    # iOS draws its own rounded corners, so the touch icon is square and opaque
    square = favicon_svg().replace('rx="7"', 'rx="0"')
    cairosvg.svg2png(
        bytestring=square.encode(),
        output_width=180,
        output_height=180,
        write_to=os.path.join(out, "apple-touch-icon.png"),
    )


# The link preview: what Slack, iMessage, Bluesky, and the like show for a shared link to the page.
PREVIEW_SIZE = (1200, 630)
PREVIEW_PANELS = [
    "perceptron",
    "hopfield",
    "cnn",
    "lstm",
    "vae",
    "gan",
    "resnet",
    "unet",
    "transformer",
    "moe",
    "mamba",
    "diffusion",
]


def preview_svg(doc: dict, diagrams: dict) -> str:
    """The link preview: the title and subtitle over twelve of the panels, in two rows."""
    pw, ph = PREVIEW_SIZE
    m, cols, gap, ink = 56, 6, 8, INK_LIGHT
    sub = 22
    while text_width(doc["subtitle"], "body", sub) > pw - 2 * m and sub > 14:
        sub -= 1
    scale = (pw - 2 * m - (cols - 1) * gap) / cols / W
    parts = [
        f'<rect width="{pw}" height="{ph}" fill="{BG_LIGHT}"/>',
        svg_text(m, 112, doc["title"], "display", 56, ink, 500),
        svg_text(m, 156, doc["subtitle"], "body", sub, ink, 400, TYPE["subtitle"][1] * 0.75),
    ]
    for i, aid in enumerate(PREVIEW_PANELS):
        x, y = m + (i % cols) * (W * scale + gap), 210 + (i // cols) * (H * scale + gap)
        parts.append(
            f'<g transform="translate({fmt(x)} {fmt(y)}) scale({scale:.4f})">'
            f'{render_diagram(diagrams[aid], "arr-preview", ink)}</g>'
        )
    site = doc.get("site")
    if site:
        parts.append(
            svg_text(
                m,
                ph - 44,
                site["url"].replace("https://", "").rstrip("/"),
                "body",
                18,
                ink,
                400,
                0.75,
            )
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {pw} {ph}" width="{pw}" height="{ph}">'
        f'<defs>{marker_def("arr-preview", ink)}</defs>{"".join(parts)}</svg>'
    )


def preview_meta(doc: dict) -> str:
    """The page's description and link-preview tags; the preview image needs the page's address."""
    desc = html.escape(doc["subtitle"], quote=True)
    out = f'<meta name="description" content="{desc}">\n'
    site = doc.get("site")
    if not site:
        return out
    url, title = html.escape(site["url"]), html.escape(doc["web_title"], quote=True)
    pw, ph = PREVIEW_SIZE
    return (
        out + f'<link rel="canonical" href="{url}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        f'<meta property="og:url" content="{url}">\n'
        f'<meta property="og:image" content="{url}preview.png">\n'
        f'<meta property="og:image:width" content="{pw}">\n'
        f'<meta property="og:image:height" content="{ph}">\n'
        f'<meta property="og:image:alt" content="{title}: twelve of its diagrams under the title">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
    )


def write_preview(out: str, doc: dict, diagrams: dict):
    """preview.png beside the page; needs cairosvg."""
    try:
        import cairosvg
    except ImportError:
        print("cairosvg is not installed, so preview.png was skipped")
        return
    svg = for_cairo(preview_svg(doc, diagrams), export_faces())
    cairosvg.svg2png(bytestring=svg.encode(), write_to=os.path.join(out, "preview.png"))


def web_page(doc: dict, diagrams: dict, mode: str = "artifact") -> str:
    """mode='artifact': a fragment for publishing; mode='standalone': a complete HTML file."""
    arch = doc["architectures"]
    fam_of = {p: s for s in doc["sections"] for p in s["panels"]}
    notes = footnotes(doc)
    data = OrderedDict()
    sections_html = []
    for s in doc["sections"]:
        cards = []
        for pid in s["panels"]:
            a, d = arch[pid], diagrams[pid]
            mid = f"arr-{pid}"
            caption = caption_text(notes, pid, a)
            svg = (
                f'<svg viewBox="0 0 {fmt(W)} {fmt(H)}" aria-hidden="true" focusable="false">'
                f'<defs>{marker_def(mid, "currentColor")}</defs>{render_diagram(d, mid)}</svg>'
            )
            cards.append(
                f'<button class="card" type="button" data-id="{pid}">{svg}'
                f'<span class="row"><span class="name">{html.escape(a["name"])}</span>'
                f'<span class="year">{a["year"]}</span></span>'
                f'<span class="cap">{html.escape(caption)}</span>'
                f'<span class="tag">{html.escape(a["objective"])}</span></button>'
            )
            papers = [dict(paper_ref(p), title=p["title"], url=p["url"]) for p in a["papers"]]
            text = " ".join(
                [
                    a["name"],
                    a.get("full", ""),
                    a["caption"],
                    a["summary"],
                    str(a["year"]),
                    a["objective"],
                    s["title"],
                ]
            ).lower()
            used_cells = {c.kind for c in d.cells.values()}
            used_links = {s.style for s in d.segs}
            data[pid] = dict(
                name=a["name"],
                full=a.get("full", ""),
                year=a["year"],
                family=fam_of[pid]["id"],
                familyTitle=fam_of[pid]["title"],
                caption=caption,
                objective=a["objective"],
                note=" ".join(notes[pid]) if pid in notes else "",
                summary=a["summary"].strip(),
                read=a.get("read", "").strip(),
                papers=papers,
                search=text,
                key=dict(
                    cells=[k for k in CELLS if k in used_cells], links=legend_links(used_links)
                ),
            )
        sections_html.append(
            f'<section class="family" id="g-{s["id"]}" aria-labelledby="h-{s["id"]}">'
            f'<h2 id="h-{s["id"]}">{html.escape(s["title"])}</h2>'
            f'<div class="grid">{"".join(cards)}</div></section>'
        )
    families = '<option value="all">All families</option>' + "".join(
        f'<option value="{s["id"]}">{html.escape(s["title"])}</option>' for s in doc["sections"]
    )
    objectives = sorted({a["objective"] for a in arch.values()}, key=OBJECTIVES.index)
    options = '<option value="all">Any objective</option>' + "".join(
        f'<option value="{html.escape(o)}">{html.escape(o.capitalize())}</option>'
        for o in objectives
    )
    cells, links = used_kinds(diagrams)
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    symbols = dict(
        cells={k: [symbol_svg("cell", k), CELLS[k]["label"]] for k in CELLS},
        links={k: [symbol_svg("link", k), LINKS[k]["label"]] for k in LINKS},
    )
    symbols_json = json.dumps(symbols, ensure_ascii=False).replace("</", "<\\/")
    defs = f"<defs>{marker_def('arr-legend', 'currentColor')}</defs>"
    body = f"""<div class="page">
<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">{defs}</svg>
<header class="masthead">
<div class="titlebar"><h1>{html.escape(doc["title"])}</h1>
{save_html(mode)}
{source_button(doc)}<button class="icon-btn" type="button" id="theme" aria-label="Switch to dark mode" title="Switch to dark mode">{THEME_ICONS}</button></div>
<div><p class="lede">{html.escape(doc["subtitle"])}</p>
<p class="reading">{html.escape(doc["reading"])} Select a diagram for its explanation, key, and papers.</p></div>
{legend_html(cells, links)}
</header>
<nav class="filters" aria-label="Filter architectures">
<div class="controls">
<select id="f-family" aria-label="Family">{families}</select>
<select id="f-order" aria-label="Order">{ORDER_OPTIONS}</select>
<select id="f-objective" aria-label="Training objective">{options}</select>
<input id="f-search" type="search" placeholder="Search, e.g. memory" aria-label="Search architectures">
<span class="count" id="f-count" aria-live="polite"></span>
</div>
</nav>
<main>
<div id="groups">{"".join(sections_html)}</div>
<div class="empty" id="empty" hidden><span>No architectures match these filters.</span>
<button class="btn" type="button" id="clear">Clear filters</button></div>
</main>
<footer><span>{credit_html(doc)}</span><span>{html.escape(notice(doc))}{license_html(doc)}{repo_html(doc)} · Last updated <time datetime="{updated_date(doc).isoformat()}">{date_text(updated_date(doc))}</time></span></footer>
</div>
<dialog id="detail" aria-labelledby="d-name">
<div class="d-wrap">
<div class="d-side">
<div class="d-keybox"><h3 class="d-label">Key</h3><div class="d-key" id="d-key"></div></div>
<div class="d-fig" id="d-fig"></div>
</div>
<div class="d-text">
<p class="d-kicker" id="d-kicker"></p>
<h2 id="d-name" tabindex="-1"></h2>
<p class="d-full" id="d-full"></p>
<p class="d-cap" id="d-cap"></p>
<p class="d-note" id="d-note" hidden></p>
<p id="d-summary"></p>
<div id="d-read-wrap"><h3>Reading the diagram</h3><p id="d-read"></p></div>
<h3>Objective</h3><p id="d-objective"></p>
<h3>Papers</h3><ul class="d-papers" id="d-papers"></ul>
</div>
<div class="d-nav"><button class="btn" type="button" id="d-prev">Previous</button>
<button class="btn" type="button" id="d-next">Next</button>
<button class="btn" type="button" id="d-close">Close</button></div>
</div>
</dialog>
<script type="application/json" id="guide-data">{data_json}</script>
<script type="application/json" id="guide-symbols">{symbols_json}</script>
<script>{PAGE_JS}</script>"""
    icons = f'<link rel="icon" href="{favicon_data_uri()}" type="image/svg+xml">\n'
    if (
        mode == "standalone"
    ):  # files the build writes beside index.html, for browsers without SVG icons
        icons = (
            '<link rel="icon" href="favicon.ico" sizes="32x32">\n'
            + icons
            + '<link rel="apple-touch-icon" href="apple-touch-icon.png">\n'
            + preview_meta(doc)
        )
    head = (
        f'<title>{html.escape(doc["web_title"])}</title>\n'
        + icons
        + f'<meta name="claude:viewport-fit-sides" content="cover">\n'
        f'<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'<link rel="stylesheet" href="{FONT_LINK}">\n'
        f"<style>{PAGE_CSS}</style>\n"
        "<script>try{var t=localStorage.getItem('field-guide-theme');"
        "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>\n"
    )
    if mode == "artifact":
        return head + body
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        + head
        + "</head>\n<body>\n"
        + body
        + "\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    doc["subtitle"] = subtitle_text(doc)
    return doc


def subtitle_text(doc: dict) -> str:
    """The subtitle with its counts and ends filled in from the chart: {count} is the number
    of panels, {first} and {last} the oldest and newest (ties go to the one placed first, or
    last, on the poster), each as its `in_text` reads in a sentence or else its name, and
    {first_year} and {last_year} their years."""
    arch = doc["architectures"]
    placed = [p for s in doc["sections"] for p in s["panels"] if p in arch]
    if not placed:
        return doc["subtitle"]
    first = min(placed, key=lambda p: arch[p]["year"])
    last = max(reversed(placed), key=lambda p: arch[p]["year"])
    said = lambda p: arch[p].get("in_text") or arch[p]["name"]  # noqa: E731
    return doc["subtitle"].format(
        count=len(placed),
        first=said(first),
        first_year=arch[first]["year"],
        last=said(last),
        last_year=arch[last]["year"],
    )


def build_all(doc: dict) -> dict:
    return {aid: build_diagram(aid, a["diagram"]) for aid, a in doc["architectures"].items()}


def write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def export_faces() -> dict:
    """The EXPORT_FACES that are installed; a family whose face is missing keeps its name."""
    found = {}
    for fam, face in EXPORT_FACES.items():
        try:
            out = subprocess.run(
                ["fc-match", "-f", "%{fullname}", face], capture_output=True, text=True, timeout=5
            )
            if face.lower() in out.stdout.lower():
                found[fam] = face
                continue
        except (OSError, subprocess.SubprocessError):
            pass
        print(f"{face} was not found, so the PDF and PNG may set {fam} a weight lighter")
    return found


def for_cairo(svg: str, faces: dict) -> str:
    """The poster as Cairo should draw it: text lighter than bold names its family's face."""

    def swap(m):
        tag = m.group(0)
        weight = re.search(r'font-weight="(\d+)"', tag)
        if weight and int(weight.group(1)) >= 550:  # cairosvg draws these bold
            return tag
        return re.sub(
            r'font-family="([^,"]+)',
            lambda f: f'font-family="{faces.get(f.group(1), f.group(1))}',
            tag,
            count=1,
        )

    return re.sub(r"<text\b[^>]*>", swap, svg)


def chart_date(doc: dict, diagrams: dict, out: str) -> datetime.date:
    """The day to print as "Last updated": the date of the last build, kept when this build
    draws the poster and the page exactly as that one did, and today when anything changed.
    So rebuilding without a change leaves dist/ as it was, and a change dates itself."""
    try:
        with open(os.path.join(out, "web", "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        with open(os.path.join(out, f"{POSTER}.svg"), encoding="utf-8") as fh:
            poster = fh.read()
        last = datetime.date.fromisoformat(re.search(r'<time datetime="([\d-]+)">', page).group(1))
    except (OSError, AttributeError, ValueError):
        return datetime.date.today()
    same = dict(doc, updated=last)
    if (
        poster_svg(same, diagrams, "light") == poster
        and web_page(same, diagrams, "standalone") == page
    ):
        return last
    return datetime.date.today()


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("command", choices=["build", "lint"])
    ap.add_argument("--specs", default=os.path.join(here, "architectures.yaml"))
    ap.add_argument("--out", default=os.path.join(here, "dist"))
    ap.add_argument(
        "--strict", action="store_true", help="exit with an error if lint finds anything"
    )
    args = ap.parse_args(argv)

    doc = load(args.specs)
    diagrams = build_all(doc)
    warns = lint_doc(doc, diagrams)
    for w in warns:
        print(f"lint: {w}")
    print(f"lint: {len(warns)} warning(s) across {len(diagrams)} diagrams")
    if args.command == "lint" or (args.strict and warns):
        sys.exit(1 if warns else 0)

    arch = doc["architectures"]
    if doc.get("updated") is None:  # set by hand in architectures.yaml, it wins
        doc["updated"] = chart_date(doc, diagrams, args.out)
    print(f"{updated_text(doc)}")
    for aid, d in diagrams.items():
        write(os.path.join(args.out, "panels", f"{aid}.svg"), panel_svg(d, arch[aid]["name"]))
    light, dark = poster_svg(doc, diagrams, "light"), poster_svg(doc, diagrams, "dark")
    write(os.path.join(args.out, f"{POSTER}.svg"), light)
    ends = (".pdf", ".png", "-dark.png")
    pdf, png, png_dark = (os.path.join(args.out, POSTER + end) for end in ends)
    try:
        import cairosvg

        faces = export_faces()
        light_c, dark_c = for_cairo(light, faces), for_cairo(dark, faces)
        cairosvg.svg2pdf(bytestring=light_c.encode(), write_to=pdf)
        add_pdf_links(pdf, light)
        cairosvg.svg2png(bytestring=light_c.encode(), write_to=png, scale=2)
        cairosvg.svg2png(bytestring=dark_c.encode(), write_to=png_dark, scale=2)
    except ImportError:
        print("cairosvg is not installed, so the PDF and PNG exports were skipped")
    write(os.path.join(args.out, "web", "index.html"), web_page(doc, diagrams, "standalone"))
    write(os.path.join(args.out, "web", "artifact.html"), web_page(doc, diagrams, "artifact"))
    write_icons(os.path.join(args.out, "web"))
    write_preview(os.path.join(args.out, "web"), doc, diagrams)
    print(f"built {len(diagrams)} panels, the poster, and the web page in {args.out}")


if __name__ == "__main__":
    main()
