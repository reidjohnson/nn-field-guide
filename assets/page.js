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
    menu.querySelector('[data-act="link"]').hidden = !standalone;
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
      if (b.dataset.act === 'link') {
        await navigator.clipboard.writeText(location.origin + location.pathname + '#' + id);
        const label = b.textContent;
        b.textContent = 'Link copied';
        setTimeout(() => { b.textContent = label; }, 1500);
        return;  // the menu stays open so the confirmation shows
      }
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
  // The primer's live network: the inputs come from the sliders, and each other unit takes
  // the weighted sum of the units below it, squashed by a sigmoid. A unit's brightness shows
  // its activity; a line's width shows its weight's size and its shade the sign (gray
  // subtracts). New weights draws a fresh set, as untrained. Pointing at a unit lights the
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

  // The primer, opened by the "?" button
  // and by a link to #primer or #primer-<anchor>, which opens it at that section. While open,
  // the address shows #primer, so it can be shared; closing puts back the address it replaced.
  const primer = document.getElementById('primer');
  let primerFrom = null;
  let primerPrev = null;
  function openPrimer(anchor, from) {
    primerFrom = from || null;
    if (!primer.open) {
      primerPrev = location.hash.startsWith('#primer') ? '' : location.hash;  // opened from its own link
      try { history.replaceState(null, '', '#primer' + (anchor ? '-' + anchor : '')); } catch (e) {}
      if (typeof primer.showModal === 'function') primer.showModal(); else primer.setAttribute('open', '');
    }
    const target = anchor && document.getElementById('primer-' + anchor);
    primer.scrollTop = 0;
    if (target) {
      primer.scrollTop = target.getBoundingClientRect().top - primer.getBoundingClientRect().top - 28;
      target.focus({ preventScroll: true });
    } else {
      document.getElementById('p-title').focus({ preventScroll: true });
    }
    primer.classList.add('instant');  // the fade above Close, as on a panel: set at once on opening
    primerMore();
    requestAnimationFrame(() => requestAnimationFrame(() => primer.classList.remove('instant')));
  }
  // the fade above Close shows while more of the primer lies below
  function primerMore() {
    primer.classList.toggle('more', primer.scrollTop + primer.clientHeight < primer.scrollHeight - 2);
  }
  primer.addEventListener('scroll', primerMore, { passive: true });
  window.addEventListener('resize', () => { if (primer.open) primerMore(); });
  document.getElementById('help').addEventListener('click', e => openPrimer(null, e.currentTarget));
  document.getElementById('p-close').addEventListener('click', () => primer.close());
  primer.addEventListener('click', e => { if (e.target === primer) primer.close(); });
  primer.addEventListener('close', () => {
    try { history.replaceState(null, '', location.pathname + location.search + (primerPrev || '')); } catch (e) {}
    (primerFrom || document.getElementById('help')).focus();
  });
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
    for (const s of sections) {
      const n = s.querySelectorAll('.card:not([hidden])').length;
      s.style.gridColumn = packOn && n && n < cols ? 'span ' + n : '';
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
    };
  }
  // light up the parts of one drawing for these entries, fading the rest
  function lightDrawing(svg, sel) {
    svg.classList.toggle('hl', sel.size > 0);
    svg.querySelectorAll('[data-k]').forEach(e => e.classList.toggle('on', sel.has(e.dataset.k)));
  }

  // The panel's key and its drawing; a click elsewhere in the panel, other than on the
  // drawing, lets go of what is pinned, and opening another panel starts from the page's.
  const fig = document.getElementById('d-fig');
  const keyBox = document.getElementById('d-key');
  const panelKey = keyControl(keyBox, sel => { const svg = fig.querySelector('svg'); if (svg) lightDrawing(svg, sel); });
  document.getElementById('detail').addEventListener('click', e => {
    if (!e.target.closest('#d-key li[data-k]') && !fig.contains(e.target) && panelKey.pinned().size) panelKey.set([]);
  });

  // The page's legend lights up every diagram: a card keeps its strength when it uses any
  // entry selected, with those parts lit, and fades when it uses none. A click anywhere on
  // the page but a legend entry, or Escape, lets go; a card clicked opens its panel with the
  // same entries selected first (its own handler runs before this one).
  const legendBox = document.querySelector('.legend');
  const pageKey = legendBox && keyControl(legendBox, sel => {
    document.querySelectorAll('.card').forEach(card => {
      const svg = card.querySelector('svg');
      const uses = [...sel].some(k => svg.querySelector('[data-k="' + k + '"]'));
      card.classList.toggle('lg-out', sel.size > 0 && !uses);
      lightDrawing(svg, sel);
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

  // Step through: the parts of the drawing light up in the order data reaches them (data-s,
  // from guide.py's flow_steps), then the loop, if any; everything shows again at the end.
  const play = document.getElementById('d-play');
  let flowTimer = null;
  let pending = [];  // lines waiting for their slot in a cascade
  function stopFlow() {
    clearTimeout(flowTimer);
    pending.forEach(clearTimeout);
    pending = [];
    flowTimer = null;
    fig.classList.remove('flow');
    fig.querySelectorAll('.later, .mid, .past').forEach(e => e.classList.remove('later', 'mid', 'past'));
    fig.querySelectorAll('[data-lag]').forEach(e => { e.style.transitionDelay = ''; });
    setFlowState('idle');
  }
  const flowCtl = document.getElementById('d-flowctl');
  const FLOW_LABELS = { idle: 'Step through', run: 'Pause', pause: 'Resume' };
  function setFlowState(state) {
    flowCtl.dataset.state = state;
    play.querySelector('span').textContent = FLOW_LABELS[state];
  }
  let flowNext = null;  // the next step, kept while paused
  document.getElementById('d-stop').addEventListener('click', () => stopFlow());
  // A line lit at this step draws in along the way data travels, from the end it leaves to
  // the end it reaches, its arrowhead last; a dashed or dotted line is revealed through a mask
  // drawn the same way, so its dashes stay put. Cells fade in as the lines reach them (CSS).
  // The speed, chosen in the drawing's "⋯" menu and remembered: how long each step shows, and
  // how long its lines take to draw in (cells light as they arrive, via --draw in the CSS)
  const SPEEDS = { slow: [2000, 1200], normal: [1000, 600], fast: [500, 300] };
  let speed = 'normal';
  try { if (SPEEDS[localStorage.getItem('field-guide-speed')]) speed = localStorage.getItem('field-guide-speed'); } catch (e) {}
  const speedBox = document.getElementById('d-speed');
  function setSpeed(v) {
    speed = v;
    speedBox.querySelectorAll('[data-speed]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.speed === v)));
    fig.style.setProperty('--draw', SPEEDS[v][1] + 'ms');
  }
  setSpeed(speed);
  speedBox.addEventListener('click', e => {
    const b = e.target.closest('[data-speed]');
    if (!b) return;
    setSpeed(b.dataset.speed);
    try { localStorage.setItem('field-guide-speed', b.dataset.speed); } catch (e) {}
    setMenu(false);
    dlBtn.focus();
  });
  const drawMs = () => SPEEDS[speed][1];
  let maskN = 0;
  function drawIn(el, reverse, ms) {
    ms = ms || drawMs();
    if (!(el instanceof SVGGeometryElement) || !el.closest('[data-k^="link:"]') || !el.isConnected) return;
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
      pen.style.transition = 'stroke-dasharray ' + ms + 'ms ease-out';
      pen.style.strokeDasharray = L / 2 + ' 0 ' + L / 2;
    } else {
      pen.style.strokeDasharray = L + ' ' + L;
      pen.style.strokeDashoffset = reverse ? -L : L;
      pen.getBoundingClientRect();  // start from there
      pen.style.transition = 'stroke-dashoffset ' + ms + 'ms ease-out';
      pen.style.strokeDashoffset = 0;
    }
    setTimeout(() => {
      if (mask) { el.removeAttribute('mask'); mask.remove(); }
      else { pen.style.transition = pen.style.strokeDasharray = pen.style.strokeDashoffset = ''; }
      if (head) el.setAttribute('marker-end', head);
    }, ms + 30);
  }
  function runFlow() {
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
    setFlowState('run');
    let i = 0;
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
          e.style.transitionDelay = on && cascade ? lag + 'ms' : '';
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
            if (!cascade) drawIn(e, rev);
            else pending.push(setTimeout(() => drawIn(e, rev, slotMs * 0.8), lag));
          }
        });
      } else {
        fig.querySelectorAll('[data-s]').forEach(e => {
          e.classList.toggle('later', +e.dataset.s > i);
          if (+e.dataset.s === i) drawIn(e, false);
        });
      }
      i += 1;
      const ms = SPEEDS[speed][0];  // read each step, so a new speed applies from the next
      flowNext = i < n ? tick : stopFlow;
      flowTimer = setTimeout(flowNext, i < n ? ms : 2 * ms);
    };
    tick();
  }
  // Pause holds the step on screen (lines already drawing finish); Resume goes on to the next
  play.addEventListener('click', () => {
    const state = flowCtl.dataset.state;
    if (state === 'idle') runFlow();
    else if (state === 'run') { clearTimeout(flowTimer); setFlowState('pause'); }
    else { setFlowState('run'); flowNext(); }
  });

  function setText(id, text) {
    document.getElementById(id).textContent = text || '';
  }

  function open(id) {
    const d = DATA[id];
    const card = document.querySelector('.card[data-id="' + id + '"]');
    if (!d || !card) return;
    current = id;
    const svg = card.querySelector('svg').outerHTML.split('arr-' + id).join('arr-' + id + '-big');
    stopFlow();
    document.getElementById('d-fig').innerHTML = svg;
    fig.querySelectorAll('.hl, .on').forEach(e => e.classList.remove('hl', 'on'));  // the page's
    flowCtl.hidden = !+fig.querySelector('svg').dataset.steps;
    speedBox.hidden = flowCtl.hidden;  // the speed only where there is a step-through
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
    setText('d-objective', d.objective + (d.objectiveNote ? ', ' + d.objectiveNote : '') +
      (d.alsoObjectives.length ? '; also ' + d.alsoObjectives.join(', ') : ''));
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
    if (d.aka.length) {  // other names and close variants, which search also finds
      const row = document.createElement('p');
      const k = document.createElement('span');
      k.className = 'k';
      k.textContent = 'Variants and other names:';
      row.append(k, ' ' + d.aka.join(', '));
      rel.append(row);
    }
    document.getElementById('d-rel-wrap').hidden = !d.related.length && !d.aka.length;
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
    dlg.classList.add('instant');  // the fade only eases in while scrolling
    moreBelow();
    requestAnimationFrame(() => requestAnimationFrame(() => dlg.classList.remove('instant')));
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
    stopFlow();
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    const card = current && document.querySelector('.card[data-id="' + current + '"]');
    if (card) card.focus();
  });
  dlg.addEventListener('click', e => { if (e.target === dlg) close(); });
  // the fade above the buttons shows while more of the panel lies below
  function moreBelow() {
    dlg.classList.toggle('more', dlg.scrollTop + dlg.clientHeight < dlg.scrollHeight - 2);
  }
  dlg.addEventListener('scroll', moreBelow, { passive: true });
  window.addEventListener('resize', () => { if (dlg.open) moreBelow(); });
  dlg.addEventListener('cancel', e => {
    if (!menu.hidden) { e.preventDefault(); setMenu(false); dlBtn.focus(); }
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
  else if (hash === 'primer' || hash.startsWith('primer-')) openPrimer(hash.slice(7));

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
