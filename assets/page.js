(() => {
  const DATA = JSON.parse(document.getElementById('guide-data').textContent);
  // Download a diagram: the drawing in its card, as a standalone SVG like dist/panels/<id>.svg,
  // in the poster's ink and with the guide's credit. A plain page saves through a link; on
  // claude.ai the poster's downloads capability sets saveFile once it is granted.
  const dlBtn = document.getElementById('d-more');  // carries the credit and ink for the file
  let saveFile = null;
  if ((document.getElementById('save') || {}).dataset?.mode === 'standalone') {
    saveFile = async (name, blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
  }
  function panelFile(id) {
    const svg = document.querySelector('.card[data-id="' + id + '"] svg').outerHTML;
    const esc = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const head = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="$1" width="$2" height="$3" role="img">' +
      '<title>' + esc(DATA[id].name) + '</title><desc>' + esc(dlBtn.dataset.credit) + '</desc>';
    const body = svg
      .replace(/^<svg viewBox="(0 0 ([\d.]+) ([\d.]+))"[^>]*>/, (m, vb, w, h) =>
        head.replace('$1', vb).replace('$2', 2 * w).replace('$3', 2 * h))
      .split('currentColor').join(dlBtn.dataset.ink);
    return new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n' + body], { type: 'image/svg+xml' });
  }
  // The drawing as a PNG: the SVG drawn on a white canvas at four times the panel's size
  function panelPng(id) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(panelFile(id));
      const img = new Image();
      img.onload = () => {
        const c = document.createElement('canvas');
        c.width = img.naturalWidth * 2;  // the file is drawn at 2x, so 4x of the panel
        c.height = img.naturalHeight * 2;
        const g = c.getContext('2d');
        g.fillStyle = '#FFFFFF';
        g.fillRect(0, 0, c.width, c.height);
        g.drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(b => (b ? resolve(b) : reject(new Error('png'))), 'image/png');
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('svg')); };
      img.src = url;
    });
  }
  const menu = document.getElementById('d-menu');
  // The entry's link, in the popup's top corner: it names the whole entry, not the drawing, so
  // it sits by the title rather than in the drawing's menu. It says so briefly when copied.
  const linkBtn = document.getElementById('d-link');
  const linkDone = linkBtn.querySelector('.d-link-done');
  let linkTimer = null;
  linkBtn.addEventListener('click', async () => {
    if (!current) return;
    try {
      await navigator.clipboard.writeText(location.origin + location.pathname + '#' + current);
      linkDone.textContent = 'Link copied';
    } catch (err) {
      linkDone.textContent = 'Couldn\u2019t copy';
    }
    clearTimeout(linkTimer);
    linkTimer = setTimeout(() => { linkDone.textContent = ''; }, 1500);
  });
  const standalone = (document.getElementById('save') || {}).dataset?.mode === 'standalone';
  function setMenu(open) {
    menu.hidden = !open;
    dlBtn.setAttribute('aria-expanded', String(open));
    if (open) (menuItems()[0] || dlBtn).focus();
  }
  const menuItems = () => Array.from(menu.querySelectorAll('button')).filter(b => b.offsetParent !== null);
  // which actions this page can offer: saving needs saveFile, a link needs a real address
  function refreshMenu() {
    menu.querySelectorAll('[data-act="svg"], [data-act="png"]').forEach(b => { b.hidden = !saveFile; });
    linkBtn.hidden = !standalone;
    dlBtn.hidden = !menu.querySelector('button:not([hidden])');
  }
  dlBtn.addEventListener('click', () => setMenu(menu.hidden));
  menu.addEventListener('click', async e => {
    const b = e.target.closest('button[data-act]');
    if (!b || !current) return;
    const id = current, base = 'neural-network-field-guide-' + id;
    try {
      if (b.dataset.act === 'svg') await saveFile(base + '.svg', panelFile(id));
      if (b.dataset.act === 'png') await saveFile(base + '.png', await panelPng(id));
    } catch (err) {}
    setMenu(false);
  });
  menu.addEventListener('keydown', e => {
    const items = menuItems();
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      e.stopPropagation();
      items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') e.stopPropagation();  // not Previous/Next
    if (e.key === 'Tab') setMenu(false);
  });
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
  // A primer figure with tips: pointing at a part (or tapping it) keeps it, and for a cell the
  // lines into it, and fades the rest, and says what it does below. A part's text is found by
  // its key ("x.2", "x.2 n1.0") or, failing that, its row's ("x", "x n1").
  document.querySelectorAll('.p-tips').forEach(fig => {
    const tips = JSON.parse(fig.dataset.tips);
    const readEl = fig.querySelector('.p-read');
    const HINT = readEl.textContent;
    const row = k => k.split('.')[0];
    const cells = {};
    fig.querySelectorAll('[data-c]').forEach(e => { cells[e.dataset.c] = e; });
    const lines = Array.from(fig.querySelectorAll('[data-e]'));
    const tipFor = el => {
      if (el.dataset.e) {
        const [a, b] = el.dataset.e.split(' ');
        return tips[a + ' ' + b] || tips[row(a) + ' ' + row(b)] || '';
      }
      return tips[el.dataset.c] || tips[row(el.dataset.c)] || '';
    };
    function point(el) {
      fig.classList.toggle('pointing', !!el);
      lines.forEach(l => l.classList.remove('on'));
      Object.values(cells).forEach(c => c.classList.remove('on'));
      if (!el) { readEl.textContent = HINT; return; }
      el.classList.add('on');
      if (el.dataset.e) el.dataset.e.split(' ').forEach(k => cells[k] && cells[k].classList.add('on'));
      else lines.filter(l => l.dataset.e.split(' ')[1] === el.dataset.c).forEach(l => {
        l.classList.add('on');
        const a = cells[l.dataset.e.split(' ')[0]];
        if (a) a.classList.add('on');
      });
      readEl.textContent = tipFor(el) || HINT;
    }
    const hits = document.createElementNS('http://www.w3.org/2000/svg', 'g');  // wide copies to point at
    hits.setAttribute('class', 'p-hits');
    lines.forEach(l => {
      const h = l.cloneNode(false);
      ['data-e', 'data-s', 'data-on', 'data-k', 'style', 'marker-end', 'opacity', 'class'].forEach(a => h.removeAttribute(a));
      h.addEventListener('pointerenter', () => point(l));
      h.addEventListener('pointerleave', () => point(null));
      hits.append(h);
    });
    const first = Object.values(cells)[0];  // beneath the cells, so they can still be pointed at
    first.closest('svg > *').before(hits);
    Object.values(cells).forEach(c => {
      c.addEventListener('pointerenter', () => point(c));
      c.addEventListener('pointerleave', () => point(null));
    });
  });
  // The primer's live network: the inputs come from the sliders, and each other unit takes
  // the weighted sum of the units below it, squashed by a sigmoid. A unit's brightness shows
  // its activity; a line's width shows its weight's size and its shade the sign (gray
  // subtracts). Randomize weights draws a fresh set, as untrained. Pointing at a unit lights the
  // lines into it and spells out its sum below; pointing at a line gives its weight and what it
  // carries. (Each line gets a wide invisible copy, so a thin one is easy to point at.)
  document.querySelectorAll('.p-live').forEach(fig => {
    const cells = {};
    fig.querySelectorAll('[data-c]').forEach(e => { if (e.tagName !== 'INPUT') cells[e.dataset.c] = e; });
    const lines = Array.from(fig.querySelectorAll('[data-e]'));
    const sliders = Array.from(fig.querySelectorAll('input[type="range"]'));
    const rowOf = k => +k.split('.')[0].replace(/\D/g, '');
    const order = Object.keys(cells).sort((a, b) => rowOf(a) - rowOf(b));
    const last = Math.max(...order.map(rowOf));
    const outEl = fig.querySelector('.p-out');
    const sig = z => 1 / (1 + Math.exp(-z));
    const readEl = fig.querySelector('.p-read');
    const HINT = readEl.textContent;
    const nameOf = (k, cap) => {  // "input 2", "hidden unit 3 (layer 1)", "output 1"
      const r = rowOf(k), i = +k.split('.')[1] + 1;
      const n = r === 0 ? 'input ' + i : r === last ? 'output ' + i : `hidden unit ${i} (layer ${r})`;
      return cap ? n[0].toUpperCase() + n.slice(1) : n;
    };
    const num = (x, d = 2) => (x < 0 ? '−' : '') + Math.abs(x).toFixed(d);
    let act = {};
    let pointed = null;  // the unit or line being pointed at
    function explain() {
      fig.classList.toggle('pointing', !!pointed);
      lines.forEach(l => l.classList.remove('on'));
      Object.values(cells).forEach(c => c.classList.remove('on'));
      if (!pointed) { readEl.textContent = HINT; return; }
      if (pointed.dataset.e) {  // a line
        const [a, b] = pointed.dataset.e.split(' '), w = +pointed.dataset.w;
        pointed.classList.add('on'); cells[a].classList.add('on'); cells[b].classList.add('on');
        readEl.textContent = `Weight ${num(w, 1)}, from ${nameOf(a)} to ${nameOf(b)}: ` +
          `it carries ${num(w, 1)} × ${num(act[a])} = ${num(w * act[a])}${w < 0 ? ', which subtracts' : ''}.`;
      } else {  // a unit
        const k = pointed.dataset.c;
        cells[k].classList.add('on');
        const into = lines.filter(l => l.dataset.e.split(' ')[1] === k);
        if (!into.length) { readEl.textContent = `${nameOf(k, true)}: ${num(act[k])}, set by its slider.`; return; }
        let z = 0;
        const terms = into.map(l => {
          const a = l.dataset.e.split(' ')[0], w = +l.dataset.w;
          l.classList.add('on'); cells[a].classList.add('on');
          z += w * act[a];
          return `${num(w, 1)} × ${num(act[a])}`;
        });
        readEl.textContent = `${nameOf(k, true)}: ${terms.join(' + ').replace(/\+ −/g, '− ')} = ${num(z)}, squashed to ${num(act[k])}.`;
      }
    }
    // a wide, invisible copy of each line to point at
    const hits = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    hits.setAttribute('class', 'p-hits');
    lines.forEach(l => {
      const h = l.cloneNode(false);
      ['data-e', 'data-w', 'style', 'marker-end', 'opacity'].forEach(a => h.removeAttribute(a));
      h.addEventListener('pointerenter', () => { pointed = l; explain(); });
      h.addEventListener('pointerleave', () => { pointed = null; explain(); });
      hits.append(h);
    });
    const firstCell = Object.values(cells)[0];
    firstCell.parentNode.insertBefore(hits, firstCell.closest('svg > *') || firstCell);
    Object.values(cells).forEach(c => {
      c.addEventListener('pointerenter', () => { pointed = c; explain(); });
      c.addEventListener('pointerleave', () => { pointed = null; explain(); });
    });
    function run() {
      act = {};
      sliders.forEach(s => { act[s.dataset.c] = +s.value; s.nextElementSibling.value = (+s.value).toFixed(2); });
      for (const k of order) {
        if (k in act) continue;
        let z = 0;
        lines.forEach(l => { const [a, b] = l.dataset.e.split(' '); if (b === k) z += +l.dataset.w * act[a]; });
        act[k] = sig(z);
      }
      const big = Math.max(...lines.map(l => Math.abs(+l.dataset.w)));
      lines.forEach(l => {
        const w = +l.dataset.w;
        l.style.strokeWidth = (0.4 + 2.2 * Math.abs(w) / big).toFixed(2);
        l.style.stroke = w < 0 ? 'var(--rule-strong, #B9B7B0)' : '';
      });
      order.forEach(k => { cells[k].style.opacity = (0.2 + 0.8 * act[k]).toFixed(2); });
      outEl.value = order.filter(k => rowOf(k) === last).map(k => act[k].toFixed(2)).join(' · ');
      explain();
    }
    sliders.forEach(s => s.addEventListener('input', run));
    fig.querySelector('.p-new').addEventListener('click', () => {
      const gauss = () => Math.sqrt(-2 * Math.log(1 - Math.random())) * Math.cos(2 * Math.PI * Math.random());
      lines.forEach(l => { l.dataset.w = (Math.round(gauss() * 22) / 10).toFixed(1); });
      run();
    });
    run();
  });

  // The primer, opened by the "?" button, and the glossary, by its book button: each a sheet
  // also opened by a link to #<name> or #<name>-<anchor>, which opens it at that section. While
  // open, the address shows it, so it can be shared; closing puts back the address it replaced.
  // The browser's Back and Forward. Opening a dialog from the page, or going from one to
  // another, adds a step: its state counts the steps since the page (depth) and names the view
  // left beneath it (under), as a panel under the glossary entry one of its terms opened.
  // Moving within one view (Previous, Next, the glossary's index) replaces the step instead.
  // A dialog closed by the reader goes back over the steps that opened it; one closed to
  // match the address (route, below) closes quietly.
  let routing = false;
  function anyOpen() { return [dlg, primer, glossary].some(d => d && d.open); }
  function push(hash, state) {
    const depth = ((history.state && history.state.depth) || 0) + 1;
    try { history.pushState(Object.assign({ depth }, state), '', hash); } catch (e) {}
  }
  function setHash(hash) {
    if (!routing) try { history.replaceState(history.state, '', hash); } catch (e) {}
  }
  function quietClose(d) { d._quiet = true; d.close(); }
  function leave() {  // the reader closed a dialog: back to what was beneath it, or the page
    const st = history.state;
    if (!st || !st.depth) return false;
    if (st.under) history.go(-(st.depth - (st.base || 0))); else history.go(-st.depth);
    return true;
  }
  function makeSheet(sheet, name, titleId, closeId, btnId) {
    let from = null, prev = null;
    sheet._from = () => from;
    const top = sheet.querySelector('.nav-top');
    function more() {  // the fade above Close shows while more lies below
      sheet.classList.toggle('more', sheet.scrollTop + sheet.clientHeight < sheet.scrollHeight - 2);
      // and "Index" once the index showing (by topic, or A to Z) has scrolled out of view
      const index = sheet.querySelectorAll('.g-nav:not([hidden]) .g-group');
      if (top && index.length) {
        sheet.classList.toggle('past', index[index.length - 1].getBoundingClientRect().bottom < sheet.getBoundingClientRect().top);
      }
    }
    if (top) top.addEventListener('click', () => {  // back to the index, the address back to the sheet's own
      setHash('#' + name);
      sheet.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      document.getElementById(titleId).focus({ preventScroll: true });
    });
    function openAt(anchor, opener, step) {  // `step`: a cross-reference, a step of its own
      from = opener || null;
      const hash = '#' + name + (anchor ? '-' + anchor : '');
      sheet._hash = hash;  // where it is, so a return to it (Back, Close above it) keeps its place
      if (!sheet.open) {
        // its Close says where it leads, when it opened over something: back to that
        const closeBtn = document.getElementById(closeId);
        const beneath = sheet !== primer && primer.open ? 'the primer'
          : dlg.open ? document.getElementById('d-name').textContent : '';
        closeBtn.textContent = beneath ? 'Back to ' + beneath : 'Close';
        prev = location.hash.startsWith('#' + name) ? '' : location.hash;  // opened from its own link
        // a step of its own, over whatever is open beneath it
        // and the step it opened over, so closing it goes back past every step taken within it
        const st = history.state || {};
        if (!routing) push(hash, anyOpen() ? { under: location.hash, base: st.depth || 0 } : { under: null });
        if (typeof sheet.showModal === 'function') sheet.showModal(); else sheet.setAttribute('open', '');
      } else if (step && !routing) {
        const st = history.state || {};
        push(hash, { under: st.under || null, base: st.base || 0 });
      } else {
        setHash(hash);
      }
      const target = anchor && document.getElementById(name + '-' + anchor);
      sheet.scrollTop = 0;
      // a section near the end can't scroll to the top on its own: an empty block after the
      // text gives it just enough room, so its heading lands where every other one does
      let room = sheet.querySelector(':scope > .sheet-room');
      if (!room) {
        room = document.createElement('div');
        room.className = 'sheet-room';
        room.setAttribute('aria-hidden', 'true');
        sheet.querySelector(':scope > .p-wrap').after(room);
      }
      room.style.height = '0px';
      if (target) {
        const want = target.getBoundingClientRect().top - sheet.getBoundingClientRect().top - 28;
        const short = want - (sheet.scrollHeight - sheet.clientHeight);
        if (short > 0) room.style.height = Math.ceil(short) + 'px';
        sheet.scrollTop = want;
        target.focus({ preventScroll: true });
      } else {
        document.getElementById(titleId).focus({ preventScroll: true });
      }
      sheet.classList.add('instant');  // the fade above Close, as on a panel: set at once on opening
      more();
      requestAnimationFrame(() => requestAnimationFrame(() => sheet.classList.remove('instant')));
    }
    sheet.addEventListener('scroll', more, { passive: true });
    window.addEventListener('resize', () => { if (sheet.open) more(); });
    const btn = document.getElementById(btnId);
    if (btn) btn.addEventListener('click', e => openAt(null, e.currentTarget));
    document.getElementById(closeId).addEventListener('click', () => sheet.close());
    sheet.addEventListener('click', e => { if (e.target === sheet) sheet.close(); });
    sheet.addEventListener('close', () => {
      if (sheet._quiet) sheet._quiet = false;
      else if (!leave()) {  // opened from a link: no step to go back over
        try { history.replaceState(null, '', location.pathname + location.search + (prev || '')); } catch (e) {}
      }
      const back = from && document.contains(from) ? from : btn;
      if (back) back.focus();
    });
    // within a link inside the sheet, move to the section it names
    sheet.addEventListener('click', e => {
      const a = e.target.closest('a[href^="#' + name + '-"]');
      if (!a) return;
      e.preventDefault();
      openAt(a.getAttribute('href').slice(name.length + 2), from);
    });
    return openAt;
  }
  const primer = document.getElementById('primer');
  const openPrimer = makeSheet(primer, 'primer', 'p-title', 'p-close', 'help');
  // a panel's link into the primer opens it over the panel, as a step its Back returns from
  const scope = document.getElementById('d-scope');
  if (scope) scope.addEventListener('click', e => {
    const a = e.target.closest('a[href^="#primer-"]');
    if (!a) return;
    e.preventDefault();
    openPrimer(a.getAttribute('href').slice(8), a);
  });
  const glossary = document.getElementById('glossary');
  const openGlossary = glossary ? makeSheet(glossary, 'glossary', 'g-title', 'g-close', 'gloss-btn') : null;
  const from0 = b => (glossary._from && glossary._from()) || b;  // what the glossary was opened from
  // The glossary's order: by topic, as written, or A to Z, under a heading per letter (so the
  // headings still step down one level at a time), with an index of its own. Remembered.
  const gOrder = document.getElementById('g-order');
  if (glossary && gOrder) {
    const wrap = glossary.querySelector('.p-wrap');
    const byTopic = Array.from(wrap.children);
    const entries = [];
    byTopic.forEach(e => {
      if (e.tagName === 'H4') entries.push([e]);
      else if (e.tagName === 'H3') entries.push(null);
      else if (entries.length && entries[entries.length - 1] && !e.matches('.g-head, .g-nav')) entries[entries.length - 1].push(e);
    });
    const term = e => e[0].textContent.trim();
    const sorted = entries.filter(Boolean).sort((a, b) => term(a).localeCompare(term(b), 'en', { sensitivity: 'base' }));
    const azNav = document.createElement('nav');
    azNav.className = 'g-nav';
    azNav.setAttribute('aria-label', 'Index');
    azNav.hidden = true;
    azNav.innerHTML = '<div class="g-group"><p class="g-gname" id="gi-az">A to Z</p><ul class="g-index" aria-labelledby="gi-az"></ul></div>';
    const list = azNav.querySelector('ul');
    const byLetter = [];
    sorted.forEach(e => {
      const li = document.createElement('li'), a = document.createElement('a');
      a.href = '#' + e[0].id;
      a.textContent = term(e);
      li.append(a);
      list.append(li);
      const letter = term(e)[0].toUpperCase();
      if (!byLetter.length || byLetter[byLetter.length - 1].letter !== letter) {
        const h = document.createElement('h3');
        h.className = 'g-sec';
        h.textContent = letter;
        byLetter.push({ letter, h, items: [] });
      }
      byLetter[byLetter.length - 1].items.push(...e);
    });
    const topicNav = wrap.querySelector('.g-nav');
    function order(az) {
      if (az) {
        topicNav.hidden = true;
        azNav.hidden = false;
        wrap.replaceChildren(wrap.querySelector('.g-head'), topicNav, azNav,
          ...byLetter.flatMap(g => [g.h, ...g.items]));
      } else {
        topicNav.hidden = false;
        azNav.hidden = true;
        wrap.replaceChildren(...byTopic, azNav);
      }
    }
    let saved = '';
    try { saved = localStorage.getItem('field-guide-gloss-order') || ''; } catch (e) {}
    if (saved === 'az') { gOrder.value = 'az'; order(true); }
    gOrder.addEventListener('change', () => {
      order(gOrder.value === 'az');
      try { localStorage.setItem('field-guide-gloss-order', gOrder.value); } catch (e) {}
    });
  }
  if (glossary) {
    // Used in: closes the glossary and opens that panel
    glossary.addEventListener('click', e => {
      const b = e.target.closest('[data-open]');
      if (!b) return;
      // the entry it was listed under, so the panel can offer a way back to it
      let entry = b.closest('p');
      while (entry && entry.tagName !== 'H4') entry = entry.previousElementSibling;
      const slug = entry ? entry.id.slice('glossary-'.length) : null;
      // the panel a step of its own, after the glossary's, so Back returns to the entry
      glossary.addEventListener('close', () => {
        open(b.dataset.open, { fromGloss: slug, fromTerm: entry ? entry.textContent : '' });
        if (entry) showBack(slug, entry.textContent);
      }, { once: true });
      quietClose(glossary);
    });
    // a term linked from a panel's text opens its entry, over the panel
    document.addEventListener('click', e => {
      const b = e.target.closest('.g-link');
      // within the glossary, a term in an entry's text is a step of its own: Back returns to the entry
      if (b) openGlossary(b.dataset.g, glossary.contains(b) ? from0(b) : b, glossary.contains(b));
    });
  }
  const dlg = document.getElementById('detail');
  let family = 'all';
  let current = null;

  // in the order the page shows them now, so Previous and Next follow the chosen order
  function visibleIds() {
    return Array.from(groupsEl.querySelectorAll('.card')).filter(c => !c.hidden).map(c => c.dataset.id);
  }

  // A section with fewer cards than a row has columns spans only those, so the next can join it.
  // Columns that fit, as the grid's repeat(auto-fill, minmax(176px, 1fr)) with a 14px gap lays them
  // out; read from the width, since a span left from a wider layout adds implicit columns.
  const colsNow = () => Math.max(1, Math.floor((groupsEl.clientWidth + 14) / (176 + 14)));
  // The toggle turns packing off, one section per row; it is hidden where nothing could pair.
  const packBtn = document.getElementById('f-pack');
  let packOn = true;
  try { packOn = localStorage.getItem('field-guide-pack') !== 'off'; } catch (e) {}
  packBtn.setAttribute('aria-pressed', String(!packOn));
  packBtn.addEventListener('click', () => {
    packOn = !packOn;
    packBtn.setAttribute('aria-pressed', String(!packOn));
    try { localStorage.setItem('field-guide-pack', packOn ? 'on' : 'off'); } catch (e) {}
    pack();
  });
  function pack() {
    const cols = colsNow();
    packBtn.hidden = cols < 3;
    const colW = (groupsEl.clientWidth - 14 * (cols - 1)) / cols;
    for (const s of sections) {
      const n = s.querySelectorAll('.card:not([hidden])').length;
      // wide enough for its title on one line, even when filtered down to one card
      const h = s.querySelector('h2');
      let need = 1;
      if (h) {
        h.style.whiteSpace = 'nowrap';
        const r = document.createRange();
        r.selectNodeContents(h);
        need = Math.ceil((r.getBoundingClientRect().width + 14) / (colW + 14));
        h.style.whiteSpace = '';
      }
      const span = Math.max(n, need);
      s.style.gridColumn = packOn && n && span < cols ? 'span ' + span : '';
    }
  }
  let packCols = 0;
  window.addEventListener('resize', () => {
    const cols = colsNow();
    if (cols !== packCols) { packCols = cols; pack(); }
  });

  function apply() {
    const term = search.value.trim().toLowerCase();
    const obj = objSel.value;
    let shown = 0;
    for (const c of cards) {
      const d = DATA[c.dataset.id];
      const ok = (family === 'all' || d.family === family || d.also.includes(family)) &&
        (obj === 'all' || d.objective === obj || d.alsoObjectives.includes(obj)) &&
        (!term || d.search.includes(term));
      c.hidden = !ok;
      if (ok) shown++;
    }
    for (const s of sections) s.hidden = !s.querySelector('.card:not([hidden])');
    pack();
    count.textContent = shown + (shown === 1 ? ' diagram' : ' diagrams');
    empty.hidden = shown > 0;
    suggestGloss(term);
  }

  // The search also suggests glossary entries: by name, then by the forms that link to them,
  // then by their definitions; each opens the glossary at its entry
  const GLOSS = (() => {
    try { return JSON.parse(document.getElementById('gloss-data').textContent); } catch (e) { return []; }
  })();
  const suggest = document.getElementById('f-gloss');
  function suggestGloss(term) {
    if (!suggest) return;
    const rankOf = e => {
      const t = e.t.toLowerCase();
      if (t === term) return 0;
      if (t.startsWith(term)) return 1;
      if (e.f.some(f => f.startsWith(term))) return 2;
      if (t.includes(term) || e.f.some(f => f.includes(term))) return 3;
      return e.d.toLowerCase().includes(term) ? 4 : 9;
    };
    const hits = term.length < 2 ? [] : GLOSS.map(e => [rankOf(e), e]).filter(h => h[0] < 9)
      .sort((a, b) => a[0] - b[0] || a[1].t.localeCompare(b[1].t)).slice(0, 6);
    suggest.replaceChildren();
    suggest.hidden = !hits.length;
    if (!hits.length) return;
    const label = document.createElement('span');
    label.className = 'k';
    label.textContent = 'In the glossary:';
    suggest.append(label);
    hits.forEach(([, e]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'g-link g-chip';  // opens its entry, as a term in a panel does
      b.dataset.g = e.g;
      b.title = e.d;
      b.textContent = e.t;
      suggest.append(b);
    });
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

  // Keys drive drawings, as a Vega-Lite legend does: a click on an entry (or Enter or Space)
  // selects it, lighting up that entry and every part it stands for while the rest fades; a
  // second click lets it go, and shift-click adds an entry to those selected or takes it
  // away. Hovering changes nothing, on the page or in a panel, so passing over a key or a
  // drawing doesn't flicker. keyControl wires one key; `show` lights its drawings for a set of
  // entries (data-k values).
  function keyControl(box, show) {
    let pinned = new Set();
    function lightUp() {
      box.classList.toggle('hl', pinned.size > 0);
      box.querySelectorAll('li[data-k]').forEach(li => {
        li.classList.toggle('on', pinned.has(li.dataset.k));
        li.setAttribute('aria-pressed', String(pinned.has(li.dataset.k)));
      });
      show(pinned);
    }
    function pin(k, add) {
      if (add) {
        if (pinned.has(k)) pinned.delete(k); else pinned.add(k);
      } else {
        pinned = pinned.size === 1 && pinned.has(k) ? new Set() : new Set([k]);
      }
      lightUp();
    }
    box.addEventListener('keydown', e => {
      const li = e.target.closest('li[data-k]');
      if (li && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); pin(li.dataset.k, e.shiftKey); }
    });
    box.addEventListener('click', e => {
      const li = e.target.closest('li[data-k]');
      if (li) { e.preventDefault(); pin(li.dataset.k, e.shiftKey); }
    });
    return {
      pinned: () => pinned,
      set: keys => { pinned = new Set(keys); lightUp(); },
      pin,
    };
  }
  // light up the parts of one drawing for these entries, fading the rest
  function lightDrawing(svg, sel) {
    svg.classList.toggle('hl', sel.size > 0);
    // a part may stand for two key entries (an output's stem that is also its loop's line)
    svg.querySelectorAll('[data-k]').forEach(e => e.classList.toggle('on', e.dataset.k.split(' ').some(k => sel.has(k))));
  }

  // The panel's key and its drawing; a click elsewhere in the panel, other than on the
  // drawing, lets go of what is pinned, and opening another panel starts from the page's.
  const fig = document.getElementById('d-fig');
  const keyBox = document.getElementById('d-key');
  const panelKey = keyControl(keyBox, sel => { const svg = fig.querySelector('svg'); if (svg) lightDrawing(svg, sel); });
  document.getElementById('detail').addEventListener('click', e => {
    if (!e.target.closest('#d-key li[data-k]') && !fig.contains(e.target) && panelKey.pinned().size) panelKey.set([]);
  });
  // and the other way: a click on a part of the drawing selects its key entry, as a click on
  // the entry would (lighting every part of that kind); on empty drawing, it lets go
  fig.addEventListener('click', e => {
    const part = e.target.closest('[data-k]');
    const k = part && part.dataset.k.split(' ').find(k => keyBox.querySelector('li[data-k="' + k + '"]'));
    if (k) panelKey.pin(k, e.shiftKey);
    else if (panelKey.pinned().size) panelKey.set([]);
  });

  // The page's legend lights up every diagram: a card keeps its strength when it uses any
  // entry selected, with those parts lit, and fades when it uses none. A click anywhere on
  // the page but a legend entry, or Escape, lets go; a card clicked opens its panel with the
  // same entries selected first (its own handler runs before this one).
  const legendBox = document.querySelector('.legend');
  const pageKey = legendBox && keyControl(legendBox, sel => {
    document.querySelectorAll('.card').forEach(card => {
      const svg = card.querySelector('svg');
      const uses = [...sel].some(k => svg.querySelector('[data-k~="' + k + '"]'));
      // a network that has the part but doesn't draw it (a Transformer's residual connections)
      const has = !uses && [...sel].some(k => (card.dataset.has || '').split(' ').includes(k));
      card.classList.toggle('lg-out', sel.size > 0 && !uses && !has);
      card.classList.toggle('lg-has', has);
      lightDrawing(svg, has ? new Set() : sel);  // nothing in it to light: shown whole
    });
  });
  if (pageKey) {
    document.addEventListener('click', e => {
      if (!pageKey.pinned().size || e.target.closest('.legend li[data-k], dialog')) return;
      pageKey.set([]);
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && pageKey.pinned().size && !document.querySelector('dialog[open]')) pageKey.set([]);
    });
  }

  // Step through: the parts of a drawing light up in the order data reaches them (data-s,
  // from guide.py's flow_steps), then the loop, if any; everything shows again at the end. The
  // popup's drawing has one, and so may a primer figure: each gets its own controls (a Step
  // through / Pause / Resume button and a stop), sharing the speed.
  const FLOW_LABELS = { idle: 'Step through', run: 'Pause', pause: 'Resume' };
  // The speed, chosen in the drawing's "⋯" menu and remembered: how long each step shows, and
  // how long its lines take to draw in (cells light as they arrive, via --draw in the CSS)
  const SPEEDS = { slow: [2000, 1200], normal: [1000, 600], fast: [500, 300] };
  let speed = 'normal';
  try { if (SPEEDS[localStorage.getItem('field-guide-speed')]) speed = localStorage.getItem('field-guide-speed'); } catch (e) {}
  // the speed, one for every step-through: each has a button beside it that cycles it
  const RATES = { slow: '0.5×', normal: '1×', fast: '2×' }, NEXT = { slow: 'normal', normal: 'fast', fast: 'slow' };
  function setSpeed(v) {
    speed = v;
    document.querySelectorAll('.d-rate').forEach(b => {
      b.textContent = RATES[v];
      b.setAttribute('aria-label', 'Step-through speed: ' + RATES[v]);
    });
    document.documentElement.style.setProperty('--draw', SPEEDS[v][1] + 'ms');  // every drawing's
  }
  setSpeed(speed);
  document.addEventListener('click', e => {
    if (!e.target.closest('.d-rate')) return;
    setSpeed(NEXT[speed]);  // a running step-through takes it from its next step
    try { localStorage.setItem('field-guide-speed', speed); } catch (err) {}
  });
  const drawMs = () => SPEEDS[speed][1];
  let maskN = 0;
  function drawIn(el, reverse, ms, delay) {
    ms = ms || drawMs();
    delay = delay || 0;  // ms to wait, hidden, before drawing in
    if (!(el instanceof SVGGeometryElement) || el.tagName === 'circle' || !el.closest('[data-k^="link:"]') || !el.isConnected) return;  // a gate's dot fades in
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let L = 0;
    try { L = el.getTotalLength(); } catch (e) { return; }  // not drawn (the panel was closed)
    if (!L) return;
    const head = el.getAttribute('marker-end');
    if (head) el.removeAttribute('marker-end');
    const dashed = getComputedStyle(el).strokeDasharray !== 'none';
    let pen = el, mask = null;
    if (dashed) {  // a solid copy, wider than the line, drawn in a mask over it
      const ns = 'http://www.w3.org/2000/svg';
      const svg = el.ownerSVGElement;
      mask = document.createElementNS(ns, 'mask');
      mask.id = 'draw-' + (++maskN);
      mask.setAttribute('maskUnits', 'userSpaceOnUse');
      ['x', 'y'].forEach(a => mask.setAttribute(a, '-1000'));
      ['width', 'height'].forEach(a => mask.setAttribute(a, '3000'));
      pen = document.createElementNS(ns, 'path');
      pen.setAttribute('d', el.tagName === 'line'
        ? `M${el.getAttribute('x1')},${el.getAttribute('y1')} L${el.getAttribute('x2')},${el.getAttribute('y2')}`
        : el.getAttribute('d'));
      pen.setAttribute('fill', 'none');
      pen.setAttribute('stroke', '#fff');
      pen.setAttribute('stroke-width', '6');
      mask.append(pen);
      (svg.querySelector('defs') || svg).append(mask);
      el.setAttribute('mask', 'url(#' + mask.id + ')');
    }
    pen.style.transition = 'none';
    if ('both' in el.dataset) {  // from both ends at once, meeting in the middle
      pen.style.strokeDasharray = '0 ' + L + ' 0';
      pen.getBoundingClientRect();
      pen.style.transition = 'stroke-dasharray ' + ms + 'ms linear ' + delay + 'ms';
      pen.style.strokeDasharray = L / 2 + ' 0 ' + L / 2;
    } else {
      pen.style.strokeDasharray = L + ' ' + L;
      pen.style.strokeDashoffset = reverse ? -L : L;
      pen.getBoundingClientRect();  // start from there
      pen.style.transition = 'stroke-dashoffset ' + ms + 'ms linear ' + delay + 'ms';
      pen.style.strokeDashoffset = 0;
    }
    setTimeout(() => {
      if (mask) { el.removeAttribute('mask'); mask.remove(); }
      else { pen.style.transition = pen.style.strokeDasharray = pen.style.strokeDashoffset = ''; }
      if (head) el.setAttribute('marker-end', head);
    }, delay + ms + 30);
  }
  // A line lit at this step draws in along the way data travels (drawIn, above). makeFlow sets
  // up one drawing's step-through, run from its controls `ctl`.
  // A line's timing within its step: the line from an output up past the return line is part
  // of the output's stem, so it is not drawn in but fades in with the output (data-late); a line that is part of one flow (a line and the branches off it,
  // data-t0/t1) draws over its share of the time, so each branch leaves as the line reaches it
  function drawPart(e, rev) {
    const ms = drawMs();
    if ('late' in e.dataset) return;  // an output's extended stem: it fades in with the output (CSS)
    else if ('t1' in e.dataset) drawIn(e, rev, Math.max(1, ms * (+e.dataset.t1 - +e.dataset.t0)), ms * +e.dataset.t0);
    else drawIn(e, rev);
  }
  // A chart figure's dot runs along its curve (data-track, the curve's points) to the point a
  // step names (data-at), its dashed guides to both axes and its input's mark going with it
  function placeDot(g, k) {
    const p = g._pts[Math.max(0, Math.min(g._pts.length - 1, Math.round(k)))];
    const [ax, ay] = g.dataset.axes.split(',').map(Number);
    const [v, h] = g.querySelectorAll('line'), c = g.querySelector('.cv-out'), f = g.querySelector('.cv-foot');
    c.setAttribute('cx', p[0]); c.setAttribute('cy', p[1]);
    f.setAttribute('cx', p[0]); f.setAttribute('cy', ay);
    v.setAttribute('x1', p[0]); v.setAttribute('y1', p[1]); v.setAttribute('x2', p[0]); v.setAttribute('y2', ay);
    h.setAttribute('x1', p[0]); h.setAttribute('y1', p[1]); h.setAttribute('x2', ax); h.setAttribute('y2', p[1]);
    g._at = k;
  }
  function runDot(g, to) {
    g._pts = g._pts || g.dataset.track.split(' ').map(t => t.split(',').map(Number));
    cancelAnimationFrame(g._raf);
    const from = g._at === undefined ? to : g._at;
    if (from === to || matchMedia('(prefers-reduced-motion: reduce)').matches) { placeDot(g, to); return; }
    const t0 = performance.now(), ms = 1400;
    const frame = now => {
      const t = Math.min(1, (now - t0) / ms), e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      placeDot(g, from + (to - from) * e);
      if (t < 1) g._raf = requestAnimationFrame(frame);
    };
    g._raf = requestAnimationFrame(frame);
  }
  // `sayEl`: a caption of its own, outside the figure (a panel's, under its drawing)
  function makeFlow(fig, ctl, sayEl) {
    const play = ctl.querySelector('.d-play');
    // the caption: a glossary figure's own, or a panel's, under its drawing
    const sayIdle = sayEl || fig.querySelector('.p-say');
    const idle = sayIdle ? sayIdle.innerHTML : '';
    let timer = null, next = null;  // the next step, kept while paused
    let pending = [];  // lines waiting for their slot in a cascade
    // The caption shows only once stepping starts (at rest the text follows the figure closely,
    // as part of its entry), as tall as its longest line at this width, so the text below moves
    // once, not at every step
    function fitSay() {
      if (!sayIdle || !fig.dataset.say) return;
      const keep = sayIdle.innerHTML;
      sayIdle.style.minHeight = '';
      const tallest = Math.max(...JSON.parse(fig.dataset.say).map(t => {
        sayIdle.innerHTML = t;  // built and escaped by guide.py
        return sayIdle.getBoundingClientRect().height;
      }));
      sayIdle.innerHTML = keep;
      sayIdle.style.minHeight = tallest + 'px';
    }
    window.addEventListener('resize', () => { if (fig.classList.contains('saying')) fitSay(); });
    function setState(state) {
      ctl.dataset.state = state;
      play.querySelector('span').textContent = FLOW_LABELS[state];
    }
    // `reset` (Stop, closing, or the end of a run): the caption clears and its place closes; the
    // last step's line stays up for twice a step's time first, to finish reading
    function stop(reset) {
      if (reset && sayIdle) {
        fig.classList.remove('saying');
        sayIdle.innerHTML = idle;
        sayIdle.style.minHeight = '';
        if (sayEl) sayEl.hidden = true;
      }
      clearTimeout(timer);
      pending.forEach(clearTimeout);
      pending = [];
      timer = null;
      fig.classList.remove('flow');
      fig.querySelectorAll('.later, .mid, .past').forEach(e => e.classList.remove('later', 'mid', 'past'));
      fig.querySelectorAll('[data-lag], [data-arrive]').forEach(e => { e.style.transitionDelay = ''; });
      fig.querySelectorAll('[data-slide]').forEach(e => { e.style.transform = ''; });
      // back to how it rests: as it was before its first step, if it has run (a figure that never
      // ran keeps its own: a bar's resting height is its transform)
      fig.querySelectorAll('[data-move]').forEach(e => { if (e._rest !== undefined) e.style.transform = e._rest; });
      fig.querySelectorAll('[data-track]').forEach(g => { cancelAnimationFrame(g._raf); g._at = undefined; });
      setState('idle');
    }
    function run() {
      const svg = fig.querySelector('svg');
      const n = svg ? +svg.dataset.steps : 0;
      if (!n) return;
      // start from nothing shown, at once: then even the first step lights in its order (a
      // column's output after the lines that reach it), not over a drawing already lit
      const parts = fig.querySelectorAll('[data-s], [data-on]');
      parts.forEach(e => { e.style.transition = 'none'; e.classList.add('later'); });
      fig.classList.add('flow');
      fig.getBoundingClientRect();
      parts.forEach(e => { e.style.transition = ''; });
      setState('run');
      if (fig.dataset.say) {  // only a flow with captions shows the caption's place
        if (sayEl) sayEl.hidden = false;
        fig.classList.add('saying');
        fitSay();
      }
      let i = 0;
      const says = fig.dataset.say ? JSON.parse(fig.dataset.say) : null;  // a line for each step
      const seq = 'seq' in svg.dataset;  // a hand-written flow: parts show at listed steps
      // the steps that cascade, and the most slots in any, which share the step's time
      const lags = Array.from(fig.querySelectorAll('[data-lag]')).flatMap(e => e.dataset.lag.split(' '));
      const cascades = new Set(lags.map(t => +t.split(':')[0]));
      const maxSlot = Math.max(0, ...lags.map(t => +t.split(':')[1]));
      let slotMs = 0;
      const has = (list, n) => (list || '').split(' ').includes(String(n));
      const tick = () => {
        slotMs = maxSlot ? SPEEDS[speed][0] / (maxSlot + 1.5) : 0;
        if (seq) {
          fig.querySelectorAll('[data-on]').forEach(e => {
            const on = has(e.dataset.on, i), mid = has(e.dataset.mid, i);
            // in a cascade step, each part waits for its slot: a cell lights in it, a line draws
            // in the slot before the cell it reaches
            const slot = +(((e.dataset.lag || '').split(' ').find(t => t.startsWith(i + ':')) || ':0').split(':')[1]);
            const cascade = cascades.has(i);
            const lag = cascade ? slot * slotMs : 0;
            e.style.transitionDelay = on && cascade ? lag + 'ms' : on && 'arrive' in e.dataset ? drawMs() * +e.dataset.arrive + 'ms' : '';
            // a cell lit in an earlier step stays faintly, so what was produced stays in view
            // (GPT's earlier outputs), as a pass that builds up keeps everything it reached
            const past = !on && !mid && e.matches('[data-k^="cell:"]') &&
              e.dataset.on.split(' ').some(t => t !== '' && +t < i);
            e.classList.toggle('later', !on && !mid && !past);
            e.classList.toggle('mid', mid);
            e.classList.toggle('past', past);
            // drawn in when it lights, and again when data turns back along it (Gibbs sampling)
            const rev = has(e.dataset.rev, i);
            if (on && !(i && has(e.dataset.on, i - 1) && has(e.dataset.rev, i - 1) === rev)) {
              if (!cascade) drawPart(e, rev);
              else pending.push(setTimeout(() => drawIn(e, rev, slotMs * 0.8), lag));
            }
          });
        } else {
          fig.querySelectorAll('[data-s]').forEach(e => {
            e.classList.toggle('later', +e.dataset.s > i);
            // a cell reached partway through a flow (a branch off a line) lights as it arrives
            if ('arrive' in e.dataset) e.style.transitionDelay = +e.dataset.s === i ? drawMs() * +e.dataset.arrive + 'ms' : '';
            if (+e.dataset.s === i) drawPart(e, false);
          });
        }
        if (says && sayIdle) sayIdle.innerHTML = says[i] || '';  // built and escaped by guide.py
        // a sliding filter moves along to where this step reads, its lines with it
        if (svg.dataset.slideAt) {
          const at = svg.dataset.slideAt.split(' ').map(Number), by = +svg.dataset.slideBy;
          fig.querySelectorAll('[data-slide]').forEach(e => { e.style.transform = `translate(${by * (at[i] || 0)}px, 0)`; });
        }
        // a plane figure's line or arrow turns and shifts to where this step puts it
        fig.querySelectorAll('[data-move]').forEach(e => {
          if (e._rest === undefined) e._rest = e.style.transform;
          e.style.transform = e.dataset.move.split('|')[i] || '';
        });
        fig.querySelectorAll('[data-track]').forEach(g => runDot(g, +(g.dataset.at.split('|')[i] || 0)));
        i += 1;
        // read each step, so a new speed applies from the next. A step with a line to read stays
        // as long as reading it takes at normal speed (about 270 words a minute, plus a second
        // to look at the drawing), halved at fast and doubled at slow; never less than the step
        const line = says ? (says[i - 1] || '').replace(/<[^>]*>/g, ' ') : '';
        const words = line.split(/\s+/).filter(Boolean).length;
        const read = (1200 + 220 * words) * SPEEDS[speed][0] / SPEEDS.normal[0];
        const ms = Math.max(SPEEDS[speed][0], says ? read : 0);
        next = i < n ? tick : () => stop(true);
        timer = setTimeout(next, i < n ? ms : 2 * ms);
      };
      tick();
    }
    // Pause holds the step on screen (lines already drawing finish); Resume goes on to the next
    play.addEventListener('click', () => {
      const state = ctl.dataset.state;
      if (state === 'idle') run();
      else if (state === 'run') { clearTimeout(timer); setState('pause'); }
      else { setState('run'); next(); }
    });
    ctl.querySelector('.d-stop').addEventListener('click', () => stop(true));
    return { stop };
  }
  const flowCtl = document.getElementById('d-flowctl');
  const popupFlow = makeFlow(fig, flowCtl, document.getElementById('d-say'));
  function stopFlow() { popupFlow.stop(true); }  // and clear the caption: it belongs to the panel
  const primerFlows = Array.from(document.querySelectorAll('.p-fig[data-flow]'))
    .map(f => makeFlow(f, f.querySelector('.d-flowctl')));
  primer.addEventListener('close', () => primerFlows.forEach(f => f.stop(true)));
  if (glossary) glossary.addEventListener('close', () => primerFlows.forEach(f => f.stop(true)));

  function setText(id, text) {
    document.getElementById(id).textContent = text || '';
  }

  // A panel opened from a glossary entry's "Used in": its Close says "Back to" that entry and
  // returns there, as the glossary's does over the primer; Esc and a click outside do the same.
  // Previous or Next moves on from the entry, and Close is Close again.
  const closeBtn = document.getElementById('d-close');
  let backTo = null;
  function showBack(slug, term) {
    backTo = slug;
    closeBtn.textContent = 'Back to ' + term;
  }
  function dismiss() {  // the reader closes the panel: back to the entry it came from, if any
    if (!backTo) { close(); return; }
    const slug = backTo;
    if (history.state && history.state.fromGloss === slug) { history.back(); return; }  // the step before
    dlg.addEventListener('close', () => { if (openGlossary) openGlossary(slug, null); }, { once: true });
    quietClose(dlg);
  }
  function open(id, from) {  // `from`: the glossary entry it was opened from, a step of its own
    const d = DATA[id];
    const card = document.querySelector('.card[data-id="' + id + '"]');
    if (!d || !card) return;
    closeBtn.textContent = 'Close';
    backTo = null;
    current = id;
    const svg = card.querySelector('svg').outerHTML.split('arr-' + id).join('arr-' + id + '-big');
    stopFlow();
    document.getElementById('d-fig').innerHTML = svg;
    fig.querySelectorAll('.hl, .on').forEach(e => e.classList.remove('hl', 'on'));  // the page's
    flowCtl.hidden = !+fig.querySelector('svg').dataset.steps;
    if (d.say && d.say.length) fig.dataset.say = JSON.stringify(d.say);
    else delete fig.dataset.say;
    // the card's drawing is hidden from screen readers (the card's text names it); here it is
    // the panel's figure, named, and described by its reading
    const big = fig.querySelector('svg');
    big.removeAttribute('aria-hidden');
    big.setAttribute('role', 'img');
    big.setAttribute('aria-label', 'Diagram: ' + d.name);
    if (d.read) big.setAttribute('aria-describedby', 'd-read'); else big.removeAttribute('aria-describedby');
    // Family, any further families, then the year; a line breaks only between names.
    const kicker = document.getElementById('d-kicker');
    const keep = t => { const s = document.createElement('span'); s.className = 'nw'; s.textContent = t; return s; };
    const also = d.alsoTitles.map((t, i) => (i ? '' : 'also ') + t + (i < d.alsoTitles.length - 1 ? ',' : ''));
    kicker.replaceChildren();
    [d.familyTitle, ...also, String(d.year)].forEach((t, i, all) => {
      kicker.append(keep(t));
      if (i < all.length - 1) kicker.append(i === 0 || i === all.length - 2 ? ' · ' : ' ');
    });
    setText('d-name', d.name);
    setText('d-full', d.full);
    document.getElementById('d-full').hidden = !d.full;
    document.getElementById('d-cap').innerHTML = d.captionHtml;  // escaped by guide.py
    setText('d-note', d.note);
    document.getElementById('d-note').hidden = !d.note;
    document.getElementById('d-summary').innerHTML = d.summaryHtml;
    document.getElementById('d-read').innerHTML = d.readHtml;
    setText('d-objective', (d.objectiveNote ? ', ' + d.objectiveNote : '') +
      (d.alsoObjectives.length ? '; also ' + d.alsoObjectives.join(', ') : ''));
    // the objective's tag, linked to the glossary entry that defines it
    const objEl = document.getElementById('d-objective');
    if (d.objectiveGloss) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'g-link';
      b.dataset.g = d.objectiveGloss;
      b.textContent = d.objective;
      objEl.prepend(b);
    } else {
      objEl.prepend(d.objective);
    }
    // an architecture's objective is its landmark paper's, one of several it could train toward
    document.getElementById('d-scope').hidden = d.scheme;
    document.getElementById('d-read-wrap').hidden = !d.read;
    setMenu(false);
    refreshMenu();
    document.getElementById('d-math').innerHTML = d.objectiveMath;  // built and escaped by guide.py
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
        li.dataset.k = (kind === 'cells' ? 'cell:' : 'link:') + k;
        li.tabIndex = 0;
        li.setAttribute('role', 'button');
        li.setAttribute('aria-pressed', 'false');
        col.append(li);
      }
      key.append(col);
    }
    // start from the page's pinned entries this panel uses
    panelKey.set([...(pageKey ? pageKey.pinned() : [])].filter(k => keyBox.querySelector('li[data-k="' + k + '"]')));
    // Related panels, each a link that opens it here
    const rel = document.getElementById('d-rel');
    rel.textContent = '';
    for (const g of d.related) {
      const row = document.createElement('p');
      const k = document.createElement('span');
      k.className = 'k';
      k.textContent = g.label + ':';
      row.append(k);
      g.ids.forEach((rid, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = DATA[rid].name;
        b.addEventListener('click', () => open(rid));
        row.append(i ? ', ' : ' ', b);
      });
      rel.append(row);
    }
    // other names, variants, and related methods, a row each (search finds them all)
    for (const [label, names] of d.names) {
      const row = document.createElement('p');
      const k = document.createElement('span');
      k.className = 'k';
      k.textContent = label + ':';
      row.append(k, ' ' + names.join(', '));
      rel.append(row);
    }
    document.getElementById('d-rel-wrap').hidden = !d.related.length && !d.names.length;
    const hasEl = document.getElementById('d-has');
    hasEl.textContent = d.has;
    hasEl.hidden = !d.has;
    const ul = document.getElementById('d-papers');
    ul.textContent = '';
    for (const p of d.papers) {
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = p.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = p.title;
      const tab = document.createElement('span');  // said to screen readers only
      tab.className = 'sr-only';
      tab.textContent = ' (opens in a new tab)';
      a.append(tab);
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
    dlg.classList.add('instant');  // the fade only eases in while scrolling
    moreBelow();
    requestAnimationFrame(() => requestAnimationFrame(() => dlg.classList.remove('instant')));
    if (!routing && (opening || from)) push('#' + id, Object.assign({ under: null }, from));
    else if (!routing) {  // Previous or Next: the same step, no longer the entry's panel
      const st = Object.assign({}, history.state);
      delete st.fromGloss;
      delete st.fromTerm;
      try { history.replaceState(st, '', '#' + id); } catch (e) {}
    }
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
    stopFlow();
    if (dlg._quiet) dlg._quiet = false;
    else if (!leave()) try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    const card = current && document.querySelector('.card[data-id="' + current + '"]');
    if (card) card.focus();
  });
  dlg.addEventListener('click', e => { if (e.target === dlg) dismiss(); });
  // the fade above the buttons shows while more of the panel lies below
  function moreBelow() {
    dlg.classList.toggle('more', dlg.scrollTop + dlg.clientHeight < dlg.scrollHeight - 2);
  }
  dlg.addEventListener('scroll', moreBelow, { passive: true });
  // "More below" in a dialog's bottom bar scrolls it on by most of what shows
  document.querySelectorAll('.nav-more:not(.nav-top)').forEach(b => b.addEventListener('click', () => {
    const box = b.closest('dialog');
    box.scrollBy({ top: box.clientHeight * 0.8, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }));
  window.addEventListener('resize', () => { if (dlg.open) moreBelow(); });
  dlg.addEventListener('cancel', e => {
    if (!menu.hidden) { e.preventDefault(); setMenu(false); dlBtn.focus(); }
    else if (backTo) { e.preventDefault(); dismiss(); }  // Esc: back to the entry, as Close
  });
  dlg.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !menu.hidden) { e.preventDefault(); e.stopPropagation(); setMenu(false); dlBtn.focus(); }
  }, true);
  dlg.addEventListener('pointerdown', e => {
    if (!menu.hidden && !menu.contains(e.target) && !dlBtn.contains(e.target)) setMenu(false);
  });
  dlg.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
  });
  document.getElementById('d-prev').addEventListener('click', () => step(-1));
  document.getElementById('d-next').addEventListener('click', () => step(1));
  closeBtn.addEventListener('click', dismiss);
  cards.forEach(c => c.addEventListener('click', () => open(c.dataset.id)));

  // The theme: the page follows the reader's setting until the button is used, then
  // remembers the choice for that reader.
  const root = document.documentElement;
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  // the keyboard's first stop, shown only when focused: past the header and its legend to the
  // first diagram showing (or to the search, when filters hide them all)
  document.getElementById('skip').addEventListener('click', () => {
    const first = Array.from(document.querySelectorAll('.card')).find(c => c.offsetParent);
    (first || document.getElementById('f-search')).focus();
  });
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
  // Show what the address and its step name: the view on top, and any left beneath it. On
  // loading the page, and on Back or Forward.
  function viewOf(h) {
    if (!h) return null;
    if (DATA[h]) return 'panel';
    if (glossary && (h === 'glossary' || h.startsWith('glossary-'))) return 'glossary';
    if (h === 'primer' || h.startsWith('primer-')) return 'primer';
    return null;
  }
  function route() {
    routing = true;
    try {
      const st = history.state || {};
      const h = decodeURIComponent(location.hash.slice(1));
      const want = {};
      [st.under ? decodeURIComponent(st.under.slice(1)) : '', h].forEach(x => { const v = viewOf(x); if (v) want[v] = x; });
      if (glossary && glossary.open && !want.glossary) quietClose(glossary);
      if (primer.open && !want.primer) quietClose(primer);
      // a link straight to the glossary or primer leaves any panel open beneath it as it is
      const keep = !('under' in st) && (want.glossary || want.primer);
      if (want.panel) { if (!dlg.open || current !== want.panel) open(want.panel); }
      else if (dlg.open && !keep) quietClose(dlg);
      // a sheet left beneath keeps its place; the one on top goes to the section named
      // a sheet returned to (left open beneath, or already at that place) keeps its scroll
      const moveTo = (sheet, x) => !sheet.open || (viewOf(h) === viewOf(x) && sheet._hash !== '#' + h);
      if (want.primer && moveTo(primer, want.primer)) openPrimer(want.primer.slice(7));
      if (want.glossary && moveTo(glossary, want.glossary)) openGlossary(want.glossary.slice(9));
      if (want.panel && st.fromGloss && viewOf(h) === 'panel') showBack(st.fromGloss, st.fromTerm || '');
    } finally {
      routing = false;
    }
  }
  window.addEventListener('popstate', route);
  route();

  // The Poster menu: its button opens it, Escape, Tab or a click outside closes it, and the
  // arrow keys move through it
  const pmBtn = document.getElementById('pm-btn');
  const pmMenu = document.getElementById('pm-menu');
  function setPosterMenu(open) {
    pmMenu.hidden = !open;
    pmBtn.setAttribute('aria-expanded', String(open));
    if (open) pmMenu.querySelector('[role="menuitem"]').focus();
  }
  pmBtn.addEventListener('click', () => setPosterMenu(pmMenu.hidden));
  pmMenu.addEventListener('click', e => {
    if (e.target.closest('a[role="menuitem"]')) setPosterMenu(false);
  });
  pmMenu.addEventListener('keydown', e => {
    const items = Array.from(pmMenu.querySelectorAll('[role="menuitem"]'));
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    }
    if (e.key === 'Escape') { e.preventDefault(); setPosterMenu(false); pmBtn.focus(); }
    if (e.key === 'Tab') setPosterMenu(false);
  });
  document.addEventListener('click', e => {
    if (!pmMenu.hidden && !pmMenu.contains(e.target) && !pmBtn.contains(e.target)) setPosterMenu(false);
  });

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
    saveFile = async (name, blob) => {
      try {
        await downloads.save({ filename: name, data: blob });
      } catch (e) {
        if (declined(e) || !malformed(e)) throw e;
        await downloads.save({ filename: name, data: new Uint8Array(await blob.arrayBuffer()) });
      }
    };
    refreshMenu();
    box.querySelectorAll('button[data-file]').forEach(btn => btn.addEventListener('click', async () => {
      const name = btn.dataset.name;
      setPosterMenu(false);
      document.getElementById('pm-btn').focus();
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
        const shown = status.textContent;  // it sits under the button, so it clears itself
        setTimeout(() => { if (status.textContent === shown) status.textContent = ''; }, 5000);
      }
    }));
  })();
})();
