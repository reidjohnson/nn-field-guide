# Neural Network Field Guide

[![The Neural Network Field Guide: ten of its diagrams under the title](dist/web/preview.png)](https://reidjohnson.github.io/nn-field-guide/)

**[See the guide](https://reidjohnson.github.io/nn-field-guide/)** · [Poster (PDF)](https://reidjohnson.github.io/nn-field-guide/neural-network-field-guide.pdf)

A reference chart of neural network architectures and training schemes, from the perceptron (1958) to diffusion LLMs (2025). Inspired by *The Neural Network Zoo*, it draws every network in miniature, with each cell's role shown by both a color and a mark, so it reads with or without color and for color-blind readers.

## How to read it

Each panel runs from input at the bottom to output at the top; a network that settles as a whole (Hopfield, Boltzmann) is drawn as a ring, and a graph network as its graph. A circle is one unit (in sequence models, one token's vector); a square is something bigger, such as an image patch or a whole network (a gray box). Faded cells and links take little or no part in the step shown. The poster's legend and the page explain every mark, and the page's glossary explains the terms (attention, convolution, gates, and more), several with a diagram that steps through how they work. On the page, the highlighter beside the menus marks each diagram's defining part, and the order menu's *Reading path, start here* (or a link to `#start`) lays the diagrams out as a path: a main line of steps, each with a line on what it adds, and after each step its side trips, which take in the rest. In short, each cell's color and mark give its job:

| Job | Color | Mark |
|---|---|---|
| Input: data coming in | yellow | a stem below |
| Hidden: transforms what it receives | green | none |
| State: carries memory between steps | blue | an outer ring |
| Mixing: mixes across positions | pink | a tick on each side |
| Output: what the network produces | red | a stem above |
| Network: a whole network, any architecture (e.g., a generator, a denoiser, an expert) | dark gray | a tiny network inside a square, in white; an outlined square is the world outside (a reinforcement learner's environment) |

And each line's style gives what it carries:

| Link | Line | Meaning |
|---|---|---|
| Connection | thin solid line | a learned weight from one cell to another |
| Skip connection | solid elbow beside the layers | data carried around one or more layers, then added or joined back |
| Data passed on or fed back | arrow | state passed to the next step (straight), or an output fed back to run the network again (elbow, or an arrowhead on an input's stem) |
| Gate | dotted line ending at a dot | a value from 0 to 1 that scales the arrow it meets at the dot |
| Similarity score | dashed line | a weight set by how well two things match, not learned: attention's reads, and comparisons such as CLIP's |
| Learnable function | wavy line | a whole learned function on the connection, not a single weight (KAN) |

## Building it

Everything is drawn by `guide.py` from `architectures.yaml`, so the panels, the poster, and the page always match; the page's stylesheet and script are in `assets/`, inlined into the page at build time. It needs Python 3.10 or newer, and for exact PDF and PNG output the fonts Poppins, Lato 2.0 (from latofonts.com, for its Medium weight), and Noto Sans Mono.

```sh
pip install -r requirements.txt
pre-commit install               # once: format and check each commit
python guide.py build --strict   # lint, then write dist/
python guide.py lint             # checks only
```

Lint checks every drawing (lines that stray near cells, marks that touch, text too wide for its column) and the metadata. The "Last updated" date and the subtitle's count and range fill themselves in.

Browser tests build the page and drive it in Chromium: every panel's and glossary figure's step-through, every glossary link, the Back and Forward paths between the page, its panels, the glossary, and the primer, and that the page reads in order by keyboard and to a screen reader. GitHub runs them on every push.

```sh
pip install -r requirements-dev.txt
playwright install chromium      # once
python -m pytest tests           # the environment's own pytest, not a global one
```

Three more scripts help weigh changes before making them. They write to `proofs/`, a local folder that isn't committed:

```sh
python revision.py --snapshot    # before a round of changes
python revision.py               # every panel that changed since, before and after
python proposals.py              # draft changes from proofs/proposals.yaml, not applied
python palettes.py               # the chart in other color schemes, as color-blind readers see them
```

## Adding an architecture

Add an entry under `architectures:` and list its id in a section. Rows run from the bottom (inputs) to the top (outputs), and layout is automatic. The comment at the top of `architectures.yaml` lists every option.

```yaml
  bert:
    name: BERT
    year: 2018
    caption: Every token sees every token     # one line, checked for width
    objective: masked token                   # one of OBJECTIVES in guide.py
    signature: {styles: [cross], cells: [[r0, 2]], ring: [[r0, 2]]}   # what the page's highlighter marks
    summary: A Transformer encoder on its own...
    papers:
      - {cite: Devlin et al. 2018, title: "BERT: ...", url: "https://arxiv.org/abs/1810.04805"}
    diagram:
      rows:
        - {cell: [input, input, mask, input]}
        - {cell: attention, n: 4, connect: full, style: cross}   # dashed: weights set by similarity
        - {cell: attention, n: 4, connect: full, style: cross}
        - {cell: output, n: 4, connect: one, dim: [0, 1, 3]}
```

Every panel needs a `signature`, its defining part: lines by `styles` (narrowed to those touching `cells` when both are given), the lines joining `cells` alone, and soft discs behind `ring` cells, with a `note` the page shows under the caption while the highlighter is on ("Highlighted: …"). Lint checks each picks something out and has its note. A few row options came with it: `fed` draws an input's stem as an arrow, for an input that was an earlier output (GPT, WaveNet, a decoder); `gate_also` adds a gate from a row above (LSTM's hidden state); and a loop with `solid: true` stays full strength into a faded cell (GPT's next position).

Every panel goes on the reading path, under `path:`: as a main step, with a `title` naming what it and its side trips share and one line on what it adds, or under a step's `branches:` as a side trip, with one line on what it changes. Lint checks each is a panel, listed once, and that none is left off.

## Adding a glossary entry

Add it under `glossary:` in `architectures.yaml`, in its group. Its first sentence is the definition, shown on hover wherever the term is linked; the panels' text and the other entries link to it by the forms under `match`. `used_in` lists the panels that use it and `sources` the works it comes from. A figure is optional: a small network drawn like a panel, or a plane, chart, or bar figure, each stepping through with a caption at every step, and each with an `alt`: one sentence saying what it shows, which is what a screen reader hears for it (lint asks for one). The comments above `figures:` and `glossary:` list every option.

```yaml
  - term: Pooling
    group: operations
    figure: pooling_max              # one of figures:
    labels: [Feature map, Pooled]    # its rows, bottom to top
    match: [pooling]
    used_in: [cnn]
    sources: ["Fukushima 1980"]      # a panel's cite links to that panel
    text: >-
      Shrinking a feature map by summarizing each small patch, usually by its largest value...
```

## Publishing

GitHub's runners don't have the fonts, so build locally, commit `dist/`, and push to `main`; the `pages` workflow publishes the page, posters, and panels to GitHub Pages.

## License

- **Code: MIT** ([`LICENSE-MIT`](LICENSE-MIT)): the Python scripts and the scripts and styles they write into the pages.
- **Everything else: CC BY-SA 4.0** ([`LICENSE-CC-BY-SA-4.0`](LICENSE-CC-BY-SA-4.0)): the chart, its text and diagrams, and this README. You may share and adapt it, commercially too, if you credit it, say what you changed, and release your version under the same license.

To credit it: *Neural Network Field Guide* by Reid Johnson, licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), with a link to [github.com/reidjohnson/nn-field-guide](https://github.com/reidjohnson/nn-field-guide). Contributions are accepted on the same terms. *The Neural Network Zoo* belongs to its authors and isn't covered by either license; the fonts keep their own, the SIL Open Font License.
