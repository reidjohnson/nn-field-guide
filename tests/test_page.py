"""Browser tests for the interactive page: it is built into a temporary folder and driven in
Chromium. They cover what lint can't: that every panel and glossary figure steps through
without an error, that every glossary link leads somewhere, and that the browser's Back and
Forward, the dialogs' Close, and the glossary's links move between views as they should, and
that the page and its glossary read in order to a screen reader and by keyboard.

    pip install -r requirements-dev.txt
    playwright install chromium
    python -m pytest tests
"""

import functools
import http.server
import pathlib
import re
import subprocess
import sys
import threading

import pytest
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # no line per request in the test output
        pass


@pytest.fixture(scope="session")
def page_url(tmp_path_factory):
    """The page, built once for the whole run (without --strict: no fonts needed), and served
    over http on a free local port, as visitors get it: Chromium doesn't reliably keep a
    file:// page's localStorage across a reload."""
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "guide.py", "build", "--out", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    handler = functools.partial(QuietHandler, directory=str(out / "web"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, page_url):
    """A fresh page at the fastest step-through speed, failing the test on any script error."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("try { localStorage.setItem('field-guide-speed', 'fast'); } catch (e) {}")
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.url_base = page_url
    yield pg
    ctx.close()
    assert not errors, errors


def go(pg, hash=""):
    pg.goto(pg.url_base + hash)
    pg.wait_for_timeout(300)


def view(pg):
    """What is showing: the address, the open panel's name, and which sheets are open."""
    return pg.evaluate("""() => ({
        hash: location.hash,
        panel: document.getElementById('detail').open
            ? document.getElementById('d-name').textContent : null,
        glossary: document.getElementById('glossary').open,
        primer: document.getElementById('primer').open,
    })""")


GPT_OPEN = {"hash": "#gpt", "panel": "GPT-style LLM", "glossary": False, "primer": False}


def settle(pg, ms=400):
    pg.wait_for_timeout(ms)


def back(pg):
    pg.go_back()
    settle(pg)


def forward(pg):
    pg.go_forward()
    settle(pg)


# --------------------------------------------------------------------------- the page itself


def test_page_loads_every_panel(page):
    go(page)
    ids = page.eval_on_selector_all(".card", "cs => cs.map(c => c.dataset.id)")
    assert len(ids) >= 40
    for pid in ids:  # each panel opens, and its step-through starts and stops cleanly
        go(page, "#" + pid)
        assert view(page)["panel"], pid
        if page.locator("#d-flowctl").is_visible():
            page.locator("#d-flowctl .d-play").click()
            settle(page, 150)
            page.locator("#d-flowctl .d-stop").click()


def test_every_glossary_figure_steps_through(page):
    go(page, "#glossary")
    figs = page.locator("#glossary figure[data-flow]")
    assert figs.count() >= 30
    for i in range(figs.count()):
        fig = figs.nth(i)
        say = fig.locator(".p-say")
        idle = say.text_content()
        fig.scroll_into_view_if_needed()
        fig.locator(".d-play").click()
        settle(page, 250)
        assert say.text_content() != idle, f"figure {i} has no caption for its first step"
        fig.locator(".d-stop").click()
        assert say.text_content() == idle, f"figure {i} keeps its caption after Stop"


def test_glossary_links_lead_somewhere(page):
    go(page)
    bad = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('[data-g]').forEach(b => {
            if (!document.getElementById('glossary-' + b.dataset.g))
                out.push('term ' + b.dataset.g);
        });
        document.querySelectorAll('#glossary [data-open]').forEach(b => {
            if (!document.querySelector('.card[data-id="' + b.dataset.open + '"]'))
                out.push('panel ' + b.dataset.open);
        });
        return out;
    }""")
    assert not bad, bad


def test_glossary_entries_never_link_to_themselves(page):
    go(page)
    selfs = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('#glossary h4[id^="glossary-"]').forEach(h => {
            const slug = h.id.slice(9);
            for (let e = h.nextElementSibling; e && !/^H[34]$/.test(e.tagName); e = e.nextElementSibling)
                if (e.querySelector('[data-g="' + slug + '"]')) out.push(slug);
        });
        return out;
    }""")
    assert not selfs, selfs


def test_search_suggests_glossary_entries(page):
    go(page)
    page.fill("#f-search", "attention")
    chips = page.locator("#f-gloss .g-chip")
    assert chips.first.text_content() == "Attention"  # its own name first
    page.fill("#f-search", "zzz")
    assert page.locator("#f-gloss").is_hidden()
    page.fill("#f-search", "kv")
    page.locator("#f-gloss .g-chip").first.click()  # opens the entry; Close returns to the search
    settle(page)
    assert view(page)["hash"] == "#glossary-kv-cache"
    page.locator("#g-close").click()
    settle(page)
    assert not view(page)["glossary"] and page.input_value("#f-search") == "kv"


def test_glossary_reads_well_with_a_screen_reader(page):
    go(page, "#glossary")
    found = page.evaluate("""() => {
        const g = document.getElementById('glossary'), out = [];
        let last = 1;  // headings step down one level at a time: the title, groups, entries
        g.querySelectorAll('h2, h3, h4').forEach(h => {
            const n = +h.tagName[1];
            if (n > last + 1) out.push('skips to ' + h.tagName + ' at ' + h.textContent);
            last = n;
        });
        if (!g.querySelector('nav[aria-label] ul a')) out.push('no index landmark');
        g.querySelectorAll('figure svg[data-steps]').forEach(s => {
            if (!s.getAttribute('aria-label')) out.push('unnamed figure ' + s.closest('figure').id);
        });
        g.querySelectorAll('.d-play').forEach(b => {
            const d = document.getElementById(b.getAttribute('aria-describedby') || '');
            if (!d) out.push('a Step through says not which entry it steps');
        });
        g.querySelectorAll('.p-say:not([aria-live])').forEach(() => out.push('silent caption'));
        g.querySelectorAll('a[target=_blank]').forEach(a => {
            if (!a.querySelector('.sr-only')) out.push('new tab unannounced: ' + a.href);
        });
        return out;
    }""")
    assert not found, found
    # the keyboard: an index link takes focus to its entry, the next Tab to what follows it
    page.locator('#glossary .g-index a[href="#glossary-kv-cache"]').focus()
    page.keyboard.press("Enter")
    settle(page)
    assert page.evaluate("document.activeElement.id") == "glossary-kv-cache"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.getAttribute('href')") == "#glossary-kv-cache"


def test_page_reads_in_order_with_keyboard_and_screen_reader(page):
    go(page)
    # the first Tab offers a way past the header and its legend, to the first diagram
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "skip"
    page.keyboard.press("Enter")
    card = page.locator(".card").first
    assert page.evaluate("document.activeElement.dataset.id") == card.get_attribute("data-id")
    # a card is named as a sentence: its name, year, caption, and objective
    said = re.sub(r"\s+([,.])", r"\1", card.aria_snapshot())  # the gaps between its parts aside
    assert (
        'button "Perceptron, landmark paper 1958. Weighted sum, then a threshold. Objective: supervised"'
        in said
    )
    # a panel opens on its name, reads in the order a phone shows it, and closes back to its card
    page.keyboard.press("Enter")
    settle(page, 600)
    assert page.evaluate("document.activeElement.id") == "d-name"
    order = page.evaluate(
        """() => ['d-name', 'd-fig', 'd-read', 'd-key', 'd-summary', 'd-papers']
        .map(id => document.getElementById(id))
        .every((e, i, all) => !i || all[i - 1].compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING)"""
    )
    assert order
    fig = page.locator("#d-fig svg")
    assert (
        fig.get_attribute("role") == "img"
        and fig.get_attribute("aria-label") == "Diagram: Perceptron"
    )
    assert fig.get_attribute("aria-describedby") == "d-read"
    page.keyboard.press("Escape")
    settle(page, 600)
    assert page.evaluate("document.activeElement.dataset.id") == card.get_attribute("data-id")
    found = page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('a[target=_blank]').forEach(a => {
            const said = (a.getAttribute('aria-label') || '') + a.textContent;
            if (!/opens in a new tab/.test(said)) out.push('new tab unannounced: ' + a.href);
        });
        document.querySelectorAll('.legend ul:not([aria-labelledby])').forEach(() => out.push('unnamed legend list'));
        document.querySelectorAll('#primer figure svg[role=img]:not([aria-label])').forEach(() => out.push('unnamed primer figure'));
        document.querySelectorAll('#primer input[type=range]').forEach(i => {
            if (!/^Input \\d$/.test(i.getAttribute('aria-label') || '')) out.push('slider named by its value');
        });
        return out;
    }""")
    assert not found, found


def test_panel_captions_show_while_stepping(page):
    go(page, "#gpt")
    say = page.locator("#d-say")
    reading = page.locator("#d-read-wrap")
    assert not say.is_visible()
    assert "Step through shows" in page.locator("#d-read").text_content()  # what it will show
    rest = reading.bounding_box()["y"]
    page.locator("#d-play").click()
    settle(page, 300)
    assert say.is_visible() and say.text_content()
    moved = reading.bounding_box()["y"]  # down once, for the caption...
    page.wait_for_timeout(3600)
    assert reading.bounding_box()["y"] == moved  # ...and not again at the next step
    page.locator("#d-stop").click()
    settle(page, 200)
    assert not say.is_visible() and reading.bounding_box()["y"] == rest
    # every panel narrates its step-through, and its reading ends by saying what it shows
    missing = page.evaluate(
        """() => Array.from(document.querySelectorAll('.card')).map(c => c.dataset.id)"""
    )
    for pid in missing:
        go(page, "#" + pid)
        if page.locator("#d-flowctl").is_visible():
            assert page.evaluate(
                "JSON.parse(document.getElementById('d-fig').dataset.say || '[]').length"
            ), pid
            assert "Step through" in page.locator("#d-read").text_content(), pid


def test_glossary_orders_a_to_z_and_back(page):
    go(page, "#glossary")
    page.select_option("#g-order", "az")
    settle(page, 200)
    found = page.evaluate("""() => {
        const g = document.getElementById('glossary');
        const terms = [...g.querySelectorAll('h4')].map(h => h.textContent.trim());
        const sorted = [...terms].sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' }));
        const index = [...g.querySelectorAll('.g-nav:not([hidden]) a')].map(a => a.textContent);
        return { n: terms.length, sorted: terms.join() === sorted.join(), index: index.join() === sorted.join(),
                 letters: [...g.querySelectorAll('h3')].every(h => h.textContent.length === 1) };
    }""")
    assert found["n"] > 100 and found["sorted"] and found["index"] and found["letters"], found
    page.locator('#glossary .g-nav:not([hidden]) a[href="#glossary-kv-cache"]').click()
    settle(page)
    assert view(page)["hash"] == "#glossary-kv-cache"
    page.reload()  # the order is remembered
    settle(page)
    assert page.locator("#g-order").input_value() == "az"
    page.select_option("#g-order", "topic")
    settle(page, 200)
    assert page.locator("#glossary h3").first.text_content() == "Basics"


def test_glossary_bars_keep_their_heights(page):
    # closing a sheet stops its figures; one that never ran keeps its resting drawing
    go(page, "#glossary-sampling")
    bars = "() => [...document.querySelectorAll('#glossary rect.cv-bar[data-move]')].map(r => r.style.transform)"
    rest = page.evaluate(bars)
    page.locator("#g-close").click()
    settle(page)
    page.locator("#gloss-btn").click()
    settle(page)
    assert page.evaluate(bars) == rest and all(rest)


def test_phone_titlebar_and_panel_bar(browser, page_url):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    pg.goto(page_url + "#gpt")
    settle(pg)
    # the buttons sit above the title, not beside it
    title = pg.locator(".titlebar h1").bounding_box()
    theme = pg.locator("#theme").bounding_box()
    assert theme["y"] + theme["height"] <= title["y"]
    # the panel's "More below" keeps only its arrow, so its bar stays on one line
    assert not pg.locator("#detail .d-nav .nav-more span").is_visible()
    ctx.close()


# --------------------------------------------------------------------------- Back and Forward


def test_panel_back_forward_and_close(page):
    go(page)
    page.locator('.card[data-id="gpt"]').click()
    settle(page)
    page.locator("#d-next").click()
    page.locator("#d-next").click()  # Previous and Next replace the step: one Back leaves
    settle(page)
    moved = view(page)["panel"]
    back(page)
    assert view(page) == {"hash": "", "panel": None, "glossary": False, "primer": False}
    forward(page)
    assert view(page)["panel"] == moved
    page.locator("#d-close").click()
    settle(page)
    assert view(page)["panel"] is None and view(page)["hash"] == ""


def test_glossary_used_in_and_back(page):
    go(page)
    page.locator("#gloss-btn").click()
    page.locator('#glossary .g-index a[href="#glossary-kv-cache"]').click()
    settle(page)
    page.locator("#glossary-kv-cache ~ .g-used button", has_text="GPT-style LLM").first.click()
    settle(page, 700)
    v = view(page)
    assert v["panel"] == "GPT-style LLM" and not v["glossary"]
    assert page.locator("#d-close").text_content() == "Back to KV cache"
    back(page)
    v = view(page)
    assert v["hash"] == "#glossary-kv-cache" and v["glossary"] and v["panel"] is None
    forward(page)
    page.locator("#d-close").click()  # its Close takes the same step back
    settle(page, 700)
    assert view(page)["hash"] == "#glossary-kv-cache" and view(page)["panel"] is None
    page.locator("#glossary-kv-cache ~ .g-used button", has_text="GPT-style LLM").first.click()
    settle(page, 700)
    page.keyboard.press("Escape")  # and so does Esc
    settle(page, 700)
    assert view(page)["hash"] == "#glossary-kv-cache" and view(page)["glossary"]
    forward(page)
    page.locator("#d-next").click()  # moving on: Close is Close again, back to the page
    settle(page)
    assert page.locator("#d-close").text_content() == "Close"
    page.locator("#d-close").click()
    settle(page, 700)
    assert view(page) == {"hash": "", "panel": None, "glossary": False, "primer": False}


def test_term_over_panel_closes_back_to_it(page):
    go(page)
    page.locator('.card[data-id="gpt"]').click()
    settle(page)
    page.locator('#d-summary .g-link[data-g="token"]').first.click()
    settle(page)
    page.locator('#glossary-token ~ p .g-link[data-g="vector"]').first.click()  # a cross-reference
    settle(page)
    assert view(page)["hash"] == "#glossary-vector" and view(page)["panel"] == "GPT-style LLM"
    back(page)
    assert view(page)["hash"] == "#glossary-token"
    forward(page)
    page.locator('#glossary-vector ~ p .g-link[data-g="embedding"]').first.click()
    settle(page)
    page.locator("#g-close").click()  # past every entry visited (three), to the panel beneath
    settle(page, 700)
    assert view(page) == GPT_OPEN
    page.keyboard.press("Escape")
    settle(page, 700)
    assert view(page)["hash"] == "" and view(page)["panel"] is None


def test_glossary_over_primer_keeps_the_readers_place(page):
    go(page)
    page.locator("#help").click()
    settle(page)
    links = page.locator("#primer .g-link")
    term = links.nth(links.count() * 2 // 3)  # a term well down the primer
    term.scroll_into_view_if_needed()
    place = page.evaluate("() => document.getElementById('primer').scrollTop")
    assert place > 0
    term.click()
    settle(page)
    assert view(page)["glossary"] and view(page)["primer"]
    assert page.locator("#g-close").text_content() == "Back to the primer"
    page.locator("#g-close").click()  # Close returns to the primer, where the reader was
    settle(page, 700)
    assert view(page)["primer"] and not view(page)["glossary"]
    assert page.evaluate("() => document.getElementById('primer').scrollTop") == place
    term.click()
    settle(page)
    back(page)  # and so does Back
    assert view(page)["primer"] and not view(page)["glossary"]
    assert page.evaluate("() => document.getElementById('primer').scrollTop") == place


def test_deep_links(page):
    go(page, "#glossary-kv-cache")
    assert view(page)["glossary"]
    page.locator("#g-close").click()
    settle(page)
    assert view(page)["hash"] == "" and not view(page)["glossary"]
    go(page, "#gpt")
    page.locator("#d-summary .g-link").first.click()
    settle(page)
    back(page)
    assert view(page) == GPT_OPEN
