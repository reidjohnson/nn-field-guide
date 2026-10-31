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
from collections import Counter, OrderedDict
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
R = 7.0  # cell radius; squares, diamonds, and inner marks scale with it
R0 = 7.0  # the radius the cell types' sizes (`half`) and inner marks are drawn for
RING_PAD = 2.3  # from a cell's outline to the state group's outer ring
STEM, EAR, MARK_GAP = 4.5, 3.5, 1.5  # input/output stem, mixing tick, gap from the outline
PITCH = 48.0  # default spacing between cells in a row (a row that must taper pins its own, as U-Net does)
SIDE = 16.0  # min distance from canvas side to a cell centre
CLEARANCE = 2.0  # a line must clear cells it doesn't touch by this

# Lines: every link, arrow, skip connection, and return arrow is drawn at one weight and
# ends the same distance from the outline it meets; styles differ only by dash and head.
LINE = 0.8  # stroke width of every line, the same as the cell outlines
GAP = 0.9  # from a line's end (or an arrow's tip) to the outline it meets
HEAD = 5.4  # arrowhead size, the same on every arrow
DOT = 1.7  # radius of the dot where a gate meets a state arrow
BEND = 6.0  # radius of every bend in a skip connection or return arrow
# the bend of an LSTM's or GRU's gate hook, tighter than BEND: its legs are only a few units
# long, and at BEND the corner swallows them, so the hook reads as a soft hump rather than a
# diagonal and a drop
HOOK_BEND = 1.5
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
    # a whole network: a neutral gray, darker than the five hues so it stays distinct in grayscale
    "gray": ("#5A5853", "#403E3A"),
}
GLYPH_INK = "#1A1A1A"  # the ink of a cell's inner mark
GLYPH_INK_ON = {
    "red": "#FFFFFF",
    "gray": "#FFFFFF",
}  # color -> ink of its inner marks where black reads poorly (white: 4.6:1 on red, 7.1:1 on gray)
GLYPH_STROKE = 1.1  # stroke width of inner marks, at R0: the stems and ticks' weight, so every mark is one weight and every link another (LINE)
INK_LIGHT, BG_LIGHT = "#1C1B19", "#FFFFFF"
INK_DARK, BG_DARK = "#ECEBE6", "#161615"

# Poster type: each role's size in px and the opacity of the ink it is set in. The opacities keep
# every text role at 7.5:1 or more on both posters.
TYPE = {
    "title": (48, 1.0),
    "subtitle": (22, 1.0),
    "reading": (16, 0.75),
    "legend_head": (15, 1.0),
    "legend": (13.5, 0.9),
    "part": (15, 1.0),
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
        # a whole network: it holds all five jobs, so it is none of them; its own legend column
        ("network", dict(color="gray", label="Whole systems", mark=None, job=False)),
    ]
)
LINK_GROUP = "Links"

# Cell types, in legend order. The glyph is an inner mark that refines the group.
CELLS = OrderedDict(
    [
        ("input", dict(group="input", label="Input unit")),
        ("patch", dict(group="input", shape="square", half=6.0, label="Image patch")),
        ("noisy", dict(group="input", glyph="triangle", label="Noisy input")),
        ("noise", dict(group="input", glyph="wave", label="Random noise")),
        ("backfed", dict(group="input", glyph="ring", label="Visible unit (in and out)")),
        ("mask", dict(group="input", hollow=True, label="Masked token")),  # the hidden input, last
        ("hidden", dict(group="hidden", label="Hidden unit")),
        ("prob", dict(group="hidden", glyph="ring", label="Probabilistic hidden")),
        ("capsule", dict(group="hidden", glyph="square", label="Capsule (vector output)")),
        ("router", dict(group="hidden", shape="diamond", half=5.5, label="Router")),
        (  # any architecture as one box: an expert, a generator, a denoiser
            "network",
            dict(group="network", shape="square", glyph="net", label="A whole network"),
        ),
        (  # outside the model (an environment, a data store, a tool): the network's box, hollow
            "environment",
            dict(group="network", shape="square", outline=True, label="Environment"),
        ),
        (  # the primer's Transformer block only: a skip path joins here and the sum is normalized
            "addnorm",
            dict(group="network", glyph="plus", label="Add, then normalize"),
        ),
        ("recurrent", dict(group="state", label="Recurrent unit")),
        ("memory", dict(group="state", glyph="ring", label="Memory cell")),
        ("gated", dict(group="state", glyph="triangle", label="Gated recurrent unit")),
        ("selective", dict(group="state", glyph="diamond", label="Selective state (SSM)")),
        (
            "matrix",
            dict(group="state", shape="square", label="Matrix-valued state"),
        ),  # the one square, last
        ("attention", dict(group="mixing", label="Attention")),
        ("conv", dict(group="mixing", glyph="ring", label="Convolution or pooling")),
        ("output", dict(group="output", label="Output unit")),
        ("match", dict(group="output", glyph="ring", label="Reconstruction of input")),
        ("velocity", dict(group="output", glyph="arrow", label="Velocity")),
    ]
)

# Link styles, in legend order. All are drawn at LINE; a style changes only the dash,
# the opacity, the arrowhead or (skip, loop, func) the path. An arrowhead means one-way
# flow: the straight arrow carries state, a message, or data from one part to another, and
# the elbow arrow carries an output back to the input to run the network again. A gate is
# dotted and acts at a dot on a state arrow.
# a glossary figure's colored lines (contrastive learning): matched pairs pulled together, the
# rest pushed apart; the page sets them again for dark mode
TONES = {"pull": PALETTE["green"][1], "push": PALETTE["red"][1]}
# the same lines on a dark page: green and blue take their lighter fill, red a lighter red
DARK_LINES = {"green": PALETTE["green"][0], "blue": PALETTE["blue"][0], "red": "#E5673E"}


def figure_palette_css() -> str:
    """The palette as the page's CSS variables, for the glossary figures' own parts (a chart's
    curves and bars, a plane's arrows and band): --fig-<hue>-fill and --fig-<hue>-line as the
    cells use them, and --fig-<hue> for a colored line, lighter on a dark page."""
    light = "".join(
        f" --fig-{h}-fill: {f}; --fig-{h}-line: {l};" for h, (f, l) in PALETTE.items()
    ) + "".join(f" --fig-{h}: {PALETTE[h][1]};" for h in DARK_LINES)
    dark = "".join(f" --fig-{h}: {c};" for h, c in DARK_LINES.items())
    return (
        f":root {{{light} }}\n"
        f':root[data-theme="dark"] {{{dark} }}\n'
        f'@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{{dark} }} }}\n'
    )


# The link styles, kept few (six today) so the legend stays learnable: each is a different kind
# of thing a line carries, not a different layer or model. Add one only with very good reason:
# when no existing style can say what a line does, and the cell marks and wiring can't either
# (a convolution's lines are learned weights like a dense layer's; its cells and windows say
# the rest).
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
        (
            "cross",
            dict(dash="3 2", label="Similarity score"),
        ),  # attention's reads, or a similarity
        ("func", dict(label="Learnable function")),
    ]
)

# Relations between panels, written once on the more specific panel; the page shows each from
# both ends. key -> (label on the panel that lists it, label on the panel it points to)
# the names a panel lists besides its own, each under its own label, all found by search:
# other names for the same thing; versions of the design drawn; and distinct methods that
# only resemble it, which don't share the drawing or the objective
NAME_LISTS = {
    "aka": "Also called",
    "variants": "Variants",
    "related_methods": "Related methods",
}

# what a glossary entry's list of panels means, and its label: the networks that have or train
# with it (attention, a gate); the panels that are one (a generative model); or, for an idea
# every model touches (inductive bias), the panels whose text discusses it, which lint checks
USED_AS = {"uses": "Used in", "examples": "Examples", "discussed": "Discussed in"}


def panel_text(a: dict) -> str:
    """Everything a panel says in words: caption, summary, reading, objective, note, and its
    step-through's captions, as plain lowercase text."""
    dg = a.get("diagram") or {}
    say = list(dg.get("say") or [])
    if isinstance(dg.get("flow"), list):
        say += [st.get("say", "") for st in dg["flow"]]
    parts = ("caption", "summary", "read", "objective_math", "note")
    return (
        " ".join(str(a.get(k) or "") for k in parts).replace("`", "").lower()
        + " "
        + (" ".join(map(str, say)).replace("`", "").lower())
    )


def mentions(e: dict, a: dict) -> bool:
    """Whether a panel's text names a glossary entry, in any of the forms it links by."""
    text = panel_text(a)
    return any(
        re.search(r"(?<![\w-])" + re.escape(f.lower()) + r"(?![\w-])", text)
        for f in e.get("match") or [e["term"]]
    )


RELATIONS = {
    "kind_of": ("Kind of", "Kinds"),
    "builds_on": ("Builds on", "Led to"),
    "built_with": ("Often built with", "Often used in"),
}

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
    "hebbian",
    "competitive",
    "reward",
]

# What each objective optimizes, one line under the tag in the page's panel; a rule, where
# training follows an update rule rather than a loss. A panel may give its own
# `objective_math` (its landmark paper's form). Markup: `x` a variable (italic), _{...} subscript,
# ^{...} superscript.
OBJECTIVE_MATH = {
    "reward": (
        "maximize E[Σ_{`t`} γ^{`t`} `r`_{`t`}]: the expected rewards `r`_{`t`} over time steps "
        "`t`, discounted by γ < 1, each action `a` drawn from the policy π(`a` | `s`) in state `s`"
    ),
    "supervised": (
        "minimize loss(`f`(`x`), `y`) over labeled pairs (`x`, `y`), `f` the network; e.g. "
        "−log `p`(`y` | `x`), the negative log probability of the correct label"
    ),
    "next token": (
        "maximize Σ_{`t`} log `p`(`x`_{`t`} | `x`_{<`t`}): the probability of each token "
        "`x`_{`t`} given the tokens before it"
    ),
    "masked token": (
        "maximize Σ_{`i`∈`M`} log `p`(`x`_{`i`} | `x̃`), `M` the positions chosen for prediction "
        "(15%: most replaced by [MASK], some by a random token, some left as they are), `x̃` the "
        "corrupted input"
    ),
    "reconstruct": "minimize ‖`x` − `g`(`f`(`x`))‖², `f` the encoder, `g` the decoder",
    "denoise": (
        "minimize ‖`x` − `g`(`f`(`x̃`))‖², `x̃` a corrupted copy of `x`, `f` the encoder, "
        "`g` the decoder"
    ),
    "variational": (
        "maximize E_{`q`(`z`|`x`)}[log `p`(`x`|`z`)] − KL(`q`(`z`|`x`) ‖ `p`(`z`)): `q` "
        "encodes `x` to a code `z`, `p`(`x`|`z`) decodes it, and the KL term keeps codes "
        "near a prior `p`(`z`); a lower bound on log `p`(`x`)"
    ),
    "adversarial": (
        "min_{`G`} max_{`D`} E[log `D`(`x`)] + E[log(1 − `D`(`G`(`z`)))], `G` the generator "
        "turning noise `z` into samples, `D` the discriminator's probability that a sample "
        "is real"
    ),
    "contrastive": (
        "minimize −log [e^{`s`(`x`_{`i`}, `y`_{`i`})/`τ`} / Σ_{`j`} e^{`s`(`x`_{`i`}, "
        "`y`_{`j`})/`τ`}], `s` the similarity of two encodings, `τ` a temperature: each "
        "matched pair must score above the batch's mismatches, averaged over both directions "
        "(images to captions and captions to images)"
    ),
    "likelihood": (
        "maximize log `p`(`x`), the log probability the model gives each training example"
    ),
    "flow matching": (
        "minimize ‖`v`(`x`_{`t`}, `t`) − (`x`_{1} − `x`_{0})‖², `v` the network's velocity, "
        "`x`_{0} noise, `x`_{1} data, `x`_{`t`} = (1 − `t`)`x`_{0} + `t`·`x`_{1} the point "
        "a fraction `t` of the way, the target for the straight-line path (other paths give "
        "other targets)"
    ),
    "hebbian": (
        "rule: set `w`_{`i``j`} = Σ_{`p`} `x`_{`i`}^{`p`} `x`_{`j`}^{`p`}, `w`_{`i``j`} the "
        "weight between units `i` and `j`, `x`_{`i`}^{`p`} unit `i`'s value (±1) in stored "
        "pattern `p`, for `i` ≠ `j` (`w`_{`i``i`} = 0): units that agree get a stronger link"
    ),
    "competitive": (
        "rule: `w`_{`k`} += `η`·`h`(`k`, `k`*)·(`x` − `w`_{`k`}), `w`_{`k`} unit `k`'s "
        "weights, `k`* the unit closest to `x`, `η` a step size, `h` shrinking with "
        "distance from `k`* on the map"
    ),
}


def plain(text: str) -> str:
    """Text without its markup: `k` (a variable, drawn in italics) reads as k, x_{t} as xt."""
    out = str(text).replace("`", "")
    while True:
        new = re.sub(r"[_^]\{([^{}]*)\}", r"\1", out)
        if new == out:
            return out
        out = new


def prose_html(text: str) -> str:
    """Prose as HTML: escaped, with each `variable` in italics and _{...} and ^{...} as sub- and
    superscripts, as in the formulas."""
    out = re.sub(r"`([^`]+)`", r"<i>\1</i>", html.escape(str(text), quote=False))
    while True:
        new = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", out)
        new = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", new)
        if new == out:
            return out
        out = new


def math_html(text: str) -> str:
    """A formula line as HTML: escaped, with `x` in italics and _{...} and ^{...} as sub- and
    superscripts (nested ones too, innermost first), labeled "Trains to:", or "Update rule:"
    for a leading "rule:"."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<i>\1</i>", out)  # `x`: a variable, in italics
    while True:
        new = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", out)
        new = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", new)
        if new == out:
            break
        out = new
    out = out.replace("log <i>", "log\u00a0<i>")  # "log p(x)" never splits across lines
    if out.startswith("rule: "):
        return '<span class="k">Update rule:</span> ' + out[len("rule: ") :]
    return '<span class="k">Trains to:</span> ' + out


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
    arrive: float = None  # in the step-through, when it lights, as a share of the draw time


@dataclass
class Seg:
    style: str
    pts: list  # polyline used for drawing straight lines and for lint
    d: str = ""  # path data for connectors with bends
    ends: tuple = ()  # cells an end of this line meets (exactly GAP from the outline)
    opacity: float = 1.0
    arrow: bool = False
    near: tuple = ()  # cells of a group the line leaves from (exempt from clearance)
    src: tuple = ()  # for the page's step-through: the cells the line carries data from,
    dst: str = ""  # and the cell it carries it to, where its ends don't say (gates, feeds)
    late: bool = False  # in the step-through, part of an output's stem: lit with it (data-late)
    t0: float = None  # in the step-through, when it starts and ends drawing in, as shares of
    t1: float = None  # the draw time: a branch starts as the line it leaves reaches it
    tone: str = ""  # a glossary figure's colored line: "pull" (green) or "push" (red)


@dataclass
class Diagram:
    id: str
    cells: "OrderedDict[str, Cell]"
    groups: "OrderedDict[str, list]"
    segs: list = field(default_factory=list)
    dots: list = field(default_factory=list)
    stems: list = field(
        default_factory=list
    )  # (start, end, opacity, kind) stems drawn apart from a cell
    dot_dst: list = field(default_factory=list)  # the cell each gate's dot acts for
    stem_cells: list = field(default_factory=list)  # the output each of those stems belongs to
    steps: dict = field(default_factory=dict)  # cell -> its step in the data flow (flow_steps)
    seq: list = field(default_factory=list)  # a hand-written step-through: (from, to) cell sets
    say: list = field(default_factory=list)  # a line of text per hand-written step (glossary)
    alt: str = ""  # a figure's description, read by screen readers (`alt`)
    names: list = field(default_factory=list)  # (cells, letter): a row's name (see layout)
    hold: list = field(default_factory=list)  # per hand-written step: cells kept lit (clamped)
    slide: tuple = ()  # cells that move as one, with their lines: (keys, step, offset per step)
    ghosts: bool = False  # at rest, faint copies of the sliding cells at each place they go
    focus: dict = field(
        default_factory=dict
    )  # a glossary figure's subject, the rest faded at rest
    notes: list = field(
        default_factory=list
    )  # a glossary figure's braces and arrows, at the right


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


def rounded_extent(h: float, r: float, ux: float, uy: float) -> float:
    """Distance from the centre of a square of half-width h, corners rounded to radius r, to
    its outline in direction (ux, uy)."""
    ux, uy = abs(ux), abs(uy)
    t = h / max(ux, uy, 1e-9)  # where the sharp square's edge would be
    cx = cy = h - r
    px, py = ux * t, uy * t
    if px <= cx or py <= cy:  # the ray meets a straight edge
        return t
    # otherwise it meets the corner arc: solve |p - c| = r along the ray
    b = ux * cx + uy * cy
    return b + math.sqrt(max(b * b - (cx * cx + cy * cy - r * r), 0.0))


def extent(cell: Cell, ux: float = 1.0, uy: float = 0.0) -> float:
    """Distance from a cell's centre to its outline (or outer ring) in direction (ux, uy)."""
    spec = CELLS[cell.kind]
    shape = spec.get("shape", "circle")
    if shape == "square":  # a rounded square: its corners sit inside the square's bounds
        ring = ring_pad(cell) > 0
        h = half_of(spec, 7.0) + ring_pad(cell)
        return rounded_extent(h, 4.0 + 0.4 if ring else 2.5, ux, uy)
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
    """Inputs keep their stem below and outputs above whenever none of the cell's links leads
    that way (a link from the side, as flow matching's velocity, leaves it be). Otherwise (a ring, or a link leaving on the stem's side) the stem points
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
        if all(vx * default[0] + vy * default[1] < 0.05 for vx, vy in vecs):  # none on its side
            c.mark_dir = default
            continue
        sx, sy = sum(v[0] for v in vecs), sum(v[1] for v in vecs)
        if math.hypot(sx, sy) < 0.35:
            c.mark_dir = default
        elif abs(sx) >= abs(sy) - 1e-9:  # a tie goes sideways, clear of the rows above and below
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
    """Lay out a diagram, then lay it out again moved sideways so the whole drawing, its
    marks and lines included, sits centred in the panel."""
    d = layout(arch_id, spec)
    lo, hi = x_span(d)
    shift = W / 2 - (lo + hi) / 2
    if abs(shift) > 0.05:
        d = layout(arch_id, dict(spec, shift=shift))
    d.alt = spec.get("alt", "")  # what a screen reader hears for the figure
    flow = spec.get("flow", True)  # optional: true (the default) follows the lines
    d.say = list(spec.get("say", []))  # a panel's caption for each step of a flow it doesn't write
    if isinstance(flow, list):
        d.seq = [
            (cells_of(d, st.get("from", [])), cells_of(d, st["to"]), st.get("within", False))
            for st in flow
        ]
        if any("say" in st for st in flow):  # a written flow's captions sit on its steps
            d.say = [st.get("say", "") for st in flow]
        d.hold = [cells_of(d, st.get("hold", [])) for st in flow]
        if spec.get("focus"):  # what the figure is about: at rest, everything else is faded
            fo = spec["focus"]
            d.focus = dict(
                cells=cells_of(d, fo.get("cells", [])),
                styles=set(fo.get("styles", [])),
                into=fo.get("into", False),
                lines=fo.get(
                    "lines", True
                ),  # false: every line fades at rest, only the cells stand out
            )
        d.notes = spec.get("notes", [])
        # one filter moved along, step by step, rather than drawn at each place
        if spec.get("slide"):
            sl = spec["slide"]
            d.slide = (cells_of(d, sl["cells"]), float(sl["by"]), list(sl["at"]))
            d.ghosts = bool(sl.get("ghosts"))
    elif flow:
        d.steps = flow_steps(d)
    d.segs = [s for s in d.segs if s.style != "_flow"]  # flow-only edges, never drawn
    return d


def compares(d: Diagram, s: Seg) -> bool:
    """A dashed comparison between two cells side by side (Siamese's and CLIP's two
    embeddings): it runs both ways, so it carries no data from one to the other."""
    return (
        s.style == "cross"
        and len(s.ends) == 2
        and abs(d.cells[s.ends[0]].y - d.cells[s.ends[1]].y) < 0.5
    )


SEQ_ATTR = ' data-seq=""'  # marks a card's drawing whose step-through is hand-written


def cells_of(d: Diagram, refs) -> set:
    """The cells a step of a hand-written flow names: a group id, a [group, index] cell, or
    a list of these."""
    if isinstance(refs, str) or (
        isinstance(refs, list) and len(refs) == 2 and isinstance(refs[1], int)
    ):
        refs = [refs]
    out = set()
    for r in refs:
        k = ref(d, r)
        out |= set(d.groups[k]) if k in d.groups else {k}
    return out


def seq_marks(d: Diagram, keys: tuple) -> str:
    """For a hand-written flow, the steps at which a part shows: data-on where it is one of
    the cells updated (or a line into one, or with `within`, a line between two of them),
    data-mid where it is a cell read from (a step's `hold` cells stay on, without their lines), and data-rev where a line carries data against the
    way it is drawn (from its second end to its first), so the page draws it in from there.
    In a step with `within`, data-lag gives each part its place in the cascade up the cells
    it updates ("step:slot"): a cell lights in its slot, a line draws in the slot before the
    cell it reaches, so a column lights layer by layer rather than all at once."""
    on, mid, rev, lag = [], [], [], []
    for i, (src, dst, within) in enumerate(d.seq):
        slots = seq_slots(d, dst) if within else {}
        if len(keys) == 1:
            if keys[0] in dst or (d.hold and keys[0] in d.hold[i]):
                on.append(i)
                if slots.get(keys[0]):
                    lag.append(f"{i}:{slots[keys[0]]}")
            elif keys[0] in src:
                mid.append(i)
        elif len(keys) == 2 and d.slide and (set(keys) & d.slide[0]):
            if set(keys) & d.slide[0] & dst:  # a sliding filter's lines show with it
                on.append(i)
        elif len(keys) == 2:
            a, b = keys
            joins = (a in src and b in dst) or (b in src and a in dst)
            if joins or (within and a in dst and b in dst):
                on.append(i)
                if a in dst and b in src:
                    rev.append(i)
                k = max(slots.get(a, 0) if a in dst else 0, slots.get(b, 0) if b in dst else 0) - 1
                if k > 0:
                    lag.append(f"{i}:{k}")
    out = f' data-on="{" ".join(map(str, on))}"'
    out += f' data-mid="{" ".join(map(str, mid))}"' if mid else ""
    out += f' data-lag="{" ".join(lag)}"' if lag else ""
    return out + (f' data-rev="{" ".join(map(str, rev))}"' if rev else "")


def seq_slots(d: Diagram, dst: set) -> dict:
    """Each cell's place in the cascade up the cells one hand-written step updates: 0 for
    one nothing in the step feeds, else one more than the latest cell in the step it reads."""
    into = {k: [a for a, b in flow_edges(d) if b == k and a in dst] for k in dst}
    slots = {}

    def slot(k, seen=()):
        if k not in slots:
            slots[k] = (
                0 if k in seen else max((slot(a, seen + (k,)) + 1 for a in into[k]), default=0)
            )
        return slots[k]

    for k in dst:
        slot(k)
    return slots


def flow_edges(d: Diagram) -> list:
    """The diagram's data flow as (from cell, to cell) pairs: every line but the return
    loops, which run the network again rather than carry data within one pass, and the
    side-by-side comparisons, which go both ways."""
    out = []
    for s in d.segs:
        if s.style == "loop" or compares(d, s):
            continue
        dst = s.dst or (s.ends[1] if len(s.ends) == 2 else "")
        src = s.src or ((s.ends[0],) if len(s.ends) == 2 else ())
        out += [(a, dst) for a in src if dst and a != dst]
    return out


def flow_steps(d: Diagram) -> dict:
    """Each cell's step in one pass of data, for the page's step-through: the length of the
    longest path of lines into it, so a cell lights up once everything it reads has. Empty
    when the drawing has no direction to follow: a cycle, or units that are both visible and
    updated (Hopfield and Boltzmann machines settle as a whole)."""
    if any(c.kind == "backfed" for c in d.cells.values()):
        return {}
    edges = flow_edges(d)
    into = {k: [] for k in d.cells}
    for a, b in edges:
        if a in into and b in into:
            into[b].append(a)
    steps, busy = {}, set()

    def step(k):
        if k in steps:
            return steps[k]
        if k in busy:
            raise ValueError("cycle")
        busy.add(k)
        steps[k] = max((step(a) + 1 for a in into[k]), default=0)
        busy.discard(k)
        return steps[k]

    try:
        for k in d.cells:
            step(k)
    except ValueError:
        return {}
    # Some cells nothing feeds arrive just before they are first read, rather than at the
    # start: a sequence's inputs, one per time step (those read by cells joined by state
    # arrows), and a lone extra input such as the noise a sampler adds at its step. A row of
    # inputs read all at once (a picture, a noise vector) stays at the start.
    out_of = {k: [b for a, b in edges if a == k and b in steps] for k in d.cells}
    chained = {k for s in d.segs if s.style == "state" and len(s.ends) == 2 for k in s.ends}
    group_of = {k: g for g, ks in d.groups.items() for k in ks}
    for k in d.cells:
        if into[k] or not out_of[k]:
            continue
        if all(b in chained for b in out_of[k]) or len(d.groups[group_of[k]]) == 1:
            steps[k] = max(0, min(steps[b] for b in out_of[k]) - 1)
    return steps if max(steps.values(), default=0) > 0 else {}


def flow_count(d: Diagram) -> int:
    """How many steps the page's step-through shows: one per step of the pass, and one more
    for a return loop or a side-by-side comparison, which light once the pass is done."""
    if d.seq:
        return len(d.seq)
    if not d.steps:
        return 0
    last = max(d.steps.values())
    after = [last + 1 for s in d.segs if s.style == "loop"]  # the return line, after the pass
    after += [max(d.steps[k] for k in s.ends) + 1 for s in d.segs if compares(d, s)]
    return max([last] + after) + 1


def x_span(d: Diagram) -> tuple:
    """Leftmost and rightmost points of everything drawn: cells, their stems and ticks, and
    every line, curve, and arrow."""
    xs = []
    for c in d.cells.values():
        xs += [c.x - extent(c, -1.0, 0.0), c.x + extent(c, 1.0, 0.0)]
        for p, q in mark_lines(c):
            xs += [p[0], q[0]]
    for s in d.segs:
        xs += [p[0] for p in s.pts]
    return min(xs), max(xs)


def layout(arch_id: str, spec: dict) -> Diagram:
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
        # `shift` moves every cell sideways; build_diagram sets it to centre the drawing
        pos = [(x + spec.get("shift", 0.0), y + PAD_Y) for x, y in pos]
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
                # false: no group marks; a list: per cell (a sampler's in-between points)
                marks=(
                    r["marks"][k] if isinstance(r.get("marks"), list) else r.get("marks", True)
                ),
            )
            keys.append(key)
        groups[gid] = keys
        order.append((gid, r))

    d = Diagram(arch_id, cells, groups)
    for gid, r in order:  # `name`: a letter for the row (see the YAML's notes): at the left of
        if r.get("name"):  # its first cell, or with `name_at: between`, centered among its cells
            at = r.get("name_at")  # between: centered among them; each: at the left of every cell
            if at == "each":
                d.names += [([k], r["name"]) for k in groups[gid]]
            else:
                d.names.append((groups[gid] if at == "between" else groups[gid][:1], r["name"]))
    prev = None
    for gid, r in order:
        if "connect" in r:
            src = r.get("from", prev)
            connect(d, groups[src], groups[gid], r["connect"], r.get("style", "link"), r)
        prev = gid
    for link in spec.get("links", []):
        n = len(d.segs)
        connect(
            d,
            groups[link["from"]],
            groups[link["to"]],
            link.get("rule", "full"),
            link.get("style", "link"),
            link,
        )
        for s in d.segs[n:]:  # a glossary figure may color a link: pulled together, pushed apart
            s.tone = link.get("tone", "")
    prev = None
    for gid, r in order:
        if "chain" in r:
            chain(d, groups[gid], r["chain"])
            if r.get("gate"):
                also = groups[r["gate_also"]] if r.get("gate_also") else None
                gates(d, groups[r.get("from", prev)], groups[gid], r["gate"], also)
        prev = gid
    for a in spec.get("arcs", []):
        arc(d, a)
    for lp in spec.get("loops", []):
        loop(d, lp)
    for ar in spec.get("arrows", []):
        arrow(d, ar)
    for dp in spec.get("drops", []):
        drop(d, dp)
    for fd in spec.get("feeds", []):
        feed(d, fd)
    assign_mark_dirs(d)
    for gid, r in order:  # `fed`: inputs that were earlier outputs, their stems drawn as arrows
        for i in r.get("fed", []):
            fed_stem(d, groups[gid][i])
    return d


def fed_stem(d: Diagram, key: str):
    """An input that was an earlier output, fed back in (GPT's earlier tokens): its stem drawn
    as an arrow into it, in the return arrow's style, in place of the plain stem. Part of the
    input's stem, so it lights with the input (late)."""
    c = d.cells[key]
    _, q = mark_lines(c)[0]  # the stem's far end
    ux, uy = c.mark_dir or (0.0, 1.0)
    e = extent(c, ux, uy) + GAP  # its tip GAP from the outline, as every arrow's
    p = (c.x + ux * e, c.y + uy * e)
    c.marks = False
    d.segs.append(
        Seg("loop", [q, p], ends=(key,), opacity=c.opacity, arrow=True, dst=key, late=True)
    )


def pairs_for(d: Diagram, rule: str, src: list, dst: list, params: dict) -> list:
    cells, ns, nd = d.cells, len(src), len(dst)
    name, k = rule, 1
    if ":" in rule:
        name, k = rule.split(":")[0], int(rule.split(":")[1])
    if name == "full":
        return [(i, j) for j in range(nd) for i in range(ns)]
    if name == "causal":
        return [(i, j) for j in range(nd) for i in range(ns) if i <= j]
    if name == "pairs":
        return [tuple(p) for p in params["pairs"]]
    # a convolution's window, as arithmetic: an unpadded one reads k neighbours, so the row
    # shrinks by k - 1; a transposed one spreads each cell over k, so it grows by k - 1
    if name == "window":
        if nd != ns - k + 1:
            raise ValueError(f"{d.id}: window:{k} takes {ns} cells to {ns - k + 1}, not {nd}")
        return [(j + i, j) for j in range(nd) for i in range(k)]
    if name == "same":  # a padded convolution: the row keeps its size, each reads the k around it
        if nd != ns:
            raise ValueError(f"{d.id}: same:{k} keeps {ns} cells, not {nd}")
        h = k // 2
        return [(i, j) for j in range(nd) for i in range(max(0, j - h), min(ns, j + h + 1))]
    if name == "spread":
        if nd != ns + k - 1:
            raise ValueError(f"{d.id}: spread:{k} takes {ns} cells to {ns + k - 1}, not {nd}")
        return [(i, i + o) for i in range(ns) for o in range(k)]
    if name == "complete":
        return [(i, j) for i in range(ns) for j in range(i + 1, ns)]
    if name == "one":
        if ns == nd:
            return [(i, i) for i in range(ns)]
        out = []
        for j in range(nd):
            xj = cells[dst[j]].x
            # nearest by x; ties go to the source farther from the centre (keeps it symmetric)
            i = min(
                range(ns),
                key=lambda i: (round(abs(cells[src[i]].x - xj), 3), -abs(cells[src[i]].x - W / 2)),
            )
            out.append((i, j))
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


def gates(d: Diagram, src: list, dst: list, mode=True, also: list | None = None):
    """Dotted line from each input to the state arrow arriving at its position. With mode
    "recurrent" (GRU) the gate is also set by the previous state: a second dotted line
    leaves that cell at 45 degrees and comes down onto the dot from above, with HOOK_BEND as
    its bend. A selective SSM's gate (mode true) reads the input alone. With `also` (a row
    above, `gate_also`), a dotted line leaves that row's previous cell at 45 degrees and comes
    straight down onto the dot: LSTM's gates read the previous hidden state, a row of its own."""
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
        # in the step-through the gate's lines reach the dot as the state arrow does, halfway
        # along it, so nothing passes the dot before what sets it has arrived
        d.segs.append(
            Seg(
                "gate",
                [p0, p1],
                ends=(S.key,),
                opacity=S.opacity,
                src=(S.key,),
                dst=B.key,
                t0=0.0,
                t1=0.5,
            )
        )
        d.dots.append((mx, my, 1.0))
        d.dot_dst.append(B.key)
        if mode == "recurrent":
            sx = 1 if B.x > A.x else -1
            s = extent(A, sx * DIAG, -DIAG) + GAP
            q0 = (A.x + sx * DIAG * s, A.y - DIAG * s)
            q2 = (mx, my - DOT - GAP)
            q1 = (mx, q0[1] - abs(mx - q0[0]))
            path, samples = rounded_path([q0, q1, q2], HOOK_BEND)
            d.segs.append(
                Seg(
                    "gate",
                    samples,
                    d=path,
                    ends=(A.key,),
                    opacity=A.opacity,
                    src=(A.key,),
                    dst=B.key,
                    t0=0.0,
                    t1=0.5,
                )
            )
        if also:  # leaves the cell at 45 degrees, then comes straight down onto the dot
            Hc = d.cells[sorted(also, key=lambda k: d.cells[k].x)[idx - 1]]
            sx = 1 if B.x > A.x else -1
            s = extent(Hc, sx * DIAG, DIAG) + GAP
            q0 = (Hc.x + sx * DIAG * s, Hc.y + DIAG * s)
            q2 = (mx, my - DOT - GAP)
            q1 = (mx, q0[1] + abs(mx - q0[0]))
            path, samples = rounded_path([q0, q1, q2], HOOK_BEND)
            d.segs.append(
                Seg(
                    "gate",
                    samples,
                    d=path,
                    ends=(Hc.key,),
                    opacity=Hc.opacity,
                    src=(Hc.key,),
                    dst=B.key,
                    t0=0.0,
                    t1=0.5,
                )
            )


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
    # `solid`: drawn at full strength even into a faded cell (GPT's next input, not yet run)
    op = 1.0 if spec.get("solid") else min(A.opacity, B.opacity)
    d.segs.append(Seg(style, samples, d=path, ends=(ka, kb), opacity=op, arrow=arrow))


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
    in its own style (the output is fed back in and the network runs again). With
    `whole: true`, the whole row of the `from` cell is fed back to the whole row of the `to`
    cell instead (whole_loop); with `via` as well, the line passes through that cell on the way
    down the side (PPO's environment)."""
    if lp.get("whole"):
        whole_loop(d, lp)
    else:
        elbow(d, lp, "loop", True, "right")


FEED_LEN = 2 * STEM  # a line that feeds a whole row: from the cell's edge (less GAP), twice a stem
# a whole-row loop's way back, in shares of the draw time: down to the bottom line by LOOP_DOWN,
# then along it beneath the inputs by LOOP_END (past 1: it runs on into the step's pause)
LOOP_DOWN, LOOP_END = 0.4, 1.45


def whole_loop(d: Diagram, lp: dict):
    """The whole output row fed back to the whole input row, as the sampler does each step: a
    line up from every output to a line above them, down the side (outside any skips), and
    beneath the inputs, with an arrow up into each, FEED_LEN long like ViT's patch feed. The lines take the place of the rows' stems; each output keeps its stem past the top
    line, since the result leaves on the last step, and each input its stem past the bottom
    line, since the first step's data comes in from outside."""
    ka, kb = ref(d, lp["from"]), ref(d, lp["to"])
    ga = next(g for g in d.groups.values() if ka in g)
    gb = next(g for g in d.groups.values() if kb in g)
    outs = sorted((d.cells[k] for k in ga), key=lambda c: c.x)
    ins = sorted((d.cells[k] for k in gb), key=lambda c: c.x)
    for c in outs + ins:
        c.marks = False
    y_top = min(c.y - extent(c, 0.0, -1.0) for c in outs) - GAP - FEED_LEN
    y_bot = max(c.y + extent(c, 0.0, 1.0) for c in ins) + GAP + FEED_LEN
    x_run = max(c.x for c in outs + ins) + REACH
    skips = [x for s in d.segs if s.style == "skip" for x, _ in s.pts]
    if skips and max(skips) > x_run - 6:
        x_run = max(skips) + 8.0
    vias = lp.get("via") or []
    if vias and not isinstance(vias[0], list):
        vias = [vias]  # one cell, or a list of them in order down the side
    vias = [d.cells[ref(d, v)] for v in vias]
    if vias:  # down the side into each cell in turn, then on from the last to the inputs
        near = tuple(ga) + tuple(gb)
        y_prev, x_prev = y_top, outs[0].x
        for i, via in enumerate(vias):
            via.x, via.marks = x_run, False
            y_in = via.y - extent(via, 0.0, -1.0) - GAP
            if i == 0:  # along the top and down the side
                pts, ends = [(x_prev, y_prev), (x_run, y_prev), (x_run, y_in)], (via.key,)
            else:  # on down from the cell before
                pts, ends = [(x_run, y_prev), (x_run, y_in)], (vias[i - 1].key, via.key)
            path, samples = rounded_path(pts, BEND)
            src = tuple(ga) if i == 0 else (vias[i - 1].key,)
            d.segs.append(  # lights with the cell it reaches, which follows the one before
                Seg("loop", samples, d=path, near=near, ends=ends, arrow=True, dst=via.key)
            )
            d.segs.append(Seg("_flow", [], src=src, dst=via.key))
            y_prev = via.y + extent(via, 0.0, 1.0) + GAP
        via = vias[-1]
        back, near_back, ends_back = [(x_run, y_prev)], near, (via.key,)
    else:
        back, near_back, ends_back = (
            [(outs[0].x, y_top), (x_run, y_top)],
            tuple(ga) + tuple(gb),
            (),
        )
    # the way back in two parts: down to the bottom line, quickly, then along it beneath the
    # inputs at the pace of ViT's patch feed, each input's arrowhead showing as it passes
    x_split = x_run - 2 * BEND  # past the corner, so the bend keeps its radius
    path, samples = rounded_path(back + [(x_run, y_bot), (x_split, y_bot)], BEND)
    d.segs.append(
        Seg("loop", samples, d=path, near=near_back, ends=ends_back, t0=0.0, t1=LOOP_DOWN)
    )
    d.segs.append(
        Seg(
            "loop",
            [(x_split, y_bot), (ins[0].x, y_bot)],
            near=near_back,  # it ends at neither cell: it carries on the line that does
            t0=LOOP_DOWN,
            t1=LOOP_END,
        )
    )
    reach = {
        c.key: LOOP_DOWN + (LOOP_END - LOOP_DOWN) * (x_split - c.x) / (x_split - ins[0].x)
        for c in ins
    }
    for c in outs:
        bot = c.y - extent(c, 0.0, -1.0) - GAP
        d.segs.append(  # leaves with its output, which the step-through lights it with (dst)
            Seg(
                "loop",
                [(c.x, bot), (c.x, y_top)],
                ends=(c.key,),
                opacity=c.opacity,
                dst=c.key,
                late=True,  # with the stem above it, part of the output's stem: lit with the output
            )
        )
        d.stems.append(
            ((c.x, y_top - MARK_GAP), (c.x, y_top - MARK_GAP - STEM), c.opacity, c.kind)
        )
        d.stem_cells.append(c.key)  # lit in its output's step, not the loop's
    # the lines up into the inputs belong to the inputs' stems, as the risers to the outputs':
    # they light with the inputs (the data comes in from outside too); their arrowheads show
    # only once the way back has drawn in along the bottom line to meet them
    for c in ins:  # and each input keeps its stem past the bottom line, as data also comes in
        d.stems.append(
            ((c.x, y_bot + MARK_GAP), (c.x, y_bot + MARK_GAP + STEM), c.opacity, c.kind)
        )
        d.stem_cells.append(c.key)
    for c in ins:
        top = c.y + extent(c, 0.0, 1.0) + GAP
        d.segs.append(  # with the stem below it, part of the input's stem: lit with the input
            Seg("loop", [(c.x, y_bot), (c.x, top)], ends=(c.key,), dst=c.key, late=True)
        )
        t = reach[c.key]
        d.segs.append(  # its arrowhead, which shows as the way back passes beneath it
            Seg(
                "loop",
                [(c.x, top + 1.0), (c.x, top)],
                ends=(c.key,),
                arrow=True,
                t0=t,
                t1=t + 0.03,
            )
        )


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
            src=ends_a or near_a,
            dst=(ends_b or near_b)[0],
        )
    )


def drop(d: Diagram, dp: dict):
    """An arrow that leaves a state arrow partway along and drops straight down into a cell
    beneath it, with no dot where it branches (that would read as a gate): the state handed
    on is also read below (Bahdanau's attention reads the previous decoder state)."""
    A, B = d.cells[ref(d, dp["along"][0])], d.cells[ref(d, dp["along"][1])]
    T = d.cells[ref(d, dp["to"])]
    t = (T.x - A.x) / (B.x - A.x)
    y = A.y + t * (B.y - A.y)
    top = T.y - extent(T, 0.0, -1.0) - GAP
    d.segs.append(
        Seg(
            dp.get("style", "state"),
            [(T.x, y), (T.x, top)],
            ends=(T.key,),
            near=(A.key, B.key),
            arrow=True,
            src=(A.key,),
            dst=T.key,
        )
    )


def feed(d: Diagram, fd: dict):
    """An arrow from a whole group that runs beneath a row of cells, just below their stems,
    and ends under the last of them: one input handed to every cell of the row (an image's
    patches to the patch tokens)."""
    src = [d.cells[k] for k in d.groups[fd["from"]]]
    row = sorted((d.cells[k] for k in d.groups[fd["to"]]), key=lambda c: c.x)
    y = max(c.y for c in row) + R + MARK_GAP + STEM + fd.get("drop", 3.0)
    x0 = max(c.x + extent(c, 1.0, 0.0) for c in src) + GAP
    x1 = row[-1].x + fd.get("past", 0.0)
    branches = fd.get("branches", False)  # a short arrow up from the line into each cell
    if branches:
        y = max(c.y + extent(c, 0.0, 1.0) for c in row) + GAP + FEED_LEN
    # the step-through draws it as one flow: each branch leaves the line as the line reaches it
    up = [y - (c.y + extent(c, 0.0, 1.0) + GAP) for c in row] if branches else [0.0]
    total = (x1 - x0) + max(up)
    d.segs.append(
        Seg(
            fd.get("style", "state"),
            [(x0, y), (x1, y)],
            near=tuple(d.groups[fd["from"]]),  # the row it runs beneath keeps its stems
            arrow=fd.get("head", True) and not branches,
            src=tuple(d.groups[fd["from"]]),
            dst=row[0].key,
            t0=0.0,
            t1=(x1 - x0) / total,
        )
    )
    for c, u in zip(row, up if branches else [0.0] * len(row)):
        c.arrive = (c.x - x0 + u) / total
    if not branches:  # one line for the whole row: it hands data to every cell
        for c in row[1:]:
            d.segs.append(Seg("_flow", [], src=tuple(d.groups[fd["from"]]), dst=c.key))
    if branches:
        for c in row:
            top = c.y + extent(c, 0.0, 1.0) + GAP
            d.segs.append(
                Seg(
                    fd.get("style", "state"),
                    [(c.x, y), (c.x, top)],
                    ends=(c.key,),
                    arrow=True,
                    src=tuple(d.groups[fd["from"]]),
                    dst=c.key,
                    t0=(c.x - x0) / total,
                    t1=(c.x - x0 + y - top) / total,
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


def text_width(
    text: str, role: str, size: float, weight: int = 400, italic: bool = False
) -> float:
    """Measure with the real font when Pillow and the font are available."""
    text = plain(text)  # a `variable`'s backticks are markup, not ink
    style = {300: "Light", 400: "Regular", 500: "Medium", 700: "Bold"}[weight]
    if italic:
        style = "Italic" if style == "Regular" else style + " Italic"
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
    """The day the poster last changed. The build sets it (see chart_dates); an `updated` in
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


def page_updated_date(doc: dict):
    """The day the page last changed, which its footer prints: the build sets it apart from
    the poster's (see chart_dates), so a change to the page alone leaves the poster's date."""
    return doc.get("page_updated") or updated_date(doc)


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


def live_weights(d: Diagram, seed: int) -> dict:
    """Weights for the primer's live network, one per line, drawn from a fixed seed (chosen so
    the two outputs answer the inputs differently) and rounded to a tenth."""
    import random

    r = random.Random(seed)
    out = {}
    for s in d.segs:
        if len(s.ends) == 2:
            out[tuple(s.ends)] = round(r.gauss(0, 2.2), 1)
    return out


def primer_live(d: Diagram) -> str:
    """The controls under the primer's live network: a slider per input, the outputs' values,
    and a button for new weights; then a target per output, the loss, and a button that trains
    one step toward the targets."""
    ins = sorted((c for c in d.cells.values() if c.kind == "input"), key=lambda c: c.x)
    sliders = "".join(
        # named without its shown value, which the slider itself reports (so it isn't read twice)
        f'<label><span>Input {i + 1}</span><input type="range" min="0" max="1" step="0.01" '
        f'value="0.5" data-c="{c.key}" aria-label="Input {i + 1}"><output aria-hidden="true">0.50</output></label>'
        for i, c in enumerate(ins)
    )
    outs = sorted((c for c in d.cells.values() if c.kind == "output"), key=lambda c: c.x)
    targets = "".join(  # a target per output, for training; spread apart so there is work to do
        f'<label><span>Target {i + 1}</span><input type="range" min="0" max="1" step="0.01" '
        f'value="{0.9 if i % 2 == 0 else 0.1}" data-t="{c.key}" aria-label="Target {i + 1}">'
        f'<output aria-hidden="true">{0.9 if i % 2 == 0 else 0.1:.2f}</output></label>'
        for i, c in enumerate(outs)
    )
    # the readout first, right under the drawing it explains, so a tap on a phone shows its
    # numbers in view rather than below the sliders
    return (
        '<div class="p-live-ctl">'
        '<p class="p-read" aria-live="polite">Point at a unit or a line to see its numbers.</p>'
        f'{sliders}<p class="p-outs"><span>Outputs</span> '
        '<output class="p-out"></output>'
        '<button type="button" class="p-new">Randomize weights</button></p>'
        f"{targets}"
        '<p class="p-loss"><span>Loss</span> <output class="p-loss-v"></output>'
        '<span class="p-loss-bar" aria-hidden="true"><span></span></span>'
        '<button type="button" class="p-train">Train one step</button></p>'
        '<p class="p-trained" aria-live="polite"></p></div>'
    )


PRIMER_FLOW = (  # a primer figure's Step through button and stop, as on the popup's drawing
    '<div class="d-flowctl" data-state="idle"><button class="d-stop" type="button" '
    'aria-label="Stop" title="Stop"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" '
    'width="10" height="10" rx="1.5"/></svg></button>'
    '<button class="d-back" type="button" aria-label="Previous step" title="Previous step">'
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="2.5" height="12" rx="1"/>'
    '<path d="M18 6v12l-8.5-6z"/></svg></button>'
    '<button class="d-play" type="button"><svg '
    'class="i-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z"/></svg>'
    '<svg class="i-pause" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="6" width="4" '
    'height="12" rx="1"/><rect x="13.5" y="6" width="4" height="12" rx="1"/></svg>'
    "<span>Step through</span></button>"
    '<button class="d-fwd" type="button" aria-label="Next step" title="Next step">'
    '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="15.5" y="6" width="2.5" height="12" rx="1"/>'
    '<path d="M6 6v12l8.5-6z"/></svg></button>'
    '<button class="d-rate" type="button" aria-label="Step-through speed: 1×" '
    'title="Step-through speed">1×</button></div>'
)


FIGURE_SCALE = 1.6  # a primer or glossary figure's pixels per unit of the panels' drawing


def figure_notes(d: Diagram, marker: str) -> tuple:
    """A glossary figure's notes, set at the right of the drawing: a brace beside rows that
    names them as one part (an encoder), or an arrow along rows that says which way something
    runs (the error passed back). Returns the SVG and the notes' right edge (0 without notes)."""
    if not d.notes:
        return "", 0.0
    right = max(c.x + extent(c, 1.0, 0.0) for c in d.cells.values())
    right = max([right] + [p[0] for s in d.segs for p in s.pts])
    x = right + 12.0

    def row_y(r):
        # a row (its first cell), or one cell by [row, index]
        return (
            d.cells[d.groups[r][0]].y
            if isinstance(r, str) and r in d.groups
            else d.cells[ref(d, r)].y
        )

    out, widest = [], 0.0
    for n in d.notes:
        a, b = row_y(n["from"]), row_y(n["to"])
        lo, hi = min(a, b), max(a, b)
        if n.get("arrow"):  # from the first row toward the second, its head at the second
            step = 6.0 if b < a else -6.0
            line = (
                f'<line x1="{fmt(x)}" y1="{fmt(a - step)}" x2="{fmt(x)}" y2="{fmt(b + step)}" '
                f'marker-end="url(#{marker})"/>'
            )
        else:  # a brace: a line with short feet toward the drawing, its ends at the rows' cells
            inset = n.get("inset", 0.0)
            t, u = lo - R + inset, hi + R - inset
            line = f'<path d="M{fmt(x - 3)} {fmt(t)}H{fmt(x)}V{fmt(u)}H{fmt(x - 3)}"/>'
        out.append(line)
        out.append(
            f'<text x="{fmt(x + 6)}" y="{fmt((lo + hi) / 2 + 3)}" stroke="none">'
            f'{html.escape(n["label"])}</text>'
        )
        widest = max(widest, text_width(n["label"], "body", 9))
    svg = (
        '<g class="p-names p-notes" fill="none" stroke="currentColor" stroke-width="0.9">'
        + "".join(out)
        + "</g>"
    )
    return svg, x + 6 + widest


def slide_parts(drawing: str) -> str:
    """The markup of a drawing's sliding parts (data-slide): each line, and each cell's whole
    group, without the attributes that step through them."""
    out, i = [], 0
    while True:
        j = drawing.find(' data-slide=""', i)
        if j < 0:
            break
        a = drawing.rfind("<", 0, j)
        if drawing.startswith("<g", a):  # a cell: its group, to the matching close
            depth, k = 0, a
            while True:
                m = re.compile(r"<g[ >]|</g>").search(drawing, k)
                depth += 1 if m.group(0) != "</g>" else -1
                k = m.end()
                if depth == 0:
                    break
            out.append(drawing[a:k])
        else:
            k = drawing.index(">", j) + 1
            out.append(drawing[a:k])
        i = k
    return re.sub(r' data-(?:on|slide|mid|lag|rev|k|s|arrive)="[^"]*"', "", "".join(out))


def slide_ghosts(d: Diagram, drawing: str, marker: str) -> str:
    """At rest, a sliding figure's later places (convolution): faint copies of its sliding
    parts at each, and an arrow from the first toward the next, saying which way it goes.
    Hidden while stepping, when the parts themselves move."""
    keys, by, at = d.slide
    parts = slide_parts(drawing)
    copies = "".join(
        f'<g transform="translate({fmt(by * a)} 0)">{parts}</g>'
        for a in sorted({a for a in at if a})
    )
    copies = (  # drawn as the lines are, which take their stroke from the group they sit in
        '<g fill="none" stroke="currentColor" stroke-width="0.8" stroke-linecap="round">'
        + copies
        + "</g>"
    )
    c = min((d.cells[k] for k in keys), key=lambda c: c.y)  # the slider itself, above its lines
    x1, x2 = c.x + extent(c, 1.0, 0.0) + 3.0, c.x + by - extent(c, -1.0, 0.0) - 3.0
    arrow = (
        f'<line class="p-ghost-arrow" x1="{fmt(x1)}" y1="{fmt(c.y)}" x2="{fmt(x2)}" '
        f'y2="{fmt(c.y)}" stroke="currentColor" stroke-width="0.9" marker-end="url(#{marker})"/>'
    )
    return f'<g class="p-ghost">{copies}</g>{arrow}'


def primer_figure(
    d: Diagram,
    labels: list,
    live: int = 0,
    flow: bool = False,
    tips: dict | None = None,
    alt: str = "",
) -> str:
    """A panel's drawing for the primer, with its rows named at the left, bottom to top. With
    `live` (a seed), the drawing runs: each line carries a weight and each unit lights by its
    activity, as the inputs are set below it. With `flow`, it has its own Step through; with
    `tips` (a part's key, or its row's, -> a line of text), pointing at a part explains it."""
    rows = sorted({round(c.y, 1) for c in d.cells.values()}, reverse=True)
    lx = min(c.x - extent(c, -1.0, 0.0) for c in d.cells.values()) - 10.0  # just left of the cells
    names = "".join(
        f'<text x="{fmt(lx)}" y="{fmt(y + 3.5)}" text-anchor="end">{html.escape(t)}</text>'
        for y, t in zip(rows, labels)
    )
    # the figure is cropped to what it draws (its row names, the drawing, any notes) and set
    # at one scale, so every primer and glossary figure is drawn the same size and centers as
    # a whole
    lo, hi = x_span(d)
    named = max((text_width(t, "body", 9) for t in labels), default=0.0)
    x0 = min(lo, lx - named) - 4.0
    marker = f"arr-{d.id}"
    notes, right = figure_notes(d, marker)
    x1 = max(hi, right) + 4.0
    wider = f' style="width: {FIGURE_SCALE * (x1 - x0):.0f}px; max-width: 100%"'
    cls = "p-fig" + (" p-live" if live else "") + (" p-tips" if tips else "")
    say = json.dumps([prose_html(t) for t in d.say], ensure_ascii=False)
    data = (
        (' data-flow=""' if flow else "")
        + (f' data-say="{html.escape(say)}"' if flow and d.say else "")
        + (f' data-tips="{html.escape(json.dumps(tips, ensure_ascii=False))}"' if tips else "")
    )
    steps = (f' data-steps="{flow_count(d)}"' + (SEQ_ATTR if d.seq else "")) if flow else ""
    if flow and d.slide:
        steps += (
            f' data-slide-by="{fmt(d.slide[1])}" data-slide-at="{" ".join(map(str, d.slide[2]))}"'
        )
    drawing = render_diagram(
        d, marker, tags=flow, weights=live_weights(d, live) if live else None, named=bool(tips)
    )
    below = ""
    if flow or tips:
        below = (
            '<div class="p-fig-ctl">'
            + (PRIMER_FLOW if flow else "")
            + ('<p class="p-say" aria-live="polite"></p>' if flow and d.say else "")
            + (
                '<p class="p-read" aria-live="polite">Point at a part to see what it does.</p>'
                if tips
                else ""
            )
            + "</div>"
        )
    return (
        f'<figure class="{cls}"{data}><svg{wider} viewBox="{fmt(x0)} 0 '
        f'{fmt(x1 - x0)} {fmt(H)}" role="img" aria-label="{html.escape(alt or d.alt or ", ".join(labels))}"{steps}>'
        f'<defs>{marker_def(marker, "currentColor")}</defs><g class="p-names">{names}</g>'
        f"{slide_ghosts(d, drawing, marker) if d.ghosts else ''}{drawing}{notes}</svg>"
        + (primer_live(d) if live else "")
        + below
        + "</figure>"
    )


def primer_key(items: list, groups: dict | None = None) -> str:
    """A primer key: each cell or link listed, with the legend's symbol and a line on what it
    means. With `groups` (legend column -> a line on what it shares), the items fall under the
    legend's columns, each headed by its name and that line."""
    if not items:
        return ""

    def li(it):
        kind, name = ("cell", it["cell"]) if "cell" in it else ("link", it["link"])
        label = (CELLS if kind == "cell" else LINKS)[name]["label"]
        return (
            f"<li>{symbol_svg(kind, name)}<span><b>{html.escape(label)}.</b> "
            f"{prose_html(it['text'])}</span></li>"
        )

    if not groups:
        return f'<ul class="p-key">{"".join(li(it) for it in items)}</ul>'
    out = []
    for gid in list(GROUPS) + ["links"]:
        mine = [
            it
            for it in items
            if ("link" in it and gid == "links")
            or CELLS.get(it.get("cell"), {}).get("group") == gid
        ]
        if not mine:
            continue
        title = LINK_GROUP if gid == "links" else GROUPS[gid]["label"]
        out.append(
            f'<div class="p-group"><h4>{html.escape(title)}</h4>'
            + (f"<p>{prose_html(groups[gid])}</p>" if groups.get(gid) else "")
            + f'<ul class="p-key">{"".join(li(it) for it in mine)}</ul></div>'
        )
    return f'<div class="p-keys">{"".join(out)}</div>'


def figure_diagrams(doc: dict) -> dict:
    """The primer's own drawings (`figures`), built like panels but shown only in the primer."""
    return {
        k: build_diagram(k, spec)
        for k, spec in doc.get("figures", {}).items()
        if not {"plane", "chart", "bars"} & set(spec)
    }


PLANE_BOX = 136.0  # a plane figure's frame: a square this wide, in the drawing's units
PLANE_X0, PLANE_Y0 = 12.0, 143.0  # its bottom left corner


def plane_xy(p: dict, u: float, v: float) -> tuple:
    """A point of a plane figure's input space in the drawing's units: its `range` (each input
    -0.35 to 1.35 if not given, so 0 and 1 sit inside the frame) fills the frame."""
    lo, hi = p.get("range", [-0.35, 1.35])
    s = PLANE_BOX / (hi - lo)
    return PLANE_X0 + (u - lo) * s, PLANE_Y0 - (v - lo) * s


def plane_steps(spec: dict) -> list:
    """Each step of a plane figure: its `say` line and the parts it shows."""
    return spec.get("flow", [])


def plane_shape(k: int, x: float, y: float, size: float) -> str:
    """A plane figure's point in a group at (x, y), a circle `size` in radius; -1, not yet in a
    group, is a small hollow dot. Which group is told by where it lies, not by its mark: the
    territories' bounds, and the lines to its center as it joins one."""
    if k < 0:
        return f'<circle class="pl-none" cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(size * 0.55)}"/>'
    return f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(size)}"/>'


def plane_clip(poly: list, a: tuple, b: float) -> list:
    """A convex polygon cut to the half-plane a . p <= b (Sutherland-Hodgman)."""
    out = []
    f = lambda p: a[0] * p[0] + a[1] * p[1] - b
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        fp, fq = f(p), f(q)
        if fp <= 0:
            out.append(p)
        if fp * fq < 0:
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def plane_cells(centers: list, lo: float, hi: float) -> list:
    """Each center's region of the frame: the points nearer it than any other (Voronoi)."""
    cells = []
    for k, (cx, cy) in enumerate(centers):
        poly = [(lo, lo), (hi, lo), (hi, hi), (lo, hi)]
        for j, (dx, dy) in enumerate(centers):
            if j != k:
                poly = plane_clip(
                    poly, (2 * (dx - cx), 2 * (dy - cy)), dx * dx + dy * dy - cx * cx - cy * cy
                )
        cells.append(poly)
    return cells


def plane_carry(steps: list, key: str, rest):
    """Each step's value of `key`, carried on from the step before when it isn't given (the
    first given value before then); `rest` if no step gives one."""
    given = [st[key] for st in steps if key in st]
    cur, out = (given[0] if given else rest), []
    for st in steps:
        cur = st.get(key, cur)
        out.append(cur)
    return out


def plane_arrow(x1: float, y1: float, x2: float, y2: float, cls: str, extra: str = "") -> str:
    """An arrow from (x1, y1) to (x2, y2), its head a small filled triangle."""
    dx, dy = x2 - x1, y2 - y1
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    bx, by = x2 - 6.0 * ux, y2 - 6.0 * uy
    head = " ".join(
        f"{fmt(a)},{fmt(b)}"
        for a, b in ((x2, y2), (bx - 3.0 * uy, by + 3.0 * ux), (bx + 3.0 * uy, by - 3.0 * ux))
    )
    return (
        f'<g class="{cls}"{extra}><line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(bx)}" '
        f'y2="{fmt(by)}"/><polygon points="{head}"/></g>'
    )


def plane_figure(fid: str, spec: dict) -> str:
    """A glossary figure drawn on the plane of two inputs rather than as a network (XOR,
    cosine similarity, clustering). `points` are [u, v, value]: on or off (filled or hollow),
    or the index of the group they end in. Each step (`flow`) can show:
    a `line` that turns and shifts to each place listed ([center u, v, angle]), cases `wrong`
    (ringed), a lasting `keep` line with a label, a `band` (corners) where the answer is on,
    `names` label the points (a name, or [name, "left"]); a step's `pairs` ([from, to] point
    indexes) draw an arrow between two points from that step on. A step can also show
    a `paint` (each point's group, -1 for none), the `centers` (green) moved to new places,
    with `bounds: true` the lines between their territories and with `joins: true`, at each
    step that paints, a line from each point to its center and a ring on each that changed,
    the `arrow` turned to an angle, and a `note` (a readout in the corner). With `project`
    ({at, angle, from}) every point drops onto that line from that step on. At rest the figure
    shows how it ends: the points in their groups, the centers in place, the kept lines, the
    band, and the arrow where it starts."""
    p, steps = spec["plane"], plane_steps(spec)
    n = len(steps)
    lo, hi = p.get("range", [-0.35, 1.35])
    (fx0, fy0), (fx1, fy1) = plane_xy(p, lo, lo), plane_xy(p, hi, hi)  # fy0 the bottom
    clip = f"clip-{fid}"
    on_from = lambda i: " ".join(str(k) for k in range(i, n))
    parts = []
    if p.get("cross"):  # the axes through the origin
        ox, oy = plane_xy(p, 0, 0)
        parts.append(
            f'<path class="pl-cross" d="M{fmt(fx0)} {fmt(oy)}H{fmt(fx1)}M{fmt(ox)} {fmt(fy0)}'
            f'V{fmt(fy1)}"/>'
        )
    for i, st in enumerate(steps):  # the band first, beneath the lines and points
        if st.get("band"):
            pts = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in (plane_xy(p, *c) for c in st["band"]))
            parts.append(f'<polygon class="pl-band" points="{pts}" data-on="{on_from(i)}"/>')
    # the moving line (a perceptron's one line): drawn level through the middle, then turned
    # and shifted to each place it is shown at; hidden at rest
    moving = [(i, st["line"]) for i, st in enumerate(steps) if st.get("line")]
    if moving:
        mid = (lo + hi) / 2
        cx, cy = plane_xy(p, mid, mid)
        moves = []
        for at in plane_carry(steps, "line", None):
            x, y = plane_xy(p, at[0], at[1])
            moves.append(f"translate({fmt(x - cx)}px, {fmt(y - cy)}px) rotate({fmt(at[2])}deg)")
        half = PLANE_BOX
        parts.append(
            f'<g clip-path="url(#{clip})"><line class="pl-line" x1="{fmt(cx - half)}" y1="{fmt(cy)}" '
            f'x2="{fmt(cx + half)}" y2="{fmt(cy)}" data-on="{" ".join(str(i) for i, _ in moving)}" '
            f'data-gone="" data-move="{"|".join(moves)}" '
            f'style="transform-origin: {fmt(cx)}px {fmt(cy)}px"/></g>'
        )
    for i, st in enumerate(steps):
        k = st.get("keep")
        if not k:
            continue
        (x1, y1), (x2, y2) = plane_xy(p, *k["from"]), plane_xy(p, *k["to"])
        parts.append(
            f'<g data-on="{on_from(i)}"><line class="pl-line" x1="{fmt(x1)}" y1="{fmt(y1)}" '
            f'x2="{fmt(x2)}" y2="{fmt(y2)}" clip-path="url(#{clip})"/>'
            + (
                f'<text class="pl-label" x="{fmt(x2 + k.get("dx", 3))}" y="{fmt(y2 + k.get("dy", 3))}">'
                f'{html.escape(k["label"])}</text>'
                if k.get("label")
                else ""
            )
            + "</g>"
        )
    ox, oy = plane_xy(p, 0, 0)
    for vec in p.get("vectors", []):  # fixed arrows from the origin: shown from step `from` on
        x, y = plane_xy(p, *vec["to"])
        u, v = vec["to"]
        if vec.get("guides") is not None:  # dashed to both axes, each number where it lands
            parts.append(
                f'<g class="pl-guides" data-on="{on_from(vec["guides"])}">'
                f'<path d="M{fmt(x)} {fmt(y)}V{fmt(oy)}M{fmt(x)} {fmt(y)}H{fmt(ox)}"/>'
                f'<text class="pl-label" x="{fmt(x)}" y="{fmt(oy + 10)}" text-anchor="middle">{u:g}</text>'
                f'<text class="pl-label" x="{fmt(ox - 4)}" y="{fmt(y + 3)}" text-anchor="end">{v:g}</text>'
                "</g>"
            )
        parts.append(
            plane_arrow(ox, oy, x, y, "pl-vec", f' data-on="{on_from(vec.get("from", 0))}"')
        )
        if vec.get("label"):
            parts.append(
                f'<text class="pl-note" x="{fmt(x + vec.get("dx", 4))}" '
                f'y="{fmt(y + vec.get("dy", 0))}">{html.escape(vec["label"])}</text>'
            )
    arrow = p.get("arrow")  # an arrow from the origin that turns: drawn where it starts
    if arrow:
        a0, length = arrow["angle"], arrow["length"]
        x, y = plane_xy(
            p, length * math.cos(math.radians(a0)), length * math.sin(math.radians(a0))
        )
        first = next((i for i, st in enumerate(steps) if "arrow" in st), 0)
        turns = "|".join(f"rotate({fmt(a0 - a)}deg)" for a in plane_carry(steps, "arrow", a0))
        parts.append(
            plane_arrow(
                ox,
                oy,
                x,
                y,
                "pl-vec pl-turn",
                f' data-on="{on_from(first)}" data-move="{turns}" '
                f'style="transform-origin: {fmt(ox)}px {fmt(oy)}px"',
            )
        )
    arc = p.get("arc")
    if arc:  # at rest only: the angle between two directions, marked from the origin
        r = arc.get("r", 0.3) * PLANE_BOX / (hi - lo)
        a, b = math.radians(arc["from"]), math.radians(arc["to"])
        sx, sy = ox + r * math.cos(a), oy - r * math.sin(a)
        ex, ey = ox + r * math.cos(b), oy - r * math.sin(b)
        mid = (a + b) / 2
        parts.append(
            f'<g class="p-rest"><path class="pl-arc" d="M{fmt(sx)} {fmt(sy)}A{fmt(r)} {fmt(r)} 0 0 0 '
            f'{fmt(ex)} {fmt(ey)}"/><text class="pl-label" x="{fmt(ox + (r + 9) * math.cos(mid))}" '
            f'y="{fmt(oy - (r + 9) * math.sin(mid) + 3)}" text-anchor="middle">'
            f'{html.escape(arc.get("label", ""))}</text></g>'
        )
    if p.get("rest_note"):
        parts.append(
            f'<text class="pl-note p-rest" x="{fmt(fx0 + 6)}" y="{fmt(fy1 + 13)}">'
            f'{html.escape(p["rest_note"])}</text>'
        )
    for i, st in enumerate(steps):
        for c in st.get("wrong", []):
            x, y = plane_xy(p, *c)
            parts.append(
                f'<circle class="pl-wrong tone-push" cx="{fmt(x)}" cy="{fmt(y)}" r="9" fill="none" '
                f'stroke="{TONES["push"]}" stroke-width="1.5" data-on="{i}" data-gone=""/>'
            )
    paints = plane_carry(steps, "paint", None) if any("paint" in st for st in steps) else []
    centers = p.get("centers", [])
    if centers:
        places = plane_carry(steps, "centers", centers)
        finals = [v for (_, _, v) in p["points"]]

        def by_key(keyf, draw, cls):
            """One drawing per distinct state, shown at every step in it, and the final one
            at rest (hidden while stepping)."""
            seen = {}
            for i in range(n):
                seen.setdefault(keyf(i), []).append(i)
            for key, idx in seen.items():
                parts.append(
                    f'<g class="{cls}" data-on="{" ".join(map(str, idx))}" data-gone="">'
                    f"{draw(*key)}</g>"
                )
            parts.append(
                f'<g class="{cls} p-rest">{draw(tuple(map(tuple, centers)), tuple(finals))}</g>'
            )

        def bounds_svg(cs, _held):
            out, done = "", set()
            on_frame = lambda a, b: any(
                abs(a[i] - e) < 1e-6 and abs(b[i] - e) < 1e-6 for i in (0, 1) for e in (lo, hi)
            )
            for poly in plane_cells([list(c) for c in cs], lo, hi):
                for a, b in zip(poly, poly[1:] + poly[:1]):
                    key = tuple(
                        sorted(
                            [(round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))]
                        )
                    )
                    if on_frame(a, b) or key in done:
                        continue
                    done.add(key)
                    (x1, y1), (x2, y2) = plane_xy(p, *a), plane_xy(p, *b)
                    out += f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" y2="{fmt(y2)}"/>'
            return out

        if p.get("bounds"):  # where each center's territory meets the next (Voronoi)
            by_key(lambda i: (tuple(map(tuple, places[i])), ()), bounds_svg, "pl-bounds")
        if p.get("joins") and paints:  # who joins whom, only as they join: at rest no lines,
            for i, st in enumerate(steps):  # which would read as a graph's edges
                held = paints[i] or []
                if "paint" not in st or all(k < 0 for k in held):
                    continue
                lines = ""
                for (u, v, _), k in zip(p["points"], held):
                    if k >= 0:
                        (x1, y1), (x2, y2) = plane_xy(p, u, v), plane_xy(p, *places[i][k])
                        lines += (
                            f'<line x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" y2="{fmt(y2)}"/>'
                        )
                parts.append(f'<g class="pl-joins" data-on="{i}" data-gone="">{lines}</g>')
                was = (paints[i - 1] if i else None) or [-1] * len(held)
                for (u, v, _), a, b in zip(p["points"], was, held):
                    if 0 <= a != b:  # it changed sides: ringed
                        x, y = plane_xy(p, u, v)
                        parts.append(
                            f'<circle class="pl-moved" cx="{fmt(x)}" cy="{fmt(y)}" r="7.5" '
                            f'data-on="{i}" data-gone=""/>'
                        )
    for j, (u, v, val) in enumerate(p.get("points", [])):
        x, y = plane_xy(p, u, v)
        grouped = isinstance(val, int) and not isinstance(val, bool)
        label = ""
        if p.get("labels"):
            tx, anchor = (x - 9, "end") if u < 0.5 else (x + 9, "start")
            label = (
                f'<text class="pl-label" x="{fmt(tx)}" y="{fmt(y + (13 if v < 0.5 else -7))}" '
                f'text-anchor="{anchor}">{u:g}, {v:g}</text>'
            )
        if not grouped:  # on or off: filled or hollow
            on = val in (1, True, "on")
            parts.append(
                f'<g class="pl-pt{" on" if on else ""}"><circle cx="{fmt(x)}" cy="{fmt(y)}" '
                f'r="{p.get("r", 5)}"/>{label}</g>'
            )
            continue
        # in a group: its shape, one drawn for each it takes (none yet: a small hollow dot), each
        # shown at the steps it holds, and at rest the group it ends in
        held = [ps[j] if ps else -1 for ps in paints] or [val] * len(steps)
        move = ""
        pr = p.get("project")
        if pr:  # from step `from` on, each point drops onto the line through `at` at `angle`
            (cu, cv), a = pr["at"], math.radians(pr["angle"])
            t = (u - cu) * math.cos(a) + (v - cv) * math.sin(a)
            qx, qy = plane_xy(p, cu + t * math.cos(a), cv + t * math.sin(a))
            moves = "|".join(
                f"translate({fmt(qx - x) if i >= pr['from'] else 0}px, "
                f"{fmt(qy - y) if i >= pr['from'] else 0}px)"
                for i in range(len(steps))
            )
            move = f' data-move="{moves}"'
            parts.append(  # the way it drops, shown as it does
                f'<line class="pl-drop" x1="{fmt(x)}" y1="{fmt(y)}" x2="{fmt(qx)}" y2="{fmt(qy)}" '
                f'data-on="{on_from(pr["from"])}" data-gone=""/>'
            )
        for k in sorted(set(held) | {val}):
            on = " ".join(str(i) for i, h in enumerate(held) if h == k)
            rest = "" if k == val else ' data-gone=""'
            parts.append(
                f'<g class="pl-grp" data-on="{on}"{rest}{move}>{plane_shape(k, x, y, 4.2)}</g>'
            )
    if centers:  # each group's center, green: what is learned
        for k, (u, v) in enumerate(centers):
            x, y = plane_xy(p, u, v)
            moves = []
            for pl in places:
                px, py = plane_xy(p, *pl[k])
                moves.append(f"translate({fmt(px - x)}px, {fmt(py - y)}px)")
            parts.append(
                f'<g class="pl-center" data-move="{"|".join(moves)}">{plane_shape(k, x, y, 5.5)}</g>'
            )
    pts = p.get("points", [])
    for j, name in enumerate(p.get("names", [])):  # each point's name, at its right (or left)
        name, side = (name, "right") if isinstance(name, str) else name
        x, y = plane_xy(p, pts[j][0], pts[j][1])
        tx, anchor = (x + 7, "start") if side == "right" else (x - 7, "end")
        parts.append(
            f'<text class="pl-name" x="{fmt(tx)}" y="{fmt(y + 3)}" text-anchor="{anchor}">'
            f"{html.escape(name)}</text>"
        )
    for i, st in enumerate(steps):  # an arrow from one point to another, from this step on
        for a, b in st.get("pairs", []):
            (x1, y1), (x2, y2) = plane_xy(p, *pts[a][:2]), plane_xy(p, *pts[b][:2])
            d = math.hypot(x2 - x1, y2 - y1)
            ux, uy = (x2 - x1) / d, (y2 - y1) / d
            parts.append(
                f'<line class="pl-pair" x1="{fmt(x1 + ux * 6)}" y1="{fmt(y1 + uy * 6)}" '
                f'x2="{fmt(x2 - ux * 7)}" y2="{fmt(y2 - uy * 7)}" marker-end="url(#arr-{fid})" '
                f'data-on="{on_from(i)}"/>'
            )
    for i, st in enumerate(steps):
        if st.get("note"):
            parts.append(
                f'<text class="pl-note" x="{fmt(fx0 + 6)}" y="{fmt(fy1 + 13)}" data-on="{i}" '
                f'data-gone="">{html.escape(st["note"])}</text>'
            )
    axes = ""
    named = p.get("axes", ["Input 1", "Input 2"])
    if named:
        ax, ay = named
        axes = (
            f'<text class="pl-axis" x="{fmt((fx0 + fx1) / 2)}" y="{fmt(fy0 + 11)}" text-anchor="middle">'
            f"{html.escape(ax)} →</text>"
            f'<text class="pl-axis" x="0" y="0" text-anchor="middle" transform="translate('
            f'{fmt(fx0 - 4)} {fmt((fy0 + fy1) / 2)}) rotate(-90)">{html.escape(ay)} →</text>'
        )  # the second input's name reads up the left side, its letters reaching left of the line
    x0 = fx0 - (14 if named else 4)
    x1, y1, top = fx1 + p.get("right", 40 if named else 4), fy0 + (15 if named else 4), fy1 - 4
    if named:
        top = 0.0
    say = json.dumps([prose_html(st["say"]) for st in steps], ensure_ascii=False)
    return (
        f'<figure class="p-fig p-plane" data-flow="" data-say="{html.escape(say)}">'
        f'<svg style="width: {FIGURE_SCALE * (x1 - x0):.0f}px; max-width: 100%" '
        f'viewBox="{fmt(x0)} {fmt(top)} {fmt(x1 - x0)} {fmt(y1 - top)}" role="img" '
        f'aria-label="{html.escape(p.get("alt", ""))}" data-steps="{n}"{SEQ_ATTR}>'
        f'<defs>{marker_def(f"arr-{fid}", "currentColor")}<clipPath id="{clip}"><rect x="{fmt(fx0)}" y="{fmt(fy1)}" '
        f'width="{fmt(fx1 - fx0)}" height="{fmt(fy0 - fy1)}"/></clipPath></defs>'
        f'<rect class="pl-frame" x="{fmt(fx0)}" y="{fmt(fy1)}" width="{fmt(fx1 - fx0)}" '
        f'height="{fmt(fy0 - fy1)}"/>{axes}{"".join(parts)}</svg>'
        '<div class="p-fig-ctl">'
        + PRIMER_FLOW
        + '<p class="p-say" aria-live="polite"></p></div></figure>'
    )


CHART_W, CHART_H = 176.0, 108.0  # a chart figure's frame, in the drawing's units
CHART_X0, CHART_Y0 = 12.0, 122.0  # its bottom left corner


def chart_xy(c: dict, x: float, y: float) -> tuple:
    """A point of a chart figure's axes (`x` and `y` ranges) in the drawing's units."""
    (xl, xh), (yl, yh) = c["x"], c["y"]
    return (
        CHART_X0 + (x - xl) / (xh - xl) * CHART_W,
        CHART_Y0 - (y - yl) / (yh - yl) * CHART_H,
    )


def bspline(values: list, x: float) -> float:
    """A clamped uniform cubic B-spline through [0, 1], shaped by `values` (Cox-de Boor)."""
    n, k = len(values), 3
    inner = [i / (n - k) for i in range(1, n - k)]
    t = [0.0] * (k + 1) + inner + [1.0] * (k + 1)
    x = min(max(x, 0.0), 1.0 - 1e-9)

    def basis(i, p):
        if p == 0:
            return 1.0 if t[i] <= x < t[i + 1] else 0.0
        a = (x - t[i]) / (t[i + p] - t[i]) * basis(i, p - 1) if t[i + p] > t[i] else 0.0
        b = (
            (t[i + p + 1] - x) / (t[i + p + 1] - t[i + 1]) * basis(i + 1, p - 1)
            if t[i + p + 1] > t[i + 1]
            else 0.0
        )
        return a + b

    return sum(v * basis(i, k) for i, v in enumerate(values))


def bspline_knots(values: list) -> tuple:
    """A clamped cubic B-spline's control points' x (Greville) and its pieces' joins."""
    n, k = len(values), 3
    t = [0.0] * (k + 1) + [i / (n - k) for i in range(1, n - k)] + [1.0] * (k + 1)
    return [sum(t[i + 1 : i + k + 1]) / k for i in range(n)], [0.0] + t[k + 1 : n] + [1.0]


def chart_fn(cv: dict):
    """A chart curve's function, by name: relu, sigmoid, gauss (c, w), sine (a, f, p),
    bspline (values), or poly (coef, from the constant up)."""
    fn = cv["fn"]
    if fn == "relu":
        return lambda x: max(0.0, x)
    if fn == "sigmoid":
        return lambda x: 1 / (1 + math.exp(-x))
    if fn == "gauss":
        return lambda x: math.exp(-((x - cv.get("c", 0.0)) ** 2) / (2 * cv.get("w", 1.0) ** 2))
    if fn == "sine":
        return lambda x: cv.get("a", 1.0) * math.sin(
            2 * math.pi * (cv.get("f", 1.0) * x + cv.get("p", 0.0))
        ) + cv.get("b", 0.0)
    if fn == "bspline":
        return lambda x: bspline(cv["values"], x)
    if fn == "poly":  # coefficients from the constant up
        return lambda x: sum(c * x**k for k, c in enumerate(cv["coef"]))
    raise ValueError(f"chart curve: no function '{fn}'")


def chart_samples(c: dict, cv: dict, a: float | None = None, b: float | None = None) -> list:
    """A curve's points, closely spaced, in the drawing's units, from x = a to b."""
    f = chart_fn(cv)
    lo, hi = cv.get("domain", c["x"])  # a spline is drawn only over [0, 1]
    a = lo if a is None else a
    b = hi if b is None else b
    n = max(2, int(abs(b - a) / (c["x"][1] - c["x"][0]) * 240))
    return [chart_xy(c, a + (b - a) * i / n, f(a + (b - a) * i / n)) for i in range(n + 1)]


def chart_shows(st_list: list, item: dict, n: int) -> str:
    """The steps a chart part shows at: `steps` if listed, else from `from` (0) on."""
    if "steps" in item:
        return " ".join(map(str, item["steps"]))
    return " ".join(str(k) for k in range(item.get("from", 0), n))


def chart_figure(fid: str, spec: dict) -> str:
    """A glossary figure drawn as a chart rather than a network (ReLU, a Gaussian bump, a
    spline, quantization): `curves` (a named function each, shown at `steps` or `from` a step
    on, at rest unless `rest: false`; a spline's `pieces: true` shades its pieces in turn),
    a `dot` that runs along a curve to each step's x, spline `controls` (squares at each
    step's values), `samples` of a curve that `snap` to `levels`, with the `stair` they make,
    and a `note` per step."""
    c, steps = spec["chart"], spec.get("flow", [])
    n = len(steps)
    fx0, fy0 = chart_xy(c, c["x"][0], c["y"][0])
    fx1, fy1 = chart_xy(c, c["x"][1], c["y"][1])
    parts = []
    gone = lambda item: "" if item.get("rest", True) else ' data-gone=""'
    if c.get("zero", True):  # the axes' zero lines, where they fall inside the frame
        zx, zy = chart_xy(c, 0, 0)
        d = ""
        if c["y"][0] < 0 < c["y"][1]:
            d += f"M{fmt(fx0)} {fmt(zy)}H{fmt(fx1)}"
        if c["x"][0] < 0 < c["x"][1]:
            d += f"M{fmt(zx)} {fmt(fy0)}V{fmt(fy1)}"
        if d:
            parts.append(f'<path class="pl-cross" d="{d}"/>')
    lv = c.get("levels")
    if lv:  # evenly spaced allowed values, from the bottom of the range to the top
        lo, hi = lv.get("range", c["y"])
        vals = [lo + (hi - lo) * (i + 0.5) / lv["n"] for i in range(lv["n"])]
        d = "".join(f"M{fmt(fx0)} {fmt(chart_xy(c, 0, v)[1])}H{fmt(fx1)}" for v in vals)
        parts.append(
            f'<path class="cv-levels" d="{d}" data-on="{chart_shows(steps, lv, n)}"{gone(lv)}/>'
        )
    for cv in c.get("curves", []):
        on = chart_shows(steps, cv, n)
        cls = (
            "cv-curve"
            + (f" cv-{cv['hue']}" if cv.get("hue") else "")
            + (" cv-faint" if cv.get("faint") else "")
        )
        if cv.get("pieces") and cv["fn"] == "bspline":
            _, joins = bspline_knots(cv["values"])
            paths = []
            for j in range(len(joins) - 1):
                pts = chart_samples(c, cv, joins[j], joins[j + 1])
                paths.append(
                    f'<path class="{cls}{" cv-alt" if j % 2 else ""}" '
                    f'd="M{" L".join(f"{fmt(x)} {fmt(y)}" for x, y in pts)}"/>'
                )
            parts.append(f'<g data-on="{on}"{gone(cv)}>{"".join(paths)}</g>')
            continue
        pts = chart_samples(c, cv)
        parts.append(
            f'<path class="{cls}" d="M{" L".join(f"{fmt(x)} {fmt(y)}" for x, y in pts)}" '
            f'data-on="{on}"{gone(cv)}/>'
        )
    sm = c.get("samples")
    if sm:  # points sampled from a curve, which can snap to the nearest level
        cv = c["curves"][sm.get("curve", 0)]
        f = chart_fn(cv)
        lo, hi = lv.get("range", c["y"]) if lv else c["y"]
        step = (hi - lo) / lv["n"] if lv else 1.0
        snap_at = [bool(st.get("snap")) for st in steps]
        xs = [c["x"][0] + (c["x"][1] - c["x"][0]) * (i + 0.5) / sm["n"] for i in range(sm["n"])]
        snapped = []
        for x in xs:
            y = f(x)
            q = (
                lo + step * (min(lv["n"] - 1, max(0, math.floor((y - lo) / step))) + 0.5)
                if lv
                else y
            )
            snapped.append(q)
            px, py = chart_xy(c, x, y)
            qy = chart_xy(c, x, q)[1]
            moves = "|".join(f"translate(0px, {fmt(qy - py) if s else 0}px)" for s in snap_at)
            parts.append(
                f'<circle class="cv-sample" cx="{fmt(px)}" cy="{fmt(py)}" r="2.6" '
                f'data-on="{chart_shows(steps, sm, n)}" data-move="{moves}" '
                f'style="transform: translate(0px, {fmt(qy - py)}px)"/>'
            )
        st_ = c.get("stair")
        if st_ is not None and lv:
            w = (c["x"][1] - c["x"][0]) / sm["n"]
            d = ""
            for i, (x, q) in enumerate(zip(xs, snapped)):
                a, ya = chart_xy(c, x - w / 2, q)
                b, _ = chart_xy(c, x + w / 2, q)
                d += (f"M{fmt(a)} {fmt(ya)}" if i == 0 else f"V{fmt(ya)}") + f"H{fmt(b)}"
            parts.append(
                f'<path class="cv-stair" d="{d}" data-on="{chart_shows(steps, st_, n)}"/>'
            )
    ctl = c.get("controls")
    if ctl:  # a spline's values, as squares at their places along it
        rest = ctl["values"]
        xs, _ = bspline_knots(rest)
        places = []
        cur = rest
        for st in steps:
            cur = st.get("controls", cur)
            places.append(cur)
        for k, (x, v) in enumerate(zip(xs, rest)):
            px, py = chart_xy(c, x, v)
            moves = "|".join(
                f"translate(0px, {fmt(chart_xy(c, x, pl[k])[1] - py)}px)" for pl in places
            )
            parts.append(
                f'<rect class="cv-ctl" x="{fmt(px - 3.5)}" y="{fmt(py - 3.5)}" width="7" height="7" '
                f'rx="1" data-on="{chart_shows(steps, ctl, n)}" data-move="{moves}"/>'
            )
    dot = c.get("dot")
    if dot:  # runs along its curve to each step's x, with dashed guides to both axes
        cv = c["curves"][dot.get("curve", 0)]
        pts = chart_samples(c, cv)
        xs = [st.get("dot") for st in steps]
        given = [x for x in xs if x is not None]
        cur, at = (given[0] if given else c["x"][0]), []
        lo, hi = cv.get("domain", c["x"])
        for x in xs:
            cur = x if x is not None else cur
            at.append(round((cur - lo) / (hi - lo) * (len(pts) - 1)))
        track = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)
        first = next((i for i, x in enumerate(xs) if x is not None), 0)
        shown = dot.get("steps", range(first, n))  # the steps it shows at, `steps` if given
        # the input it reads, a yellow mark where its guide meets the bottom, and the output
        # it gives, a red dot on the curve, as the panels color an input and an output; with
        # `tone: neutral` (a weight against its loss) an ink dot, and no mark
        neutral = " neutral" if dot.get("tone") == "neutral" else ""
        parts.append(
            f'<g class="cv-dot{neutral}" data-on="{" ".join(map(str, shown))}" data-gone="" '
            f'data-track="{track}" data-at="{"|".join(map(str, at))}" '
            f'data-axes="{fmt(fx0)},{fmt(fy0)}"><line class="cv-guide"/><line class="cv-guide"/>'
            f'<circle class="cv-foot" r="2.8"/><circle class="cv-out" r="3.6"/></g>'
        )
    # each step's readout: top left unless `note_at` puts it elsewhere (clear of the guides)
    nx, ny, anchor = fx0 + 6, fy1 + 13, "start"
    if c.get("note_at"):
        nx, ny = chart_xy(c, *c["note_at"])
        anchor = "end" if nx > (fx0 + fx1) / 2 else "start"
    for i, st in enumerate(steps):
        if st.get("note"):
            parts.append(
                f'<text class="pl-note" x="{fmt(nx)}" y="{fmt(ny)}" text-anchor="{anchor}" '
                f'data-on="{i}" data-gone="">{html.escape(st["note"])}</text>'
            )
    ax, ay = c.get("axes", ["Input", "Output"])
    axes = (
        f'<text class="pl-axis" x="{fmt((fx0 + fx1) / 2)}" y="{fmt(fy0 + 11)}" text-anchor="middle">'
        f"{html.escape(ax)} →</text>"
        f'<text class="pl-axis" x="0" y="0" text-anchor="middle" transform="translate('
        f'{fmt(fx0 - 4)} {fmt((fy0 + fy1) / 2)}) rotate(-90)">{html.escape(ay)} →</text>'
    )
    x0, x1, y0, y1 = fx0 - 14, fx1 + c.get("right", 6), fy1 - 6, fy0 + 15
    say = json.dumps([prose_html(st["say"]) for st in steps], ensure_ascii=False)
    return (
        f'<figure class="p-fig p-plane p-chart" data-flow="" data-say="{html.escape(say)}">'
        f'<svg style="width: {FIGURE_SCALE * (x1 - x0):.0f}px; max-width: 100%" '
        f'viewBox="{fmt(x0)} {fmt(y0)} {fmt(x1 - x0)} {fmt(y1 - y0)}" role="img" '
        f'aria-label="{html.escape(c.get("alt", ""))}" data-steps="{n}"{SEQ_ATTR}>'
        f'<rect class="pl-frame" x="{fmt(fx0)}" y="{fmt(fy1)}" width="{fmt(fx1 - fx0)}" '
        f'height="{fmt(fy0 - fy1)}"/>{axes}{"".join(parts)}</svg>'
        '<div class="p-fig-ctl">'
        + PRIMER_FLOW
        + '<p class="p-say" aria-live="polite"></p></div></figure>'
    )


BARS_W = 30.0  # a bar chart figure's bar width, and the gap between bars is a third of it


def bars_figure(fid: str, spec: dict) -> str:
    """A glossary figure drawn as bars (softmax): each step sets every bar's `values` (null for
    none yet) and the `scale` that turns them into heights (drawing units per unit), labels
    each bar with its value, and the bars grow or shrink to it; a step's `pick` marks one bar
    as drawn. Bars are red, as outputs, unless `tone` is neutral (a measure, not a unit's value)
    or hidden (a hidden layer's values, green as its units are). At rest it shows the last step."""
    b, steps = spec["bars"], spec.get("flow", [])
    n, names = len(steps), b["names"]
    gap = BARS_W / 3
    zero, top = b.get("zero", 112.0), 14.0
    # the names sit just under the lowest point any bar or its value reaches, at any step
    hang = 0.0
    sc = 1.0
    for st in steps:
        sc = st.get("scale", sc)
        hang = max([hang] + [-(v * sc) for v in st.get("values") or [] if v is not None])
    if b.get("inputs"):
        ms = b["inputs"].get("scale", 15.0)
        first = b["inputs"].get("values") or steps[0]["values"]
        hang = max([hang] + [-(v * ms) for v in first if v is not None])
    bottom = zero + (hang + 11 if hang > 0 else 0) - 2  # the names' line, 13 below it
    x_left, parts = 22.0, []
    inputs = b.get("inputs")
    if inputs:  # the first step's values as a small chart at the left, an arrow on to the bars
        mw, mg, ms = 10.0, 4.0, inputs.get("scale", 15.0)
        first = b["inputs"].get("values") or steps[0]["values"]
        for k, v in enumerate(first):
            x, h = x_left + k * (mw + mg), v * ms
            y0, y1 = (zero - h, zero) if h >= 0 else (zero, zero - h)
            parts.append(
                f'<rect class="cv-bar cv-in" x="{fmt(x)}" y="{fmt(y0)}" width="{fmt(mw)}" '
                f'height="{fmt(y1 - y0)}"/><text class="cv-val" x="{fmt(x + mw / 2)}" '
                f'y="{fmt(zero - h - 3 if h >= 0 else zero - h + 8)}" text-anchor="middle">'
                f"{v:g}</text>".replace(">-", ">−")
            )
        right = x_left + len(first) * (mw + mg) - mg
        parts.append(
            f'<path class="pl-cross" d="M{fmt(x_left - 4)} {fmt(zero)}H{fmt(right + 4)}"/>'
            f'<text class="pl-axis" x="{fmt((x_left + right) / 2)}" y="{fmt(bottom + 13)}" '
            f'text-anchor="middle">{html.escape(inputs.get("label", "Scores"))}</text>'
        )
        ax0, ax1, ay = right + 8, right + 50, zero - 34
        parts.append(
            f'<line class="cv-arrow" x1="{fmt(ax0)}" y1="{fmt(ay)}" x2="{fmt(ax1)}" y2="{fmt(ay)}" '
            f'stroke="currentColor" stroke-width="0.9" marker-end="url(#arr-{fid})"/>'
            f'<text class="pl-label" x="{fmt((ax0 + ax1) / 2)}" y="{fmt(ay - 5)}" '
            f'text-anchor="middle">{html.escape(inputs.get("arrow", ""))}</text>'
        )
        x_left = ax1 + 14
    parts += [
        f'<path class="pl-cross" d="M{fmt(x_left - 6)} {fmt(zero)}H{fmt(x_left + len(names) * (BARS_W + gap) - gap + 6)}"/>'
    ]
    vals = [st.get("values") for st in steps]
    scales = plane_carry(steps, "scale", 1.0)  # a step that only marks a draw keeps the scale
    cur, hs = vals[0], []
    for v, s in zip(vals, scales):
        cur = v or cur
        hs.append([x * s if x is not None else 0.0 for x in cur])
    shown = plane_carry(steps, "values", [None] * len(names))
    tone = f" {b['tone']}" if b.get("tone") in ("neutral", "hidden") else ""
    for k, name in enumerate(names):
        x = x_left + k * (BARS_W + gap)
        moves = "|".join(f"scale(1, {h[k]:.3f})" for h in hs)
        for i, st in enumerate(steps):  # the bar a step draws, marked above it
            if st.get("pick") == k:
                parts.append(
                    f'<text class="pl-note" x="{fmt(x + BARS_W / 2)}" y="{fmt(zero - hs[i][k] - 14)}" '
                    f'text-anchor="middle" data-on="{i}" data-gone="">drawn</text>'
                )
        on = [i for i in range(n) if shown[i][k] is not None]
        lit = f' data-on="{" ".join(map(str, on))}"' if len(on) < n else ""  # none yet: unseen
        parts.append(
            f'<rect class="cv-bar{tone}"{lit} x="{fmt(x)}" y="{fmt(zero - 1)}" width="{fmt(BARS_W)}" height="1" '
            f'data-move="{moves}" style="transform-origin: {fmt(x)}px {fmt(zero)}px; '
            f'transform: scale(1, {hs[-1][k]:.3f})"/>'
            f'<text class="pl-axis" x="{fmt(x + BARS_W / 2)}" y="{fmt(bottom + 13)}" '
            f'text-anchor="middle">{html.escape(name)}</text>'
        )
        for i, h in enumerate(hs):
            v = shown[i][k]  # each step's value, carried from the step before if it sets none
            if v is None:
                continue
            y = (
                zero - h[k] - 3 if h[k] >= 0 else zero - h[k] + 9
            )  # above a bar, or below one that hangs
            hide = "" if i == n - 1 else ' data-gone=""'  # only the last step's values at rest
            parts.append(
                f'<text class="cv-val" x="{fmt(x + BARS_W / 2)}" y="{fmt(y)}" text-anchor="middle" '
                f'data-on="{i}"{hide}>'
                f'{b.get("format", "{:.2f}").format(v).replace("-", "−")}</text>'
            )
    x1 = x_left + len(names) * (BARS_W + gap) - gap + 12
    say = json.dumps([prose_html(st["say"]) for st in steps], ensure_ascii=False)
    return (
        f'<figure class="p-fig p-plane p-chart" data-flow="" data-say="{html.escape(say)}">'
        f'<svg style="width: {FIGURE_SCALE * (x1 - 10):.0f}px; max-width: 100%" '
        f'viewBox="10 {fmt(top - 6)} {fmt(x1 - 10)} {fmt(bottom + 18 - top + 6)}" role="img" '
        f'aria-label="{html.escape(b.get("alt", ""))}" data-steps="{n}"{SEQ_ATTR}>'
        f'<defs>{marker_def(f"arr-{fid}", "currentColor")}</defs>{"".join(parts)}</svg>'
        '<div class="p-fig-ctl">'
        + PRIMER_FLOW
        + '<p class="p-say" aria-live="polite"></p></div></figure>'
    )


NAV_TOP = (  # in the glossary's bottom bar once its index has scrolled away: back to it
    '<button class="nav-more nav-top" type="button" title="Back to the index">'
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 14.5l6-6 6 6"/></svg>Index</button>'
)
NAV_MORE = (  # in a dialog's bottom bar while more lies below: says so (the scroll bar may be hidden)
    '<button class="nav-more" type="button" tabindex="-1" aria-hidden="true" title="Scroll down">'
    '<svg viewBox="0 0 24 24"><path d="M6 9.5l6 6 6-6"/></svg><span>More below</span></button>'
)


GLOSSARY_ICON = (  # an open book
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6.5C10.3 5.3 7.6 4.8 4.5 5v12.5'
    'c3.1-.2 5.8.3 7.5 1.5 1.7-1.2 4.4-1.7 7.5-1.5V5c-3.1-.2-5.8.3-7.5 1.5zM12 6.5V19"/></svg>'
)


GLOSSARY_UNLINKED = ("Input and output", "Link", "Unit")  # too common in the entries to link there
# the basics the primer teaches, left unlinked in a panel's summary and reading, where linking
# every use made the text look stippled and hid the links that carry meaning; the glossary and
# the primer still link them
PANEL_UNLINKED = (
    "Unit",
    "Layer",
    "Weight",
    "Position",
    "Vector",
    "Link",
    "Input and output",
    "Parameters",
    "Head",
    "Token",
    "Class",
)


def glossary_button(doc: dict) -> str:
    if not doc.get("glossary"):
        return ""
    return (
        '<button class="icon-btn" type="button" id="gloss-btn" aria-label="Glossary" '
        f'title="Glossary" aria-haspopup="dialog">{GLOSSARY_ICON}</button>'
    )


def glossary_slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


# the glossary's groups, in the order its index and entries show them
GLOSSARY_GROUPS = OrderedDict(
    [
        ("basics", "Basics"),
        ("operations", "Operations"),
        ("objectives", "Objectives"),
        ("training", "Training"),
        ("rl", "Reinforcement learning"),
        ("models", "Model families"),
        ("tasks", "Tasks and data"),
        ("scale", "Scale"),
    ]
)


def glossary_entries(doc: dict) -> list:
    """The glossary's entries by group, then alphabetically."""
    order = list(GLOSSARY_GROUPS)
    return sorted(
        doc.get("glossary", []),
        key=lambda e: (
            order.index(e["group"]) if e.get("group") in order else len(order),
            e["term"].lower(),
        ),
    )


def glossary_split(e: dict) -> tuple:
    """An entry's text as its first sentence (the lead, and a linked term's tooltip) and the
    rest."""
    parts = re.split(r"(?<=[.!?])\s", " ".join(str(e["text"]).split()), maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def glossary_hint(e: dict) -> str:
    """An entry's first sentence, as plain text: the tooltip on a term linked from a panel."""
    return glossary_split(e)[0].replace("`", "")


def glossary_used(doc: dict, e: dict) -> list:
    """The panels an entry names: its list, or for an objective's entry, the panels trained
    with that objective."""
    arch = doc["architectures"]
    if "used_in" in e:
        return [p for p in e["used_in"] if p in arch]
    if e.get("objective"):
        return [
            p
            for p, a in arch.items()
            if a.get("objective") == e["objective"]
            or e["objective"] in (a.get("also_objectives") or [])
        ]
    return []


def glossary_table(e: dict) -> str:
    """An entry's table: a caption, a header row, and a row per kind of model, its first cell
    naming the row. On a phone each row stacks into a block, every cell under its column's
    name; the roles keep it a table to a screen reader, which a block display would lose."""
    t = e["table"]
    cap = f'gt-{glossary_slug(e["term"])}'
    head = '<th role="columnheader"><span class="sr-only">Model</span></th>' + "".join(
        f'<th scope="col" role="columnheader">{prose_html(c)}</th>' for c in t["cols"]
    )
    body = "".join(
        f'<tr role="row"><th scope="row" role="rowheader">{prose_html(r[0])}</th>'
        + "".join(
            f'<td role="cell" data-col="{html.escape(c, quote=True)}">{prose_html(v)}</td>'
            for c, v in zip(t["cols"], r[1:])
        )
        + "</tr>"
        for r in t["rows"]
    )
    return (
        f'<table class="g-table" role="table" aria-labelledby="{cap}">'
        f'<caption id="{cap}">{prose_html(t["caption"])}</caption>'
        f'<thead role="rowgroup"><tr role="row">{head}</tr></thead>'
        f'<tbody role="rowgroup">{body}</tbody></table>'
    )


def glossary_html(doc: dict, diagrams: dict) -> str:
    """The glossary: a dialog the page's glossary button opens. Its index of terms by group,
    then each term: its first sentence as a lead, a drawing that steps through how it works
    (some), the panels that use it, and the work it comes from."""
    diagrams = {**diagrams, **figure_diagrams(doc)}
    arch = doc["architectures"]
    entries = glossary_entries(doc)
    out = ['<nav class="g-nav" aria-label="Index">']  # a landmark, so a screen reader can skip it
    for g, name in GLOSSARY_GROUPS.items():
        terms = [e for e in entries if e.get("group") == g]
        if terms:  # each group a list named by its label, so a screen reader says how many
            out.append(
                f'<div class="g-group"><p class="g-gname" id="gi-{g}">{name}</p>'
                f'<ul class="g-index" aria-labelledby="gi-{g}">'
                + "".join(
                    f'<li><a href="#glossary-{glossary_slug(e["term"])}">{html.escape(e["term"])}</a></li>'
                    for e in terms
                )
                + "</ul></div>"
            )
    out.append("</nav>")
    group = None
    for e in entries:
        if e.get("group") != group:
            group = e.get("group")
            out.append(f'<h3 class="g-sec">{GLOSSARY_GROUPS.get(group, "")}</h3>')
        out.append(
            f'<h4 id="glossary-{glossary_slug(e["term"])}" tabindex="-1">'
            # its name links to itself: a click brings it to the top, its address in the URL
            f'<a class="g-self" href="#glossary-{glossary_slug(e["term"])}">'
            f'{html.escape(e["term"])}</a></h4>'
        )
        # the definition, then the rest: one paragraph, or split around the figure when there is one
        lead, rest = glossary_split(e)
        if not e.get("figure"):
            lead, rest = " ".join(t for t in (lead, rest) if t), ""
        # other entries it mentions link to them, the first use of each, as in the panels; not
        # itself, nor the few that nearly every entry mentions
        lead_html, rest_html = link_terms(
            doc,
            [prose_html(lead), prose_html(rest)],
            "glossary",
            (e["term"], *GLOSSARY_UNLINKED, *e.get("unlink", [])),
        )
        out.append(f'<p class="g-lead">{lead_html}</p>')
        if e.get("figure"):
            spec = doc.get("figures", {}).get(e["figure"], {})
            fig_html = (
                plane_figure(e["figure"], spec)
                if "plane" in spec
                else (
                    chart_figure(e["figure"], spec)
                    if "chart" in spec
                    else (
                        bars_figure(e["figure"], spec)
                        if "bars" in spec
                        else primer_figure(
                            diagrams[e["figure"]], e.get("labels", []), 0, True, e.get("tips")
                        )
                    )
                )
            )
            # its Step through names the entry, so it isn't one of many alike to a screen reader
            out.append(
                fig_html.replace(
                    '<button class="d-play" type="button">',
                    f'<button class="d-play" type="button" '
                    f'aria-describedby="glossary-{glossary_slug(e["term"])}">',
                )
            )
        if rest:
            out.append(f"<p>{rest_html}</p>")
        if e.get("table"):
            out.append(glossary_table(e))
        used = glossary_used(doc, e)
        label = USED_AS[e.get("used_as", "uses")]
        if e.get("used_in_text"):
            out.append(
                f'<p class="g-used"><span class="k">{label}:</span> '
                f'{prose_html(e["used_in_text"])}</p>'
            )
        elif used:
            out.append(
                f'<p class="g-used"><span class="k">{label}:</span> '
                + " ".join(  # each name keeps its comma
                    f'<span class="g-nw"><button type="button" data-open="{p}">'
                    f'{html.escape(arch[p]["name"])}</button>{"," if k < len(used) - 1 else ""}</span>'
                    for k, p in enumerate(used)
                )
                + "</p>"
            )
        if e.get("sources"):
            out.append(
                f'<p class="g-used g-src"><span class="k">Source:</span> '
                f'{keep_punct("; ".join(glossary_source(doc, e, c) for c in e["sources"]))}</p>'
            )
    return (
        '<dialog id="glossary" aria-labelledby="g-title"><div class="p-wrap">'
        '<div class="g-head"><h2 id="g-title" tabindex="-1">Glossary</h2>'
        '<select id="g-order" aria-label="Order of terms" autocomplete="off"><option value="topic">By topic</option>'
        '<option value="az">A to Z</option></select></div>'
        f'{"".join(out)}</div><div class="p-nav">{NAV_MORE}{NAV_TOP}<button class="btn" '
        'type="button" id="g-close">Close</button></div></dialog>'
    )


def cited_in(doc: dict, cite: str) -> list:
    """The panels whose papers include this cite, in the order the YAML lists them."""
    return [
        pid
        for pid, a in doc["architectures"].items()
        if any(p["cite"] == cite for p in a.get("papers", []))
    ]


def glossary_source(doc: dict, e: dict, c) -> str:
    """One of an entry's sources: a cite that a panel lists opens that panel (the one the entry
    uses, if several list it), where the paper is given in full; any other links to the work."""
    arch = doc["architectures"]
    if isinstance(c, str):
        panels = cited_in(doc, c)
        used = glossary_used(doc, e)
        pid = next((p for p in panels if p in used), panels[0] if panels else "")
        if not pid:
            return html.escape(c)
        title = next(p["title"] for p in arch[pid]["papers"] if p["cite"] == c)
        tip = f'{title}. In the {arch[pid]["name"]} panel\'s papers.'
        return (
            f'<button type="button" data-open="{pid}" title="{html.escape(tip)}">'
            f"{html.escape(c)}</button>"
        )
    return (
        f'<a href="{html.escape(c["url"])}" target="_blank" rel="noopener" '
        f'title="{html.escape(c["title"])}">{html.escape(c["cite"])}'
        f"{NEW_TAB}</a>"
    )


def has_keys(a: dict) -> list:
    """The key entries (data-k) a panel's network has but doesn't draw: its `contains`."""
    return [("link:" if k in LINKS else "cell:") + k for k in a.get("contains", [])]


def has_attr(a: dict) -> str:
    keys = has_keys(a)
    return f' data-has="{" ".join(keys)}"' if keys else ""


def has_text(a: dict) -> str:
    """The line under a panel's key naming what its network has but the panel doesn't draw."""
    names = [
        (LINKS[k]["label"] if k in LINKS else CELLS[k]["label"]).lower() + "s"
        for k in a.get("contains", [])
    ]
    return ("Also has, not drawn: " + ", ".join(names) + ".") if names else ""


def objective_gloss(doc: dict, objective: str) -> str:
    """The glossary entry (its anchor) that defines an objective tag, if any."""
    for e in doc.get("glossary", []):
        if e.get("objective") == objective:
            return glossary_slug(e["term"])
    return ""


# a glossary term linked from running text: a real link to its entry, as Wikipedia's are, so a
# term of several words wraps between them like the text around it (a button can't: it sits on
# one line as a block, leaving a gap at the end of the line before it); page.js opens the entry
# over the page, and a new tab follows the address
LINK_OPEN = '<a class="g-link"'


def link_terms(doc: dict, parts: list, pid: str = "", skip: tuple = ()) -> list:
    """Panel prose (HTML) with the first use of each glossary term made a link to its entry:
    its earliest use, in any of its forms, once across all the parts given, and never inside
    a tag or a link already made. An entry with `link_in` links only in those panels (and in the
    primer if it lists `primer`): `node`, which means something else in a seq2seq's drawing.
    An entry with `link_each` (groups of its forms) links each group on its own. Terms in
    `skip` are left unlinked (an entry's own name, in its text)."""
    entries = [
        # a paired entry (encoder and decoder) links each half at its own first use
        {**e, "match": forms}
        for e in doc.get("glossary", [])
        if e.get("link", True)
        and e["term"] not in skip
        and (not e.get("link_in") or (pid or "primer") in e["link_in"])
        for forms in e.get("link_each") or [e.get("match", [e["term"]])]
    ]
    # entries with longer forms first, so "dense layer" is taken before "layer" can be
    entries.sort(key=lambda e: -max(len(f) for f in e.get("match", [e["term"]])))
    for e in entries:
        forms = sorted(e.get("match", [e["term"]]), key=len, reverse=True)
        pat = re.compile(
            r"(?<![\w-])(?:" + "|".join(re.escape(f) for f in forms) + r")(?![\w-])",
            re.IGNORECASE,
        )
        slug, hint = glossary_slug(e["term"]), glossary_hint(e)
        for i, text in enumerate(parts):
            bits = re.split(r"(<[^>]+>)", text)
            depth, hit = 0, False
            for j, b in enumerate(bits):
                if b.startswith("<"):
                    if b.startswith(("<button", "<i", "<a ")):
                        depth += 1
                    elif b.startswith(("</button", "</i", "</a")):
                        depth -= 1
                    continue
                if depth:
                    continue
                # never touching another term's link, where the two would read as one (a space
                # between keeps them apart: each is underlined on its own)
                prev_link = j > 0 and bits[j - 1].startswith(("</button", "</a"))
                next_link = j + 1 < len(bits) and bits[j + 1].startswith(LINK_OPEN)
                m = next(
                    (
                        m
                        for m in pat.finditer(b)
                        if not (prev_link and not b[: m.start()])
                        and not (next_link and not b[m.end() :])
                    ),
                    None,
                )
                if m:
                    bits[j] = (
                        b[: m.start()] + f'{LINK_OPEN} href="#glossary-{slug}" data-g="{slug}" '
                        f'title="{html.escape(hint)}">{m.group(0)}</a>' + b[m.end() :]
                    )
                    hit = True
                    break
            if hit:
                parts[i] = "".join(bits)
                break
    return [keep_punct(p) for p in parts]


KEEP_PUNCT = re.compile(
    r'([(“‘]*)(<button type="button"[^>]*>(?:(?!</button>).)*</button>)([,.;:!?)”’]+)'
)


def keep_punct(s: str) -> str:
    """A button in running text keeps the punctuation against it on its line: a browser may
    break a line after a button (it sits inline as a block), leaving "token" and its comma apart.
    """
    return KEEP_PUNCT.sub(r'<span class="g-nw">\1\2\3</span>', s)


def primer_html(doc: dict, diagrams: dict) -> str:
    """The primer for readers new to neural networks: a dialog the page's "?" button opens."""
    diagrams = {**diagrams, **figure_diagrams(doc)}
    # glossary terms link to their entries, the first use of each in the whole primer
    paras = [
        [prose_html(t) for t in sec["text"].strip().split("\n") if t.strip()]
        for sec in doc.get("primer", [])
    ]
    flat = link_terms(doc, [t for ps in paras for t in ps])
    linked, i = [], 0
    for ps in paras:
        linked.append(flat[i : i + len(ps)])
        i += len(ps)
    sections = "".join(
        (
            f'<h3 id="primer-{html.escape(sec["anchor"])}" tabindex="-1">'
            if sec.get("anchor")
            else "<h3>"
        )
        + f'{html.escape(sec["title"])}</h3>'
        + (
            primer_figure(
                diagrams[sec["figure"]],
                sec.get("labels", []),
                sec.get("live", 0),
                sec.get("flow", False),
                sec.get("tips"),
                sec.get("alt", ""),
            )
            if sec.get("figure")
            else ""
        )
        + "".join(f"<p>{t}</p>" for t in ps)
        + primer_key(sec.get("key", []), sec.get("groups"))
        for sec, ps in zip(doc.get("primer", []), linked)
    )
    if doc.get("path"):  # the way out of the primer, onto the reading path (#start)
        sections += (
            '<h3>Where to start</h3><p><a class="p-start" href="#start">Start here</a>: '
            f'{len(doc["path"])} steps in order, from the perceptron on, each with a line on what it '
            "adds to the ones before, and after each step its side trips, which take in the rest. "
            "The page's order menu keeps it as Reading path.</p>"
        )
    return (
        '<dialog id="primer" aria-labelledby="p-title"><div class="p-wrap">'
        '<h2 id="p-title" tabindex="-1">What is a neural network?</h2>'
        f'{sections}</div><div class="p-nav">{NAV_MORE}<button class="btn" type="button" id="p-close">Close'
        "</button></div></dialog>"
    )


def panel_credit(doc: dict) -> str:
    """The credit a downloaded diagram carries: the guide, its copyright and license, and repo."""
    repo = doc.get("repo")
    return f"From the {doc['title']}. {notice_text(doc)}" + (f". {repo['url']}" if repo else "")


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
        text = (
            doc.get("poster_subtitle", doc.get(key, "")) if key == "subtitle" else doc.get(key, "")
        )
        if text_width(str(text), role, size, weight) > text_col:
            warns.append(f"poster: the {key} is too wide for one line")
    placed = [p for s in doc["sections"] for p in s["panels"]]
    for p in placed:
        if p not in arch:
            warns.append(f"section lists unknown architecture '{p}'")
    for aid in arch:
        if placed.count(aid) != 1:
            warns.append(f"{aid}: appears {placed.count(aid)} times in sections (expected once)")
    greek = re.compile("[αβγδεζηθικλμνξπρστυφχψω]")  # lowercase Greek: a variable, so italic

    def unmarked(where, text):
        for t in (text if isinstance(text, list) else [text]):
            bare = re.sub(r"`[^`]*`", "", str(t or ""))
            if greek.search(bare):
                warns.append(
                    f"{where}: mark the Greek variable in `...` (italic): {bare.strip()[:60]}"
                )

    for aid, a in arch.items():
        for k in ("summary", "read", "objective_math", "caption", "used_for"):
            unmarked(f"{aid} {k}", a.get(k))
        if not str(a.get("used_for", "")).strip():
            warns.append(
                f"{aid}: needs a used_for line (what it is used for, now or historically)"
            )
        dg = a.get("diagram", {})
        unmarked(f"{aid} say", dg.get("say", []))
        if isinstance(dg.get("flow"), list):
            unmarked(f"{aid} say", [st.get("say") for st in dg["flow"]])
    for e in doc.get("glossary", []):
        unmarked(f"glossary '{e['term']}'", e.get("text"))
        used_as = e.get("used_as", "uses")
        if used_as not in USED_AS:
            warns.append(f"glossary '{e['term']}': used_as is one of {list(USED_AS)}")
        elif used_as == "discussed":  # a panel listed as discussing it must name it
            for p in e.get("used_in", []):
                if p in arch and not mentions(e, arch[p]):
                    warns.append(
                        f"glossary '{e['term']}': {p} is listed as discussing it but never names it"
                    )
        t = e.get("table")
        if t is not None:  # a caption, and every row a name and one cell per column
            if not t.get("caption") or not t.get("cols"):
                warns.append(f"glossary '{e['term']}': a table needs a caption and cols")
            for r in t.get("rows", []):
                if len(r) != len(t.get("cols", [])) + 1:
                    warns.append(
                        f"glossary '{e['term']}': table row '{r[0]}' needs a name and one cell per column"
                    )
    for fid, f in doc.get("figures", {}).items():
        unmarked(f"figure '{fid}'", [st.get("say") for st in f.get("flow", [])])
    for aid in arch:  # a step-through's captions: one for each step, none empty
        d = diagrams.get(aid)
        if d is not None and d.say and (len(d.say) != flow_count(d) or not all(d.say)):
            warns.append(f"{aid}: give each of its {flow_count(d)} steps a `say` line")
        if d is not None and d.say and "Step through" not in arch[aid].get("read", ""):
            warns.append(f"{aid}: end its reading with what Step through shows")
    for sec in doc.get("primer", []):
        if (
            sec.get("figure")
            and sec["figure"] not in arch
            and sec["figure"] not in doc.get("figures", {})
        ):
            warns.append(f"primer: figure '{sec['figure']}' is not an architecture or a figure")
        elif sec.get("figure") and not (
            sec.get("alt") or doc.get("figures", {}).get(sec["figure"], {}).get("alt")
        ):
            warns.append(f"primer: give figure '{sec['figure']}' an `alt` for screen readers")
        for it in sec.get("key", []):
            if it.get("cell", "") not in CELLS and it.get("link", "") not in LINKS:
                warns.append(f"primer: key item {it} names no cell or link type")
        if sec.get("groups"):  # a full key: every symbol the legend shows, once
            cells, links = used_kinds(diagrams)
            want = [("cell", k) for k in cells] + [("link", k) for k in links]
            have = [
                ("cell", it["cell"]) if "cell" in it else ("link", it.get("link"))
                for it in sec.get("key", [])
            ]
            for w in want:
                if have.count(w) != 1:
                    warns.append(
                        f"primer: the full key lists {w[0]} '{w[1]}' {have.count(w)} times (expected once)"
                    )
            for h in have:
                if h not in want:
                    warns.append(
                        f"primer: the full key lists {h[0]} '{h[1]}', which no diagram uses"
                    )
            for g in sec["groups"]:
                if g not in GROUPS and g != "links":
                    warns.append(f"primer: unknown key group '{g}'")
    for o in OBJECTIVES:
        if o not in OBJECTIVE_MATH:
            warns.append(f"objective '{o}' has no formula in OBJECTIVE_MATH")
    notes = footnotes(doc)
    if len(notes) > len(MARKERS):
        warns.append(f"{len(notes)} footnotes, but there are only {len(MARKERS)} markers")
    for aid, a in arch.items():
        for key in ("name", "year", "caption", "objective", "summary", "papers"):
            if not a.get(key):
                warns.append(f"{aid}: missing '{key}'")
        home = next((s["id"] for s in doc["sections"] if aid in s["panels"]), None)
        for f in a.get("also", []):
            if f not in {s["id"] for s in doc["sections"]}:
                warns.append(f"{aid}: also lists unknown family '{f}'")
            elif f == home:
                warns.append(f"{aid}: also lists its own family '{f}'")
        linked = {}
        for rel, targets in (a.get("related") or {}).items():
            for t in targets:
                linked.setdefault(t, []).append(rel)
        for t, rels in linked.items():
            if len(rels) > 1:
                warns.append(
                    f"{aid}: related names '{t}' under more than one relation ({', '.join(rels)})"
                )
        for rel, targets in (a.get("related") or {}).items():
            if rel not in RELATIONS:
                warns.append(f"{aid}: related has unknown relation '{rel}'")
            for t in targets:
                if t not in arch:
                    warns.append(f"{aid}: related {rel} names unknown architecture '{t}'")
                elif t == aid:
                    warns.append(f"{aid}: related {rel} names itself")
        seen = {}  # each name in one list only: another name, a variant, or a related method
        for field in NAME_LISTS:
            names = a.get(field, [])
            if not isinstance(names, list) or not all(
                isinstance(n, str) and n.strip() for n in names
            ):
                warns.append(f"{aid}: {field} must be a list of names")
                continue
            for n in names:
                if n.lower() in (a["name"].lower(), str(a.get("full", "")).lower()):
                    warns.append(f"{aid}: {field} repeats its own name '{n}'")
                if n.lower() in seen:
                    warns.append(f"{aid}: '{n}' is in both {seen[n.lower()]} and {field}")
                seen[n.lower()] = field
        for o in a.get("also_objectives", []):
            if o not in OBJECTIVES:
                warns.append(
                    f"{aid}: also_objectives lists '{o}', which is not one of {OBJECTIVES}"
                )
            elif o == a.get("objective"):
                warns.append(f"{aid}: also_objectives repeats its objective '{o}'")
        for key in ("caption", "summary", "read", "note", "objective_note"):
            if str(a.get(key) or "").count("`") % 2:
                warns.append(f"{aid}: {key} has an unmatched backtick")
            if re.search(r"[{}]", prose_html(a.get(key) or "")):
                warns.append(f"{aid}: {key} has unmatched braces")
        line = a.get("objective_math") or OBJECTIVE_MATH.get(a.get("objective"), "")
        if line and re.search(r"[{}`]", math_html(line)):
            warns.append(f"{aid}: objective formula has unmatched braces or backticks")
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
    # a legend column is a panel column, or narrower when there are more groups than panel columns
    # (the last, Links, is sized to fit its labels; see legend_pitch)
    col_w = legend_pitch(legend_groups(*used_kinds(diagrams))) - POSTER_GX / 2
    for c in CELLS.values():
        if LEGEND_CELL_X + text_width(c["label"], "body", TYPE["legend"][0]) > col_w:
            warns.append(f"legend: '{c['label']}' is too wide for its column")
    for l in LINKS.values():
        if LEGEND_LINK_X + text_width(l["label"], "body", TYPE["legend"][0]) > W - 2:
            warns.append(f"legend: '{l['label']}' is too wide for its column")
    figs = figure_diagrams(doc)
    for e in doc.get("glossary", []):
        t = e.get("term", "?")
        if e.get("group") not in GLOSSARY_GROUPS:
            warns.append(f"glossary '{t}': group must be one of {', '.join(GLOSSARY_GROUPS)}")
        if not e.get("sources"):
            warns.append(f"glossary '{t}': name its `sources`")
        for c in e.get("sources", []):
            if isinstance(c, str) and not cited_in(doc, c):
                warns.append(f"glossary '{t}': no panel cites '{c}'; give its title and url")
            elif isinstance(c, dict) and not (c.get("cite") and c.get("title") and c.get("url")):
                warns.append(f"glossary '{t}': a source needs its cite, title, and url")
        if e.get("objective") and e["objective"] not in OBJECTIVES:
            warns.append(f"glossary '{t}': '{e['objective']}' is not an objective")
    slugs = Counter(glossary_slug(e.get("term", "")) for e in doc.get("glossary", []))
    for e in doc.get("glossary", []):
        t = e.get("term", "?")
        if slugs[glossary_slug(t)] > 1:
            warns.append(f"glossary: '{t}' is listed twice")
        for u in e.get("unlink", []):
            if not any(x["term"] == u for x in doc["glossary"]):
                warns.append(f"glossary '{t}': unlink '{u}' is not an entry")
        each = [f for g in e.get("link_each") or [] for f in g]
        if any(f not in e.get("match", []) for f in each):
            warns.append(f"glossary '{t}': link_each lists a form that is not in its match")
        for p in e.get("link_in", []):
            if p not in arch and p not in ("primer", "glossary"):
                warns.append(f"glossary '{t}': link_in '{p}' is not an architecture")
        for p in e.get("used_in", []):
            if p not in arch:
                warns.append(f"glossary '{t}': used_in '{p}' is not an architecture")
        f = e.get("figure")
        plane = doc.get("figures", {}).get(f, {}) if f else {}
        if {"plane", "chart", "bars"} & set(plane):
            if any(not st.get("say") for st in plane_steps(plane)):
                warns.append(f"glossary '{t}': give each step of '{f}' a `say` line")
            if not next(plane[k] for k in ("plane", "chart", "bars") if k in plane).get("alt"):
                warns.append(f"glossary '{t}': give figure '{f}' an `alt` for screen readers")
            f = None  # drawn on a plane: the checks below are for networks
        if f and f not in figs:
            warns.append(f"glossary '{t}': no figure '{f}'")
        elif f and not figs[f].alt:
            warns.append(f"glossary '{t}': give figure '{f}' an `alt` for screen readers")
        elif f and len(figs[f].say) != len(figs[f].seq):
            warns.append(f"glossary '{t}': give each step of '{f}' a `say` line")
        if f and figs[f].slide and len(figs[f].slide[2]) != len(figs[f].seq):
            warns.append(f"glossary '{t}': give '{f}' a slide offset for each step")
        if f and any(s.tone and s.tone not in TONES for s in figs[f].segs):
            warns.append(f"glossary '{t}': a link's tone must be one of {', '.join(TONES)}")
        if f and len(e.get("labels", [])) != len({round(c.y, 1) for c in figs[f].cells.values()}):
            warns.append(f"glossary '{t}': give each row of '{f}' a label")
    for pid, a in arch.items():
        for k in a.get("contains", []):
            if k not in LINKS and k not in CELLS:
                warns.append(f"{pid}: contains '{k}', not a key entry")
            elif pid in diagrams and any(
                (s.style == k or LINKS.get(s.style, {}).get("legend_as") == k)
                for s in diagrams[pid].segs
            ):
                warns.append(f"{pid}: contains '{k}', which its panel already draws")
    placed_all = {p for sec in doc["sections"] for p in sec["panels"]}
    for p in POSTER_PANELS:
        if p not in placed_all:
            warns.append(f"poster: '{p}' is not in any section")
    for p in PREVIEW_PANELS:
        if p not in arch:
            warns.append(f"preview: '{p}' is not an architecture")
    for d in list(diagrams.values()) + list(figure_diagrams(doc).values()):
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
    # a signature must pick something out, and the reading path must name panels, once each
    for aid, a in arch.items():
        if not a.get("signature"):  # the page's Highlight toggle shows one on every panel
            warns.append(f"{aid}: give it a `signature`, its defining part")
        if a.get("signature"):
            try:
                segs, rings = signature_parts(diagrams[aid], a["signature"])
            except ValueError as e:
                warns.append(f"{aid}: signature: {e}")
                continue
            if not segs and not rings:
                warns.append(f"{aid}: its signature picks out nothing")
            if not a.get("circle"):
                warns.append(f"{aid}: give it a `circle`, what a circle stands for in its drawing")
            if not a["signature"].get("note"):
                warns.append(f"{aid}: give its signature a `note`, what the highlight shows")
    seen = set()
    for p in [q for m in doc.get("path", []) for q in [m] + list(m.get("branches", []))]:
        if p.get("id") not in arch:
            warns.append(f"path: unknown panel '{p.get('id')}'")
        elif p["id"] in seen:
            warns.append(f"path: '{p['id']}' is on it twice")
        if not p.get("text"):
            warns.append(f"path: '{p.get('id')}' needs a line of text")
        seen.add(p.get("id"))
    if doc.get("path"):
        for aid in arch:
            if aid not in seen:
                warns.append(f"path: '{aid}' is on neither the path nor a branch")
    for st in doc.get("path", []):
        if not st.get("title"):
            warns.append(
                f"path: step '{st.get('id')}' needs a title, what it and its side trips share"
            )
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
    if glyph == "net":  # a tiny network, two nodes feeding one, read upward: a whole network
        top, left, right = (x, y - 2.7), (x - 3.0, y + 2.5), (x + 3.0, y + 2.5)
        r, gap = 0.95, 1.4  # node radius; each line stops this far from a node's centre
        out = []
        for a, b in ((left, top), (right, top)):
            ux, uy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(ux, uy)
            ux, uy = ux / n, uy / n
            out.append(
                f'<path d="M{fmt(a[0] + ux * gap)},{fmt(a[1] + uy * gap)} '
                f'L{fmt(b[0] - ux * gap)},{fmt(b[1] - uy * gap)}"/>'
            )
        return "".join(out) + "".join(
            f'<circle cx="{fmt(px)}" cy="{fmt(py)}" r="{r}"/>' for px, py in (top, left, right)
        )
    if glyph == "plus":  # a sum: what arrives is added
        a = 3.3
        return (
            f'<path d="M{fmt(x - a)},{fmt(y)} H{fmt(x + a)} M{fmt(x)},{fmt(y - a)} V{fmt(y + a)}" '
            f'stroke-width="1.3" stroke-linecap="round"/>'
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
    elif shape == "square" and spec.get("outline"):
        h = half_of(spec, 7.0)
        body = (
            f'<rect x="{fmt(x - h)}" y="{fmt(y - h)}" width="{fmt(2 * h)}" height="{fmt(2 * h)}" '
            f'rx="2.5" fill="none" stroke="{ink}" stroke-width="1.2"/>'
        )  # in the ink, not the gray box's edge, so it shows on the dark theme as well
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


def unfocused(d: Diagram, s: "Seg | None" = None, cell: str = "") -> bool:
    """Whether a part lies outside a glossary figure's focus, so it is faded at rest."""
    fo = d.focus
    if not fo:
        return False
    if s is None:
        return cell not in fo["cells"]
    if not fo.get("lines", True):
        return True
    if s.style in fo["styles"]:
        return False
    ends = list(s.ends) or ([s.dst] if s.dst else [])
    if ends and all(k in fo["cells"] for k in ends):
        return False
    if fo["into"] and len(s.ends) == 2 and s.ends[1] in fo["cells"]:
        return False
    return True


SIG_LINE = 1.9  # a signature's lines: heavier than LINE
SIG_HALO = 5.2  # and the soft band of ink under them
SIG_HALO_OPACITY = 0.16


def signature_parts(d: Diagram, sig: dict) -> tuple:
    """A panel's signature (`signature`): its defining part, the lines and cells that make it what it is. `styles` picks
    lines by style, narrowed to those touching `cells` when both are given; `cells` alone picks
    the lines joining two of those cells (not a stem, which touches one); `ring` sets a soft disc behind cells (BERT's masked token).
    """

    def keys_of(refs):
        out = set()
        for r in refs:
            k = ref(d, r)
            out |= set(d.groups[k]) if k in d.groups else {k}
        return out

    styles, keys = set(sig.get("styles", [])), keys_of(sig.get("cells", []))
    segs = []
    for s in d.segs:
        ends = set(s.ends) | ({s.dst} if s.dst else set()) | set(s.src)
        if styles and s.style in styles and (not keys or ends & keys):
            segs.append(s)
        elif not styles and keys and len(ends) > 1 and ends <= keys:  # joins two picked cells
            segs.append(s)
    return segs, [d.cells[k] for k in sorted(keys_of(sig.get("ring", [])))]


def signature_svg(d: Diagram, sig: dict, marker_id: str, ink: str = "currentColor") -> tuple:
    """A signature drawn as two layers, under and over the drawing: a soft band of ink under its
    lines and behind its ringed cells, and its lines again at SIG_LINE. In ink, not a new hue, so
    it holds in grayscale; the page shows it only while its Highlight toggle is on."""
    segs, rings = signature_parts(d, sig)

    def shape(s, extra):
        if s.style == "func":
            return f'<path d="{squiggle(s.pts[0], s.pts[-1])}"{extra}/>'
        if s.d:
            return f'<path d="{s.d}"{extra}/>'
        (x0, y0), (x1, y1) = s.pts[0], s.pts[-1]
        return f'<line x1="{fmt(x0)}" y1="{fmt(y0)}" x2="{fmt(x1)}" y2="{fmt(y1)}"{extra}/>'

    def dash(s):  # the style's dash, opened up for the heavier line
        st = LINKS[s.style]
        if "dash" not in st:
            return ""
        return (
            ' stroke-dasharray="' + " ".join(fmt(float(v) * 1.6) for v in st["dash"].split()) + '"'
        )

    discs = "".join(
        f'<circle cx="{fmt(c.x)}" cy="{fmt(c.y)}" r="{fmt(R + 5.5)}" fill="{ink}" fill-opacity="{SIG_HALO_OPACITY}"/>'
        for c in rings
    )
    halos = "".join(shape(s, "") for s in segs)
    over = "".join(
        shape(s, dash(s) + (f' marker-end="url(#{marker_id})"' if s.arrow else "")) for s in segs
    )
    g = f'fill="none" stroke="{ink}" stroke-linecap="round" stroke-linejoin="round"'
    return (
        f'<g class="sig" aria-hidden="true">{discs}<g {g} stroke-width="{SIG_HALO}" '
        f'stroke-opacity="{SIG_HALO_OPACITY}">{halos}</g></g>',
        f'<g class="sig" aria-hidden="true"><g {g} stroke-width="{SIG_LINE}">{over}</g></g>',
    )


def render_diagram(
    d: Diagram,
    marker_id: str,
    ink: str = "currentColor",
    tags: bool = False,
    weights: dict | None = None,
    named: bool = False,
    sig: dict | None = None,
) -> str:
    """A diagram's lines, dots and cells. With `tags`, each part names its key entry
    (data-k="cell:hidden", "link:state"), so the page can light up the parts a key entry
    stands for, and its step in the data flow (data-s, see flow_steps), so the page can
    step through a pass. With `weights` ({(from, to): w}), each cell names itself (data-c) and
    each line its ends and weight (data-e, data-w), for the primer's live network; `named`
    names the cells and lines' ends the same way, without weights (a primer figure's tips).
    With `sig` (a panel's `signature`), its two layers go under and over the rest."""

    def tag(k):
        return f' data-k="{k}"' if tags else ""

    last = max(d.steps.values(), default=0)

    def at(n, keys=()):  # a part's step, for the step-through
        if tags and d.seq:
            return seq_marks(d, keys)
        return f' data-s="{n}"' if tags and d.steps else ""

    def both(s):  # a comparison draws in from both ends at once, meeting in the middle
        return ' data-both=""' if tags and compares(d, s) else ""

    def seg_step(s):
        if s.style == "loop" and not s.dst:  # the return line, after the pass
            return last + 1
        if compares(d, s):  # the step after both sides are ready
            return max(d.steps.get(k, 0) for k in s.ends) + 1
        dst = s.dst or (s.ends[1] if len(s.ends) == 2 else s.ends[0] if s.ends else "")
        return d.steps.get(dst, 0)

    out = []
    for style in STYLE_ORDER:
        segs = [s for s in d.segs if s.style == style and not s.late]
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
            op += at(seg_step(s), s.ends) + both(s) + (' data-late=""' if tags and s.late else "")
            if s.tone:
                op += f' class="tone-{s.tone}" stroke="{TONES[s.tone]}"'
            if tags and unfocused(d, s):
                op += ' data-dim=""'
            if tags and d.slide and set(s.ends) & d.slide[0]:
                op += ' data-slide=""'
            if tags and s.t1 is not None:
                op += f' data-t0="{s.t0:.3f}" data-t1="{s.t1:.3f}"'
            if weights is not None and tuple(s.ends) in weights:
                op += f' data-e="{s.ends[0]} {s.ends[1]}" data-w="{weights[tuple(s.ends)]}"'
            elif named and len(s.ends) == 2:
                op += f' data-e="{s.ends[0]} {s.ends[1]}"'
            if style == "func":
                items.append(f'<path d="{squiggle(s.pts[0], s.pts[-1])}"{op}/>')
            elif s.d:
                items.append(f'<path d="{s.d}"{op}{mk}/>')
            else:
                (x0, y0), (x1, y1) = s.pts[0], s.pts[-1]
                items.append(
                    f'<line x1="{fmt(x0)}" y1="{fmt(y0)}" x2="{fmt(x1)}" y2="{fmt(y1)}"{op}{mk}/>'
                )
        out.append(f'<g{tag("link:" + st.get("legend_as", style))} {attrs}>{"".join(items)}</g>')
    dot_arrive = ' data-arrive="0.5"' if tags else ""  # a gate's dot, halfway along its arrow
    if d.dots:
        dim = ' data-dim=""' if tags and d.focus and "gate" not in d.focus["styles"] else ""
        dots = "".join(
            f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(DOT)}"{at(d.steps.get(k, 0))}'
            f"{dot_arrive}{dim}/>"
            for (x, y, _), k in zip(d.dots, d.dot_dst)
        )
        out.append(f'<g{tag("link:gate")} fill="{ink}">{dots}</g>')
    for kind in CELLS:
        for c in d.cells.values():
            if c.kind == kind:
                body = cell_svg(kind, c.x, c.y, c.opacity, ink, c.mark_dir, c.marks)
                if weights is not None or named:
                    body = f'<g data-c="{c.key}">{body}</g>'
                arrive = f' data-arrive="{c.arrive:.3f}"' if tags and c.arrive is not None else ""
                if tags and d.slide and c.key in d.slide[0]:
                    arrive += ' data-slide=""'
                if tags and unfocused(d, cell=c.key):
                    arrive += ' data-dim=""'
                out.append(
                    f'<g{tag("cell:" + kind)}{at(d.steps.get(c.key, 0), (c.key,))}{arrive}>{body}</g>'
                    if tags
                    else body
                )
    late = ' data-late=""' if tags else ""  # lit with its output, as one long stem
    # the line from an output up to a return line belongs to the output's stem as much as to the
    # loop: it lights with the output, and with the key's entries for both
    for s in (s for s in d.segs if s.late):
        cell = d.cells[s.ends[0]]
        loop_k = "link:" + LINKS[s.style].get("legend_as", s.style)
        fade = "" if s.opacity == 1 else f' opacity="{fmt(s.opacity)}"'
        (x0, y0), (x1, y1) = s.pts[0], s.pts[-1]
        mk = f' marker-end="url(#{marker_id})"' if s.arrow else ""  # the arrow into an input
        out.append(
            f'<line{tag("cell:" + cell.kind + " " + loop_k)}{at(seg_step(s), s.ends)}{late} '
            f'x1="{fmt(x0)}" y1="{fmt(y0)}" x2="{fmt(x1)}" y2="{fmt(y1)}" stroke="{ink}" '
            f'stroke-width="{LINE}" stroke-linecap="round"{fade}{mk}/>'
        )
    for (
        p,
        q,
        op,
        kind,
    ), key in zip(
        d.stems, d.stem_cells
    ):  # stems set apart from their cells, past a whole-row return line
        fade = "" if op == 1 else f' opacity="{fmt(op)}"'
        out.append(
            f'<line{tag("cell:" + kind)}{at(d.steps.get(key, last), (key,))}{late} x1="{fmt(p[0])}" y1="{fmt(p[1])}" x2="{fmt(q[0])}" y2="{fmt(q[1])}" '
            f'stroke="{ink}" stroke-width="1.1" stroke-linecap="round"{fade}/>'
        )
    for keys, letter in d.names:  # in italic, as in the objective
        cs = [d.cells[k] for k in keys]
        if len(cs) > 1:  # centered among the row's cells
            x, anchor = sum(c.x for c in cs) / len(cs), "middle"
        else:  # at the cell's left
            x, anchor = cs[0].x - half_of(CELLS[cs[0].kind], R) - 3.5, "end"
        label = svg_text(
            x, cs[0].y + 3.5, f"`{letter}`", "body", 10, ink, anchor=anchor, opacity=0.75
        )
        # lit and faded with its cells: in a step-through, and when their key entry is picked
        step = at(d.steps.get(keys[0], 0), tuple(keys))
        out.append(f'<g{tag("cell:" + cs[0].kind)}{step}>{label}</g>' if tags else label)
    if sig:
        under, over = signature_svg(d, sig, marker_id, ink)
        out = [under] + out + [over]
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
    s = str(s)
    if "`" not in s:
        return f"<text {attrs}>{html.escape(s)}</text>"
    # With a `variable` in it, the text is set as runs, each placed at its measured position:
    # a renderer such as cairosvg misplaces an italic <tspan> inside centered text.
    runs = [
        (r[1:-1], True) if r.startswith("`") else (r, False)
        for r in re.split(r"(`[^`]+`)", s)
        if r
    ]
    widths = [text_width(t, role, size, weight, italic) for t, italic in runs]
    total = sum(widths)
    x0 = x - (total / 2 if anchor == "middle" else total if anchor == "end" else 0.0)
    base = re.sub(r' text-anchor="[^"]*"', "", re.sub(r'^x="[^"]*" ', "", attrs))
    out = []
    for (t, italic), w in zip(runs, widths):
        style = ' font-style="italic"' if italic else ""
        out.append(
            f'<text x="{fmt(x0)}" {base}{style} xml:space="preserve">{html.escape(t)}</text>'
        )
        x0 += w
    return "".join(out)


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


def legend_pitch(groups: list) -> float:
    """The distance between legend columns: a panel column's, or, with more groups than panel
    columns, an even share of the width left once the last column (Links) has room for its
    longest label."""
    if len(groups) <= POSTER_COLS:
        return W + POSTER_GX
    inner = POSTER_COLS * W + (POSTER_COLS - 1) * POSTER_GX
    last = max(
        (LEGEND_LINK_X if kind == "link" else LEGEND_CELL_X)
        + text_width((LINKS if kind == "link" else CELLS)[k]["label"], "body", TYPE["legend"][0])
        for kind, k in groups[-1][1]
    )
    return (inner - last) / (len(groups) - 1)


def legend_columns(cells: list, links: list, x0: float, ink: str, mid: str):
    """Poster legend: each group sits in its own column, aligned with the panel columns; with
    more groups than panel columns, the columns share the same width evenly instead."""
    out, height = [], 0.0
    groups = legend_groups(cells, links)
    pitch = legend_pitch(groups)
    for ci, (label, items) in enumerate(groups):
        x = x0 + ci * pitch
        top = 0.0
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


def related_of(doc: dict, aid: str) -> list:
    """The panels related to `aid`, from both ends: [{label, ids}], in RELATIONS order, each
    relation's own label first and then its reverse, with the ids in poster order."""
    arch = doc["architectures"]
    order = [p for s in doc["sections"] for p in s["panels"]]
    groups = []
    for rel, (label, back) in RELATIONS.items():
        fwd = (arch[aid].get("related") or {}).get(rel, [])
        rev = [o for o in arch if aid in ((arch[o].get("related") or {}).get(rel, []))]
        for lab, ids in ((label, fwd), (back, rev)):
            if ids:
                groups.append({"label": lab, "ids": sorted(ids, key=order.index)})
    return groups


def poster_svg(doc: dict, diagrams: dict, theme: str = "light") -> str:
    ink, bg = (INK_LIGHT, BG_LIGHT) if theme == "light" else (INK_DARK, BG_DARK)
    arch = doc["architectures"]
    M, GX, COLS = POSTER_M, POSTER_GX, POSTER_COLS
    width = 2 * M + COLS * W + (COLS - 1) * GX
    parts, y = [], M
    parts.append(type_text(M, y + 38, doc["title"], "title", "display", ink, 500))
    parts.append(
        type_text(M, y + 78, doc.get("poster_subtitle", doc["subtitle"]), "subtitle", "body", ink)
    )
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
    part = None
    for row in pack_rows(poster_sections(doc)):
        head = row[0][0].get("part")
        if head and head != part:  # a part opens: its label over a rule the full width
            part = head
            parts.append(
                type_text(
                    M, y + 14, doc["parts"][head].upper(), "part", "display", ink, 500, spacing=1.6
                )
            )
            parts.append(
                f'<line x1="{fmt(M)}" y1="{fmt(y + 26)}" x2="{fmt(width - M)}" y2="{fmt(y + 26)}" '
                f'stroke="{ink}" stroke-opacity="0.5" stroke-width="1.5"/>'
            )
            y += 50
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
        ("path", "Reading path, start here"),
        ("year", "By year, oldest first"),
        ("year-desc", "By year, newest first"),
        ("name", "By name, A to Z"),
        ("objective", "By objective"),
    )
)

# Published next to the web page; its Save buttons hand these files to the viewer.
POSTER = "neural-network-field-guide"  # the posters' file name in dist/, before .svg, .pdf, .png
POSTER_FILES = [  # file, its format, and a word on which to choose (the page's Poster menu)
    (f"{POSTER}.pdf", "PDF", "for print"),
    (f"{POSTER}.png", "PNG", "light"),
    (f"{POSTER}-dark.png", "PNG", "dark"),
]

# The page's stylesheet and script live beside this file, in assets/, so editors treat them as
# CSS and JavaScript; the build inlines them into the page as they are.
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def asset(name: str) -> str:
    with open(os.path.join(ASSETS, name), encoding="utf-8", newline="") as fh:
        return fh.read()


PAGE_CSS = asset("page.css")

PAGE_JS = asset("page.js")


def symbol_svg(kind: str, name: str) -> str:
    """A legend or key icon: one cell with its mark, or a sample of one link style."""
    inner = (
        cell_svg(name, 12, 0)
        if kind == "cell"
        else link_sample(name, 1, 0, "currentColor", "arr-legend")
    )
    return f'<svg viewBox="-2 -13 28 26" aria-hidden="true" focusable="false">{inner}</svg>'


def legend_html(cells: list, links: list) -> str:
    def li(kind, name, label):  # each entry lights up the diagrams that use it (page script)
        return (
            f'<li data-k="{kind}:{name}" tabindex="0" role="button" aria-pressed="false">'
            f"{symbol_svg(kind, name)}{html.escape(label)}</li>"
        )

    groups = []
    for label, items in legend_groups(cells, links):
        lis = "".join(
            li(kind, k, CELLS[k]["label"] if kind == "cell" else LINKS[k]["label"])
            for kind, k in items
        )
        gid = "lg-" + re.sub(r"[^a-z]+", "-", label.lower()).strip(
            "-"
        )  # names its list for a screen reader
        groups.append(
            f'<div class="lg-group"><p class="lg-title" id="{gid}">{html.escape(label)}</p>'
            f'<ul aria-labelledby="{gid}">{lis}</ul></div>'
        )
    return (
        f'<details class="legend" open><summary class="eyebrow">How to read the diagrams</summary>'
        f'<div class="legend-grid">{"".join(groups)}</div></details>'
    )


NEW_TAB = '<span class="sr-only"> (opens in a new tab)</span>'  # a link's warning, heard only


def credit_html(doc: dict) -> str:
    text = html.escape(doc["credit"])
    link = doc.get("credit_link")
    if link and html.escape(link["text"]) in text:
        anchor = (
            f'<a href="{html.escape(link["url"])}" target="_blank" rel="noopener">'
            f'{html.escape(link["text"])}{NEW_TAB}</a>'
        )
        text = text.replace(html.escape(link["text"]), anchor, 1)
    return text


def repo_html(doc: dict) -> str:
    """ " · github.com/…" for the page's footer, linked; empty without a repo."""
    repo = doc.get("repo")
    if not repo:
        return ""
    return f' · <a href="{html.escape(repo["url"])}" target="_blank" rel="noopener">{html.escape(repo["text"])}{NEW_TAB}</a>'


def source_button(doc: dict) -> str:
    """The title row's link to the repository, an icon button like the theme button."""
    repo = doc.get("repo")
    if not repo:
        return ""
    return (
        f'<a class="icon-btn" href="{html.escape(repo["url"])}" target="_blank" rel="noopener" '
        f'aria-label="Source on GitHub (opens in a new tab)" title="Source on GitHub">{CODE_ICON}</a>\n'
    )


def license_html(doc: dict) -> str:
    """ " · CC BY-SA 4.0" for the page's footer, linked to the license; empty without one."""
    lic = license_of(doc)
    if not lic:
        return ""
    return (
        f' · <a href="{html.escape(lic["url"])}" target="_blank" rel="license noopener">'
        f'{html.escape(lic["text"])}{NEW_TAB}</a>'
    )


DOWNLOAD_ICON = (
    '<svg class="i-dl" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14"/></svg>'
)
CHEVRON_ICON = (
    '<svg class="i-chev" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="m7 10 5 5 5-5"/></svg>'
)


def save_html(mode: str) -> str:
    """The title bar's Poster button and its menu of files. On its own the page links them,
    opened in a new tab where the browser shows them; on claude.ai each is a button that hands
    the file to the viewer's downloads capability, and the whole control stays hidden
    wherever that isn't available."""
    if mode == "standalone":
        items = "".join(
            f'<a role="menuitem" href="../{src}" target="_blank" rel="noopener">{fmt_}'
            f'<span class="sr-only">, </span><span>{note}</span>{NEW_TAB}</a>'
            for src, fmt_, note in POSTER_FILES
        )
    else:
        items = "".join(
            f'<button type="button" role="menuitem" data-file="{src}" data-name="{src}">{fmt_}'
            f'<span class="sr-only">, </span><span>{note}</span></button>'
            for src, fmt_, note in POSTER_FILES
        )
    status = (
        '<span class="save-status" id="save-status" role="status" aria-live="polite"></span>'
        if mode != "standalone"
        else ""
    )
    return (
        f'<div class="save" id="save" data-mode="{mode}"{"" if mode == "standalone" else " hidden"}>'
        '<button class="pm-btn" type="button" id="pm-btn" aria-haspopup="menu" aria-expanded="false" '
        'aria-controls="pm-menu" aria-label="Download the poster" title="Download the poster">'
        f"{DOWNLOAD_ICON}<span>Poster</span>{CHEVRON_ICON}</button>"
        f'<div class="d-menu pm-menu" id="pm-menu" role="menu" aria-label="Download the poster" '
        f"hidden>{items}</div>{status}</div>"
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
# The poster's panels: the page may list more (and more sections) than the poster has room
# for, so the poster keeps its own list, in its own order within each section
POSTER_PANELS = [
    *("perceptron", "mlp", "kan"),
    *("autoencoder", "sparse_ae", "denoising_ae"),
    *("hopfield", "som", "boltzmann", "rbm", "dbn", "modern_hopfield"),
    *("cnn", "resnet", "unet", "wavenet", "rbf", "siamese"),
    *("rnn", "lstm", "birnn", "gru", "rnn_attention", "ntm"),
    *("transformer", "bert", "gpt", "vit", "clip", "diffusion_llm"),
    *("linear_attention", "mamba", "mamba_hybrid"),
    *("gnn", "moe", "capsnet"),
    *("vae", "gan", "normalizing_flow", "diffusion", "flow_matching"),
    "policy_gradient",
]


def poster_sections(doc: dict) -> list:
    """The page's sections, each keeping only its poster panels; a section with none is left
    off the poster."""
    out = []
    for sec in doc["sections"]:
        panels = [p for p in sec["panels"] if p in POSTER_PANELS]
        if panels:
            out.append(dict(sec, panels=panels))
    return out


PREVIEW_PANELS = [  # landmarks across the families, one each, chosen to look unlike one
    # another at thumbnail size (so not both LSTM and Mamba, or diffusion and PPO); drawn in order of
    # year (ties as listed), in two rows of five;
    # the MLP rather than the perceptron, as the picture most people have of a neural network
    "mlp",
    "hopfield",
    "cnn",
    "lstm",
    "vae",
    "unet",
    "diffusion",
    "transformer",
    "gpt",
    "moe",
]


def preview_svg(doc: dict, diagrams: dict) -> str:
    """The link preview: the title and subtitle over a few landmark panels, in two rows, oldest
    first."""
    pw, ph = PREVIEW_SIZE
    arch = doc["architectures"]
    panels = sorted(PREVIEW_PANELS, key=lambda a: arch[a]["year"])  # stable: ties keep their order
    cols = -(-len(panels) // 2)
    m, gap, row_gap, ink = 56, 8, 28, INK_LIGHT  # rows apart, so stems don't meet
    sub = 22
    while text_width(doc["subtitle"], "body", sub) > pw - 2 * m and sub > 14:
        sub -= 1
    scale = (pw - 2 * m - (cols - 1) * gap) / cols / W
    parts = [
        f'<rect width="{pw}" height="{ph}" fill="{BG_LIGHT}"/>',
        svg_text(m, 112, doc["title"], "display", 56, ink, 500),
        svg_text(m, 156, doc["subtitle"], "body", sub, ink, 400, TYPE["subtitle"][1] * 0.75),
    ]
    # the rows start at 198: as far below the subtitle as they end above the address
    for i, aid in enumerate(panels):
        x, y = m + (i % cols) * (W * scale + gap), 198 + (i // cols) * (H * scale + row_gap)
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


# how the preview's alt text counts its panels
NUMBER_WORDS = dict(
    enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())
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
    shown = NUMBER_WORDS.get(len(PREVIEW_PANELS), str(len(PREVIEW_PANELS)))
    return (
        out + f'<link rel="canonical" href="{url}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:title" content="{title}">\n'
        f'<meta property="og:description" content="{desc}">\n'
        f'<meta property="og:url" content="{url}">\n'
        f'<meta property="og:image" content="{url}preview.png">\n'
        f'<meta property="og:image:width" content="{pw}">\n'
        f'<meta property="og:image:height" content="{ph}">\n'
        f'<meta property="og:image:alt" content="{title}: {shown} of its diagrams under the title">\n'
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


# What a circle stands for, by kind (an architecture's `circle`); each completes "In this
# diagram, ...". A panel may give a phrase of its own instead.
CIRCLES = {
    "unit": "each circle is one unit, a single number",
    "seq": "each circle is one position's vector at one layer: a token, or a time step",
    "grid": (
        "each circle is one position's features at one layer, all its channels together: a pixel, or an audio sample"
    ),
    "sample": (
        "each circle in the middle column is a whole sample, such as an image, at one step of generation"
    ),
    "node": "each circle is one node of a graph, its vector at one step",
}


def circle_text(a: dict) -> str:
    c = a.get("circle", "")
    return f"In this diagram, {CIRCLES.get(c, c)}." if c else ""


def sig_note_html(a: dict) -> str:
    """The highlight's note under a card's caption, shown only while the Highlight toggle is on
    (by the page's style, so a screen reader hears it only then too)."""
    note = (a.get("signature") or {}).get("note", "")
    return f'<span class="snote">Highlighted: {prose_html(note)}</span>' if note else ""


def path_places(doc: dict) -> dict:
    """Each panel's place on the reading path: its main step's number, its label (4, or 4a for
    a side trip), its line, and a rank that sorts main steps and their branches in order."""
    out = {}
    for i, p in enumerate(doc.get("path", []), 1):
        out[p["id"]] = dict(
            step=i, label=str(i), text=p["text"], title=p.get("title", ""), main=True, rank=i * 100
        )
        for j, b in enumerate(p.get("branches", []), 1):
            out[b["id"]] = dict(
                step=i, label=f"{i}{chr(96 + j)}", text=b["text"], main=False, rank=i * 100 + j
            )
    return out


def web_page(doc: dict, diagrams: dict, mode: str = "artifact") -> str:
    """mode='artifact': a fragment for publishing; mode='standalone': a complete HTML file."""
    arch = doc["architectures"]
    fam_of = {p: s for s in doc["sections"] for p in s["panels"]}
    titles = {s["id"]: s["title"] for s in doc["sections"]}
    notes = footnotes(doc)
    # the reading path (`path`): each panel's place on it and the line on what it adds
    path_at = path_places(doc)
    data = OrderedDict()
    sections_html = []
    for s in doc["sections"]:
        cards = []
        for pid in s["panels"]:
            a, d = arch[pid], diagrams[pid]
            mid = f"arr-{pid}"
            caption = caption_text(notes, pid, a)
            svg = (
                f'<svg viewBox="0 0 {fmt(W)} {fmt(H)}" aria-hidden="true" focusable="false" '
                f'data-steps="{flow_count(d)}"{SEQ_ATTR if d.seq else ""}><defs>{marker_def(mid, "currentColor")}</defs>'
                f"{render_diagram(d, mid, tags=True, sig=a.get('signature'))}</svg>"
            )
            step = path_at.get(pid)
            pnote = (
                f'<span class="pnote{"" if step["main"] else " side"}" hidden>'
                f'<span class="pnum">{step["label"]}</span> {prose_html(step["text"])}</span>'
                if step
                else ""
            )
            cards.append(
                f'<button class="card" type="button" data-id="{pid}"{has_attr(a)}>{svg}'
                # unseen punctuation, so a screen reader names the card as a sentence, not a run of words
                f'<span class="row"><span class="name">{html.escape(a["name"])}</span>'
                f'<span class="year"><span class="sr-only">, landmark paper </span>{a["year"]}<span class="sr-only">. </span></span></span>'
                f'<span class="cap">{prose_html(caption)}</span>'
                f"{sig_note_html(a)}"
                f'<span class="tag"><span class="sr-only">. {"Objective" if a.get("scheme") else "Typical objective"}: </span>'
                f'{html.escape(a["objective"])}</span>{pnote}</button>'
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
                    *a.get("also_objectives", []),
                    s["title"],
                    *(titles[f] for f in a.get("also", [])),
                    *(n for f in NAME_LISTS for n in a.get(f, [])),
                    a.get("used_for", ""),
                ]
            )
            text = plain(text).lower()
            used_cells = {c.kind for c in d.cells.values()}
            used_links = {s.style for s in d.segs}
            data[pid] = dict(
                name=a["name"],
                full=a.get("full", ""),
                year=a["year"],
                family=fam_of[pid]["id"],
                familyTitle=fam_of[pid]["title"],
                also=a.get("also", []),
                alsoTitles=[titles[f] for f in a.get("also", [])],
                caption=plain(caption),
                captionHtml=prose_html(caption),
                say=[prose_html(t) for t in d.say],  # the step-through's captions, if any
                objective=a["objective"],
                objectiveGloss=objective_gloss(doc, a["objective"]),
                has=has_text(a),
                objectiveNote=a.get("objective_note", ""),
                objectiveMath=math_html(a.get("objective_math") or OBJECTIVE_MATH[a["objective"]]),
                alsoObjectives=a.get("also_objectives", []),
                # an architecture, not a way of training: its objective is one of several
                scheme=bool(a.get("scheme")),
                usedForHtml=prose_html(a.get("used_for", "")),  # what it is used for, now and once
                # other names, variants, and related methods, each under its own label
                names=[[label, a[f]] for f, label in NAME_LISTS.items() if a.get(f)],
                related=related_of(doc, pid),
                # its place on the reading path (0 if off it), and the main step it hangs off
                path=path_at[pid]["rank"] if pid in path_at else 0,
                pathStep=path_at[pid]["step"] if pid in path_at else 0,
                sigNote=prose_html((a.get("signature") or {}).get("note", "")),  # escaped here
                circle=prose_html(circle_text(a)),  # what a circle stands for, atop the key
                pathTitle=path_at[pid].get("title", "") if pid in path_at else "",
                pathMain=bool(path_at.get(pid, {}).get("main")),
                note=" ".join(notes[pid]) if pid in notes else "",
                summary=plain(a["summary"].strip()),
                read=plain(a.get("read", "").strip()),
                # glossary terms link to their entries, the first use of each in the summary
                # and again in the reading, which sits apart, under the drawing
                summaryHtml=link_terms(
                    doc, [prose_html(a["summary"].strip())], pid, PANEL_UNLINKED
                )[0],
                readHtml=link_terms(
                    doc, [prose_html(a.get("read", "").strip())], pid, PANEL_UNLINKED
                )[0],
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
    # the glossary for the page's search to suggest from: each entry's anchor, name, the forms
    # that link to it, and its definition
    gloss = [
        dict(
            g=glossary_slug(e["term"]),
            t=e["term"],
            f=[f.lower() for f in e.get("match", [])],
            d=glossary_hint(e),
        )
        for e in glossary_entries(doc)
    ]
    gloss_json = json.dumps(gloss, ensure_ascii=False).replace("</", "<\\/")
    defs = f"<defs>{marker_def('arr-legend', 'currentColor')}</defs>"
    body = f"""<div class="page">
<button class="skip" type="button" id="skip">Skip to the diagrams</button>
<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">{defs}</svg>
<header class="masthead">
<div class="titlebar"><h1>{html.escape(doc["title"])}</h1>
{save_html(mode)}
<button class="icon-btn" type="button" id="help" aria-label="What is a neural network?" title="What is a neural network?" aria-haspopup="dialog"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 0 1 4.9.7c0 1.7-2.5 2.2-2.5 3.8"/><circle cx="12" cy="17" r="0.6" fill="currentColor"/></svg></button>
{glossary_button(doc)}{source_button(doc)}<button class="icon-btn" type="button" id="theme" aria-label="Switch to dark mode" title="Switch to dark mode">{THEME_ICONS}</button></div>
<div><p class="lede">{html.escape(doc["subtitle"])}</p>
<p class="reading">{html.escape(doc["reading"])} Select a diagram for its explanation, key, and papers.</p></div>
{legend_html(cells, links)}
</header>
<nav class="filters" aria-label="Filter diagrams">
<div class="controls">
<select id="f-family" aria-label="Family">{families}</select>
<select id="f-order" aria-label="Order">{ORDER_OPTIONS}</select>
<select id="f-objective" aria-label="Training objective">{options}</select>
<button class="icon-btn pack" type="button" id="f-sig" aria-pressed="false" aria-label="Highlight each diagram's defining part" title="Highlight each diagram's defining part"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 21h8"/><path d="M15 4l5 5-8.5 8.5H7v-4.5z"/><path d="M13 6l5 5"/></svg></button>
<button class="icon-btn pack" type="button" id="f-pack" aria-pressed="false" aria-label="One section per row" title="One section per row"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3.5" width="18" height="4.5" rx="1.2"/><rect x="3" y="9.75" width="18" height="4.5" rx="1.2"/><rect x="3" y="16" width="18" height="4.5" rx="1.2"/></svg></button>
<input id="f-search" type="search" placeholder="Search, e.g. memory" aria-label="Search diagrams and the glossary">
<span class="count" id="f-count" aria-live="polite"></span>
</div>
<p class="g-suggest" id="f-gloss" hidden></p>
</nav>
<main>
<div id="groups">{"".join(sections_html)}</div>
<div class="empty" id="empty" hidden><span>No diagrams match these filters.</span>
<button class="btn" type="button" id="clear">Clear filters</button></div>
</main>
<footer><span>{credit_html(doc)}</span><span>{html.escape(notice(doc))}{license_html(doc)}{repo_html(doc)} · Last updated <time datetime="{page_updated_date(doc).isoformat()}">{date_text(page_updated_date(doc))}</time></span></footer>
</div>
{primer_html(doc, diagrams)}
{glossary_html(doc, diagrams)}
<dialog id="detail" aria-labelledby="d-name">
<div class="d-wrap">
<div class="d-text d-top">
<div class="d-head"><p class="d-kicker" id="d-kicker"></p><button class="d-link" type="button" id="d-link" aria-label="Copy a link to this entry" title="Copy link" hidden><span class="d-link-done" aria-live="polite"></span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7.1-7.1l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7.1 7.1l1.7-1.7"/></svg></button></div>
<h2 id="d-name" tabindex="-1"></h2>
<p class="d-full" id="d-full"></p>
<p class="d-cap" id="d-cap"></p>
<p class="d-snote" id="d-snote"></p>
<p class="d-note" id="d-note" hidden></p>
</div>
<div class="d-side">
<div class="d-tools"><h3 class="d-label">Diagram</h3>
<button class="d-more" type="button" id="d-more" aria-label="Diagram actions" title="Diagram actions" aria-haspopup="menu" aria-expanded="false" aria-controls="d-menu" data-credit="{html.escape(panel_credit(doc))}" data-ink="{INK_LIGHT}"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg></button>
<div class="d-menu" id="d-menu" role="menu" aria-label="Diagram actions" hidden>
<button type="button" role="menuitem" data-act="svg">Save as SVG</button>
<button type="button" role="menuitem" data-act="png">Save as PNG</button>
</div>
</div>
<div class="d-fig" id="d-fig"></div>
<div class="p-fig-ctl d-fig-ctl"><div class="d-flowctl" id="d-flowctl" data-state="idle" hidden><button class="d-stop" type="button" id="d-stop" aria-label="Stop" title="Stop"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg></button><button class="d-back" type="button" aria-label="Previous step" title="Previous step"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="2.5" height="12" rx="1"/><path d="M18 6v12l-8.5-6z"/></svg></button><button class="d-play" type="button" id="d-play"><svg class="i-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z"/></svg><svg class="i-pause" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="6" width="4" height="12" rx="1"/><rect x="13.5" y="6" width="4" height="12" rx="1"/></svg><span>Step through</span></button><button class="d-fwd" type="button" aria-label="Next step" title="Next step"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="15.5" y="6" width="2.5" height="12" rx="1"/><path d="M6 6v12l8.5-6z"/></svg></button><button class="d-rate" type="button" aria-label="Step-through speed: 1×" title="Step-through speed">1×</button></div>
<p class="p-say d-say" id="d-say" aria-live="polite" hidden></p></div>
<div class="d-keybox"><h3 class="d-label">Key</h3><p class="d-circle" id="d-circle"></p><div class="d-key" id="d-key"></div><p class="d-has" id="d-has" hidden></p></div>
<div class="d-read" id="d-read-wrap"><h3 class="d-label">Reading the diagram</h3><p id="d-read"></p></div>
</div>
<div class="d-text d-body">
<h3 class="d-over" id="d-over">Overview</h3>
<p id="d-summary"></p>
<div id="d-uses" hidden><h3>Used for</h3><p id="d-uses-text"></p></div>
<div id="d-rel-wrap"><h3>Related</h3><div class="d-rel" id="d-rel"></div></div>
<h3>Objective</h3><p id="d-objective"></p><p class="d-math" id="d-math"></p><p class="d-scope" id="d-scope" hidden>A typical way to train it, not always its landmark paper's exact loss; the same wiring can be trained toward <a href="#primer-goals">other goals</a>.</p>
<h3>Papers</h3><ul class="d-papers" id="d-papers"></ul>
</div>
<div class="d-nav">{NAV_MORE}<button class="btn" type="button" id="d-prev">Previous</button>
<button class="btn" type="button" id="d-next">Next</button>
<button class="btn" type="button" id="d-close">Close</button></div>
</div>
</dialog>
<script type="application/json" id="guide-data">{data_json}</script>
<script type="application/json" id="gloss-data">{gloss_json}</script>
<script type="application/json" id="guide-symbols">{symbols_json}</script>
<script>
{PAGE_JS}</script>"""
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
        f"<style>\n{figure_palette_css()}{PAGE_CSS}</style>\n"
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
    template = doc["subtitle"]
    doc["subtitle"] = subtitle_text(doc, template, doc["sections"])  # the page's
    doc["poster_subtitle"] = subtitle_text(doc, template, poster_sections(doc))
    return doc


def subtitle_text(doc: dict, template: str, sections: list) -> str:
    """The subtitle with its counts and ends filled in from the chart: {count} is the number
    of panels, {first} and {last} the oldest and newest (ties go to the one placed first, or
    last, on the poster), each as its `in_text` reads in a sentence or else its name, and
    {first_year} and {last_year} their years."""
    arch = doc["architectures"]
    placed = [p for s in sections for p in s["panels"] if p in arch]
    if not placed:
        return template
    first = min(placed, key=lambda p: arch[p]["year"])
    last = max(reversed(placed), key=lambda p: arch[p]["year"])
    said = lambda p: arch[p].get("in_text") or arch[p]["name"]
    return template.format(
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


def chart_dates(doc: dict, diagrams: dict, out: str) -> tuple:
    """The days to print as "Last updated", on the poster and on the page: each the date of
    the last build, kept when this build draws it exactly as that one did, and today when it
    changed. The poster and the page are dated apart, so a change to the page alone (its
    explanations, primer or code) leaves the poster's date, and rebuilding without a change
    leaves dist/ as it was."""
    today = datetime.date.today()

    def read(name):
        try:
            with open(os.path.join(out, name), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    poster, page = read(f"{POSTER}.svg"), read(os.path.join("web", "index.html"))
    m = poster and re.search(r"Last updated (\d{1,2}) (\w+) (\d{4})", poster)
    try:
        last = datetime.date(int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1)))
    except (AttributeError, TypeError, ValueError):
        last = None
    poster_day = (
        last
        if last and poster_svg(dict(doc, updated=last), diagrams, "light") == poster
        else today
    )
    m = page and re.search(r'<time datetime="([\d-]+)">', page)
    try:
        last = datetime.date.fromisoformat(m.group(1))
    except (AttributeError, TypeError, ValueError):
        last = None
    same = dict(doc, updated=poster_day, page_updated=last)
    page_day = last if last and web_page(same, diagrams, "standalone") == page else today
    return poster_day, page_day


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
    if doc.get("updated") is None:  # set by hand in architectures.yaml, it wins for both
        doc["updated"], doc["page_updated"] = chart_dates(doc, diagrams, args.out)
    print(f"Poster: {updated_text(doc)}; page: last updated {date_text(page_updated_date(doc))}")
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
