#!/usr/bin/env node
// ═══════════════════════════════════════════════════════════════════════
// ddg_vqd.js — DuckDuckGo x-vqd-hash-1 challenge solver (headless, v2)
//
// Reads a base64 challenge blob on stdin (or argv[1]), executes it against a
// GLOBALS-INSTALLED clean-browser environment, prints the raw solution JSON:
//   {"server_hashes":[...], "client_hashes":[ua, probe2, probe3],
//    "signals":{}, "meta":{v, challenge_id, timestamp, debug}}
//
// Why globals (not Function params): the pxjzr probe family checks
//   (function(){return this;}()) === window
// which is only true when `window` IS the global object — exactly like a
// real browser page. The solver runs as a dedicated subprocess, so
// polluting globalThis is safe and gets us that semantics for free.
//
// Clean-browser facts emulated (verified against captured real-browser hashes):
//   - window.parseInt.toString() includes '[native code]'     (real native)
//   - class X extends Array; X.prototype.map → instanceof X    (ES6 semantics)
//   - Object.prototype.toString.call(window) === '[object Window]'
//   - navigator.webdriver === true → FALSE (human browsing)
//   - iframe.contentWindow.self.get → undefined (no instrumentation)
//   - window.top has no polluted _Array/_Promise/... aliases
//   - document.body.children tracks appendChild/removeChild
//   - document.querySelectorAll('*') → real NodeList (not an Array)
//   - document.createElement('div') instanceof HTMLDivElement,
//     HTMLDivElement.prototype instanceof HTMLElement instanceof Element
//   - DOM elements measure > 0 (offsetWidth/Height, scrollHeight, rect)
//   - getComputedStyle(...).getPropertyValue('display') non-empty
//   - innerHTML + querySelectorAll → real HTML-parsed element counts
//   - meta[http-equiv=Content-Security-Policy] present (duck.ai CSP)
//
// Usage:  node ddg_vqd.js [--report] [challenge.b64 | -] < blob
// Env:    DDG_SOLVE_UA  override navigator.userAgent
// ═══════════════════════════════════════════════════════════════════════
'use strict';

const REPORT = process.argv.includes('--report');
const accesses = new Map();
function note(p) { if (REPORT) accesses.set(p, (accesses.get(p) || 0) + 1); }

const G = globalThis;

// ── DOM class hierarchy (instanceof chains must hold) ──────────────────
class Element {
  constructor(tagName) {
    this.tagName = String(tagName || 'div').toUpperCase();
    this.style = {};
    this.textContent = '';
    this._attributes = {};
    this._parsed = [];
    this.children = [];
  }
  get offsetWidth() { note('el.offsetWidth'); return 8; }
  get offsetHeight() { note('el.offsetHeight'); return 8; }
  get scrollHeight() { note('el.scrollHeight'); return 8; }
  get scrollWidth() { note('el.scrollWidth'); return 8; }
  getBoundingClientRect() {
    note('el.getBoundingClientRect');
    return { width: 8, height: 8, top: 0, left: 0, right: 8, bottom: 8, x: 0, y: 0 };
  }
  set innerHTML(html) { note('el.innerHTML(set)'); this._parsed = parseHtml(String(html)); }
  get innerHTML() { return ''; }
  querySelectorAll(sel) {
    note('el.querySelectorAll:' + sel);
    return this._parsed.filter(e => selectorMatches(e, sel));
  }
  querySelector(sel) {
    note('el.querySelector:' + sel);
    return this._parsed.find(e => selectorMatches(e, sel)) || null;
  }
  getAttribute(name) {
    note('el.getAttribute:' + name);
    return Object.prototype.hasOwnProperty.call(this._attributes, name)
      ? this._attributes[name] : null;
  }
  setAttribute(name, value) { this._attributes[name] = String(value); }
  appendChild(child) { note('el.appendChild'); if (child) this.children.push(child); }
  removeChild(child) {
    note('el.removeChild');
    const i = this.children.indexOf(child);
    if (i !== -1) this.children.splice(i, 1);
  }
}
class HTMLElement extends Element {}
class HTMLDivElement extends HTMLElement {}
class HTMLMetaElement extends HTMLElement {}
class HTMLIFrameElement extends HTMLElement {}

// NodeList — a real class, NOT an Array subclass
class NodeList {
  constructor(items) { this._items = items.slice(); }
  get length() { return this._items.length; }
  item(i) { return this._items[i] || null; }
  entries() { return this._items.entries(); }
  forEach(fn, thisArg) { this._items.forEach(fn, thisArg); }
  [Symbol.iterator]() { return this._items[Symbol.iterator](); }
}

// ── mini HTML parser (for innerHTML/querySelectorAll probes) ───────────
const VOID_TAGS = new Set(['area','base','br','col','embed','hr','img','input',
  'link','meta','param','source','track','wbr']);

function parseHtml(html) {
  const els = [], stack = [];
  const re = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>?/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const closing = m[1] === '/';
    const tag = m[2].toLowerCase();
    if (closing) {
      const idx = stack.lastIndexOf(tag);
      if (idx !== -1) stack.length = idx;
    } else {
      els.push({ tag });
      if (!VOID_TAGS.has(tag)) stack.push(tag);
    }
  }
  return els;
}
function selectorMatches(el, sel) {
  if (sel === '*') return true;
  return el.tag === String(sel).trim().toLowerCase();
}

// ── element factory with the right per-tag class ───────────────────────
function makeElement(tagName) {
  const t = String(tagName || 'div').toLowerCase();
  let el;
  if (t === 'div') el = new HTMLDivElement();
  else if (t === 'meta') el = new HTMLMetaElement();
  else if (t === 'iframe') el = new HTMLIFrameElement();
  else el = new HTMLElement(t);
  if (t === 'iframe') {
    // window-like self, NO 'get' property (matches a real browser)
    el.contentWindow = { self: {} };
    el.contentDocument = null;
  }
  return el;
}

// ── document ────────────────────────────────────────────────────────────
const DUCK_CSP = "default-src 'none' ; connect-src  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai ; manifest-src  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai ; media-src 'self'  https://duck.ai https://*.duck.ai ; script-src blob:  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai 'unsafe-inline' 'unsafe-eval' ; font-src data:  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai ; img-src 'self' data:  https://duck.ai https://*.duck.ai https://external-content.duckduckgo.com/ip3/ https://improving.duckduckgo.com/t/ ; style-src  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai 'unsafe-inline' ; object-src 'none' ; worker-src blob: ; child-src blob:  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai ; frame-src blob:  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai ; form-action  https://duckduckgo.com https://*.duckduckgo.com https://duck.ai https://*.duck.ai ; frame-ancestors  https://duckduckgo.com https://*.duckduckgo.com ; base-uri 'self' ; block-all-mixed-content ;";

const metaCsp = makeElement('meta');
metaCsp._attributes['http-equiv'] = 'Content-Security-Policy';
metaCsp._attributes['content'] = DUCK_CSP;

const _body = makeElement('body');
Object.defineProperty(_body, 'children', {
  get() { return _body._kids; },
  configurable: true,
});
_body._kids = [];

const documentStub = {
  createElement(tag) { note('document.createElement:' + tag); return makeElement(tag); },
  get body() { return _body; },
  querySelector(sel) {
    note('document.querySelector:' + sel);
    if (sel && String(sel).indexOf('Content-Security-Policy') !== -1) return metaCsp;
    return null;
  },
  querySelectorAll(sel) {
    note('document.querySelectorAll:' + sel);
    return new NodeList([]);
  },
  getElementsByTagName() { return new NodeList([]); },
  getElementById() { return null; },
  documentElement: makeElement('html'),
  head: makeElement('head'),
};
_body.appendChild = function (child) { note('body.appendChild'); if (child) _body._kids.push(child); };
_body.removeChild = function (child) {
  note('body.removeChild');
  const i = _body._kids.indexOf(child);
  if (i !== -1) _body._kids.splice(i, 1);
};

// ── navigator / getComputedStyle ────────────────────────────────────────
const navigatorStub = {
  userAgent: process.env.DDG_SOLVE_UA ||
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
  webdriver: false,
  language: 'en-US',
  languages: ['en-US', 'en'],
  platform: 'Linux x86_64',
  hardwareConcurrency: 8,
  maxTouchPoints: 0,
};

function getComputedStyleStub(el) {
  note('getComputedStyle');
  return {
    getPropertyValue(prop) {
      note('cs.getPropertyValue:' + prop);
      if (prop === 'display') {
        const css = (el && el.style && el.style.cssText) || '';
        const m = /display\s*:\s*([a-z-]+)/i.exec(css);
        return m ? m[1] : 'block';
      }
      return '';
    },
  };
}

// ── install the browser globals onto globalThis ─────────────────────────
// window IS globalThis — the pxjzr probe demands (function(){return this}()) === window
const SAVED = {};
const TO_INSTALL = {
  window: G,
  self: G,
  top: G,
  parent: G,
  frames: G,
  document: documentStub,
  navigator: navigatorStub,
  getComputedStyle: getComputedStyleStub,
  location: { href: 'https://duck.ai/', origin: 'https://duck.ai', protocol: 'https:', host: 'duck.ai' },
  HTMLElement, HTMLDivElement, HTMLMetaElement, HTMLIFrameElement, Element, NodeList,
  localStorage: {}, sessionStorage: {},
  alert: () => {}, confirm: () => true, prompt: () => '',
  innerWidth: 1920, innerHeight: 1080, outerWidth: 1920, outerHeight: 1080,
  screenX: 0, screenY: 0, pageXOffset: 0, pageYOffset: 0, scrollX: 0, scrollY: 0,
  devicePixelRatio: 1,
  screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040 },
  history: { length: 1 },
};

for (const [k, v] of Object.entries(TO_INSTALL)) {
  SAVED[k] = G[k];
  try { Object.defineProperty(G, k, { value: v, writable: true, configurable: true }); } catch (e) { /* ignore */ }
}
// [object Window] — override Node's 'global' toStringTag
const SAVED_TAG = Object.getOwnPropertySymbols(G).find(s => s === Symbol.toStringTag);
const HAD_TAG = Object.getOwnPropertySymbols(G).includes(Symbol.toStringTag);
try {
  Object.defineProperty(G, Symbol.toStringTag, { value: 'Window', configurable: true });
} catch (e) { /* ignore */ }

// ── run the challenge ───────────────────────────────────────────────────
let b64 = null;
const argi = process.argv.slice(2).filter(a => a !== '--report');
if (argi.length && argi[0] !== '-') {
  b64 = require('fs').readFileSync(argi[0], 'utf8');
} else {
  b64 = require('fs').readFileSync(0, 'utf8');
}
const js = Buffer.from(String(b64).trim(), 'base64').toString('utf8');

const t0 = Date.now();
let result = null, failure = null;
try {
  // new Function body: free identifiers resolve to our installed globals;
  // non-strict `this` === globalThis === window
  result = new Function('return (' + js + ');')();
} catch (e) { failure = e; }

Promise.resolve(result).then(v => {
  if (REPORT) {
    console.error('── API accesses (' + (Date.now() - t0) + 'ms) ──');
    for (const [k, c] of [...accesses.entries()].sort()) console.error('  ' + k + ' ×' + c);
  }
  if (!v || typeof v !== 'object' || !Array.isArray(v.client_hashes)) {
    process.stderr.write('SHAPE_ERROR: ' + JSON.stringify(v) + '\n');
    process.exit(2);
  }
  process.stdout.write(JSON.stringify(v));
  process.exit(0);
}).catch(e => {
  if (REPORT) {
    console.error('── API accesses before failure ──');
    for (const [k, c] of [...accesses.entries()].sort()) console.error('  ' + k + ' ×' + c);
  }
  process.stderr.write('CHALLENGE_EXEC_ERROR: ' + e.message + '\n');
  process.exit(1);
});
