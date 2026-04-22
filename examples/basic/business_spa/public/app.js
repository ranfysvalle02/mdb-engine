// ------------------------------ primitives ------------------------------
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const BASE = location.origin;
const state = {
  session: null, currentView: 'home', authMode: 'login',
  services: [], leadFilter: '', apptFilter: '',
  slideIdx: 0, slideCount: 0, slideTimer: null,
};

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body === undefined) delete opts.headers['Content-Type'];
  else opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  let data = null;
  try { data = await res.json(); } catch {}
  return { ok: res.ok, status: res.status, data };
}

function toast(msg, type = 'ok') {
  const e = document.createElement('div');
  e.className = 'toast toast-' + type;
  e.textContent = msg;
  $('#toasts').appendChild(e);
  setTimeout(() => {
    e.style.opacity = '0';
    e.style.transition = 'opacity .3s';
    setTimeout(() => e.remove(), 300);
  }, 2800);
}

function esc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }
function attr(s) { return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function fmt(iso) { if (!iso) return ''; try { return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); } catch { return iso; } }
function fmtMoney(p, c) { if (p == null) return '\u2014'; const cur = c || 'USD'; try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(p); } catch { return cur + ' ' + p; } }
function stars(n){ n=Math.max(1,Math.min(5,n||5)); return '\u2605'.repeat(n) + '\u2606'.repeat(5-n); }

function isAdmin() { return state.session?.user?.role === 'admin' || state.session?.user?.roles?.includes?.('admin'); }
function isAuthed() { return !!state.session?.authenticated; }

// ------------------------------ navigation ------------------------------
// Section order used by the scroll-spy (matches marketing sections in DOM order).
const NAV_SECTIONS = ['about', 'services', 'gallery', 'testimonials', 'hours', 'find-us', 'faq', 'contact'];

$$('nav.top .links button').forEach(b => b.addEventListener('click', () => navClick(b)));

function navClick(el) {
  const navId = el.dataset.nav;
  const viewId = el.dataset.v;
  closeNav();
  if (viewId) { go(viewId); return; }
  if (!navId) return;
  if (state.currentView !== 'home') {
    state.currentView = 'home';
    $$('.view').forEach(v => v.classList.remove('on'));
    $('#v-home')?.classList.add('on');
    startSlideTimer();
    setTimeout(() => scrollToSection(navId), 80);
  } else {
    scrollToSection(navId);
  }
}

function scrollToSection(id) {
  if (!id || id === 'home') { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
  const el = document.getElementById(id);
  if (!el) return;
  const navH = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--nav-h')) || 58;
  const y = el.getBoundingClientRect().top + window.scrollY - navH - 12;
  window.scrollTo({ top: y, behavior: 'smooth' });
}

function go(v) {
  if (v === 'admin' && !isAdmin()) {
    toast('Admin access required', 'err');
    showAuthPanel('login');
    return;
  }
  state.currentView = v;
  $$('.view').forEach(el => el.classList.remove('on'));
  $('#v-' + v)?.classList.add('on');
  if (v === 'home') { startSlideTimer(); syncNavActive('home'); }
  else { stopSlideTimer(); syncNavActive(v); }
  if (v === 'book') loadBook();
  if (v === 'admin') loadAdmin();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function syncNavActive(id) {
  $$('nav.top .links button').forEach(b => {
    const k = b.dataset.nav || b.dataset.v;
    b.classList.toggle('on', k === id);
  });
  moveIndicator();
}

function moveIndicator() {
  const ind = document.querySelector('nav.top .nav-indicator');
  if (!ind) return;
  const active = document.querySelector('nav.top .links button.on:not(.hidden)');
  const parent = ind.parentElement;
  if (!active || !parent || document.body.classList.contains('nav-open')) {
    ind.classList.remove('ready');
    return;
  }
  const pRect = parent.getBoundingClientRect();
  const rect = active.getBoundingClientRect();
  if (rect.width === 0) { ind.classList.remove('ready'); return; }
  ind.style.setProperty('--nav-x', `${rect.left - pRect.left}px`);
  ind.style.setProperty('--nav-w', `${rect.width}px`);
  ind.classList.add('ready');
}

// Scroll-spy: pick the deepest section whose top has passed the nav line.
let _spyTicking = false;
function onScrollSpy() {
  if (_spyTicking) return;
  _spyTicking = true;
  requestAnimationFrame(() => {
    _spyTicking = false;
    const nav = $('#topNav');
    if (nav) nav.classList.toggle('scrolled', window.scrollY > 6);
    if (state.currentView !== 'home') return;
    const y = window.scrollY;
    if (y < 80) { syncNavActive('home'); return; }
    const navH = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--nav-h')) || 58;
    const line = y + navH + 40;
    let active = 'home';
    for (const id of NAV_SECTIONS) {
      const el = document.getElementById(id);
      if (!el) continue;
      if (el.offsetTop <= line) active = id;
    }
    syncNavActive(active);
  });
}

function initScrollspy() {
  window.addEventListener('scroll', onScrollSpy, { passive: true });
  window.addEventListener('resize', () => requestAnimationFrame(moveIndicator), { passive: true });
  onScrollSpy();
  moveIndicator();
}

// ------------------------------ mobile nav ------------------------------
function toggleNav() {
  document.body.classList.contains('nav-open') ? closeNav() : openNav();
}
function openNav() {
  document.body.classList.add('nav-open', 'nav-lock');
  $('#navToggle')?.setAttribute('aria-expanded', 'true');
  moveIndicator();
}
function closeNav() {
  document.body.classList.remove('nav-open', 'nav-lock');
  $('#navToggle')?.setAttribute('aria-expanded', 'false');
  moveIndicator();
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.body.classList.contains('nav-open')) closeNav();
});

// ------------------------------ auth ------------------------------
// Only admin auth is surfaced in the UI. Non-admin (guest/visitor) sessions
// are created silently when needed for intake forms — there's no "My requests"
// portal to expose, so we keep the top-nav auth controls admin-focused.
function renderAuth() {
  const area = $('#authArea');
  if (!isAdmin()) {
    area.innerHTML = '<button class="btn btn-o btn-sm" onclick="showAuthPanel(\'login\')">Admin sign in</button>';
    $$('.admin-only').forEach(el => el.classList.add('hidden'));
  } else {
    const u = state.session.user || {};
    area.innerHTML = `<span class="auth-user">${esc(u.email)} (admin)</span><button class="btn btn-o btn-sm" onclick="logout()">Logout</button>`;
    $$('.admin-only').forEach(el => el.classList.remove('hidden'));
  }
  requestAnimationFrame(() => moveIndicator());
}

function showAuthPanel(mode) {
  state.authMode = mode;
  $('#authPanel').classList.add('on');
  $('#authPanelTitle').textContent = mode === 'register' ? 'Create account' : 'Sign in';
  $('#authSubmitBtn').textContent = mode === 'register' ? 'Register' : 'Sign in';
  $('#authPanel').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function hideAuthPanel() { $('#authPanel').classList.remove('on'); }

$('#authSubmitBtn').onclick = async () => {
  const email = $('#authEmail').value.trim().toLowerCase();
  const password = $('#authPassword').value.trim();
  if (!email || !password) { toast('Email and password required', 'err'); return; }
  const path = state.authMode === 'register' ? '/auth/register' : '/auth/login';
  const r = await api('POST', path, { email, password });
  if (!r.ok) { toast(r.data?.detail || 'Auth failed', 'err'); return; }
  await refreshSession();
  hideAuthPanel();
  $('#authEmail').value = ''; $('#authPassword').value = '';
  toast(state.authMode === 'register' ? 'Welcome!' : 'Signed in');
  go(state.currentView);
};

async function logout() {
  await api('POST', '/auth/logout', {});
  await refreshSession();
  toast('Signed out', 'info');
  go('home');
}

async function refreshSession() {
  const r = await api('GET', '/auth/me');
  state.session = r.data || { authenticated: false, user: null };
  renderAuth();
}

// Guest auto-registration for first-time visitors submitting the intake form.
async function ensureVisitorSession(email) {
  if (isAuthed()) return true;
  const normalized = email.trim().toLowerCase();
  const key = 'business_spa.visitor.' + normalized;
  let password = localStorage.getItem(key);
  if (!password) {
    password = 'v-' + Math.random().toString(36).slice(2, 10) + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(key, password);
  }
  let r = await api('POST', '/auth/login', { email: normalized, password });
  if (!r.ok) {
    r = await api('POST', '/auth/register', { email: normalized, password });
    if (!r.ok) {
      toast(r.data?.detail || 'Could not create visitor account', 'err');
      return false;
    }
  }
  await refreshSession();
  return true;
}

// ------------------------------ home: slideshow (hydrates SSR-rendered slides) ------------------------------
function initSlideshow() {
  const root = $('#heroSlider');
  if (!root) return;
  const slides = root.querySelectorAll('.hero-slide');
  state.slideCount = slides.length;
  state.slideIdx = 0;
  if (slides.length < 2) return;
  const nav = $('#heroNav');
  if (nav) {
    nav.innerHTML = Array.from(slides, (_, i) =>
      `<div class="hero-dot${i === 0 ? ' on' : ''}" onclick="slideGo(${i})"></div>`
    ).join('');
  }
  startSlideTimer();
  root.onmouseenter = stopSlideTimer;
  root.onmouseleave = startSlideTimer;
}

function slideGo(i) {
  if (!state.slideCount) return;
  const slides = $$('#heroSlider .hero-slide');
  const dots = $$('#heroNav .hero-dot');
  state.slideIdx = ((i % state.slideCount) + state.slideCount) % state.slideCount;
  slides.forEach((el, idx) => el.classList.toggle('on', idx === state.slideIdx));
  dots.forEach((el, idx) => el.classList.toggle('on', idx === state.slideIdx));
}
function slideStep(d) { slideGo(state.slideIdx + d); stopSlideTimer(); startSlideTimer(); }
function startSlideTimer() {
  stopSlideTimer();
  if (state.slideCount < 2 || state.currentView !== 'home') return;
  state.slideTimer = setInterval(() => slideGo(state.slideIdx + 1), 6000);
}
function stopSlideTimer() { if (state.slideTimer) { clearInterval(state.slideTimer); state.slideTimer = null; } }

// ------------------------------ home: scroll-reveal ------------------------------
// Fade + translate sections as they enter the viewport. Uses IntersectionObserver
// (supported everywhere we care about) and bails out gracefully for users who
// set prefers-reduced-motion, who see the content with no motion at all.
function initReveal() {
  const nodes = $$('[data-reveal]');
  if (!nodes.length) return;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || !('IntersectionObserver' in window)) {
    nodes.forEach(n => n.classList.add('revealed'));
    return;
  }
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        e.target.classList.add('revealed');
        io.unobserve(e.target);
      }
    }
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
  nodes.forEach(n => io.observe(n));
}

// ------------------------------ home: gallery lightbox (event-delegated) ------------------------------
function initGallery() {
  document.addEventListener('click', e => {
    const item = e.target.closest('.gallery-item[data-src]');
    if (item) openLightbox(item.dataset.src, item.dataset.alt || '');
  });
}
function openLightbox(src, alt) {
  $('#lightboxImg').src = src;
  $('#lightboxImg').alt = alt || '';
  $('#lightbox').classList.add('on');
}
function closeLightbox() { $('#lightbox').classList.remove('on'); }

// ------------------------------ quote form ------------------------------
async function submitLead() {
  const name = $('#lName').value.trim();
  const contact = $('#lContact').value.trim();
  const channel = $('#lChannel').value;
  const service_id = $('#lService').value;
  const message = $('#lMessage').value.trim();
  if (!name || !contact) { toast('Name and contact are required', 'err'); return; }

  const loginEmail = channel === 'email' ? contact : `${name.toLowerCase().replace(/[^a-z0-9]+/g, '.')}.${Math.random().toString(36).slice(2, 6)}@visitor.local`;
  const ok = await ensureVisitorSession(loginEmail);
  if (!ok) return;

  const payload = { name, contact, channel, message };
  if (service_id) payload.source_service_id = service_id;
  const r = await api('POST', '/api/leads', payload);
  if (!r.ok) { toast(r.data?.detail || 'Could not send request', 'err'); return; }
  $('#lName').value = ''; $('#lContact').value = ''; $('#lMessage').value = '';
  $('#leadFormCard').hidden = true;
  $('#leadThanks').hidden = false;
  $('#leadThanks').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function resetLeadForm() {
  $('#leadThanks').hidden = true;
  $('#leadFormCard').hidden = false;
  $('#lName').focus();
}

// ------------------------------ book ------------------------------
async function loadBook() {
  const bs = $('#bService');
  // Book form options are SSR-rendered when the template rendered; if the list
  // is empty or stale, refresh from the API.
  if (!bs.options.length || bs.options[0].value === '') {
    const r = await api('GET', '/api/services?scope=active&sort=name&limit=100');
    state.services = r.data?.data || [];
    bs.innerHTML = state.services.map(s => `<option value="${attr(s._id)}">${esc(s.name)}</option>`).join('')
      || '<option value="">(no services yet)</option>';
  } else {
    state.services = Array.from(bs.options).map(o => ({ _id: o.value, name: o.textContent }));
  }
}

async function submitBooking() {
  const service_id = $('#bService').value;
  const scheduled_at = $('#bWhen').value;
  const channel = $('#bChannel').value;
  const contact = $('#bContact').value.trim();
  const notes = $('#bNotes').value.trim();
  if (!service_id || !scheduled_at || !contact) { toast('Service, time, and contact are required', 'err'); return; }

  const loginEmail = channel === 'email' ? contact : `booking.${Math.random().toString(36).slice(2, 8)}@visitor.local`;
  const ok = await ensureVisitorSession(loginEmail);
  if (!ok) return;

  const r = await api('POST', '/api/appointments', { service_id, scheduled_at: new Date(scheduled_at).toISOString(), channel, contact, notes });
  if (!r.ok) { toast(r.data?.detail || 'Booking failed', 'err'); return; }
  $('#bWhen').value = ''; $('#bContact').value = ''; $('#bNotes').value = '';
  $('#bookFormCard').hidden = true;
  $('#bookThanks').hidden = false;
  $('#bookThanks').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function resetBookForm() {
  $('#bookThanks').hidden = true;
  $('#bookFormCard').hidden = false;
  $('#bService').focus();
}

// ------------------------------ admin ------------------------------
async function loadAdmin() {
  await Promise.all([
    loadAdminStats(),
    loadLeads(),
    loadAppts(),
    loadOutbox(),
    loadAdminServices(),
    loadContent('slides'),
    loadContent('about_sections'),
    loadContent('testimonials'),
    loadContent('gallery'),
    loadContent('faqs'),
    loadContent('locations'),
    loadAudit(),
    mdbOpsRefresh(),
  ]);
}

// ---------------- mdb-engine admin plane (/__mdb) ----------------------
// Tokens live in localStorage so they survive reloads. The Ops section
// renders itself from GET /__mdb/health/modules — any module the manifest
// enables automatically shows up here; no hard-coded endpoints for
// per-module routing.
function mdbAppToken() { return localStorage.getItem('mdb_app_token') || ''; }
function mdbSetAppToken(v) { localStorage.setItem('mdb_app_token', v || ''); }

const MDB_PREFIX = '/__mdb';
let _mdbModuleMap = null;

async function mdbOpsFetch(path, method = 'GET', body = null) {
  let token = mdbAppToken();
  if (!token) {
    token = prompt('Enter this app\'s mdb-engine X-App-Token to access admin ops:') || '';
    if (!token) return { ok: false, status: 401, data: { detail: 'missing token' } };
    mdbSetAppToken(token);
  }
  const slug = (state.session?.app_slug) || (window.__APP_SLUG__) || '';
  const url = MDB_PREFIX + path + (path.includes('?') ? '&' : '?') + 'slug=' + encodeURIComponent(slug);
  const headers = { 'X-App-Token': token, 'Accept': 'application/json' };
  const opts = { method, headers };
  if (body !== null) {
    headers['Content-Type'] = 'application/json';
    // Destructive admin POSTs carry an Idempotency-Key so an accidental
    // double-click turns into one server-side apply.
    headers['Idempotency-Key'] = 'spa-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    opts.body = JSON.stringify(body);
  } else if (method !== 'GET' && method !== 'HEAD') {
    headers['Idempotency-Key'] = 'spa-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
  const res = await fetch(url, opts);
  let data = null; try { data = await res.json(); } catch {}
  // Clear the cached token ONLY on auth failures (401/403). A 429 rate
  // limit or a transient 5xx must not silently log the user out.
  if (res.status === 401) mdbSetAppToken('');
  // Surface actionable errors centrally so callers don't all duplicate.
  if (res.status === 429) {
    const retry = res.headers.get('Retry-After') || data?.retry_after_seconds || '';
    toast(`Admin plane rate-limited${retry ? ' (retry in ' + retry + 's)' : ''}`, 'err');
  } else if (res.status === 403 && data?.detail?.includes?.('scope')) {
    toast('Permission denied: ' + data.detail, 'err');
  }
  return { ok: res.ok, status: res.status, data };
}

async function mdbLoadModules(force = false) {
  if (_mdbModuleMap && !force) {
    mdbRenderModuleBadges(_mdbModuleMap);
    return _mdbModuleMap;
  }
  const r = await mdbOpsFetch('/health/modules');
  if (!r.ok) return null;
  _mdbModuleMap = {};
  for (const m of (r.data?.modules || [])) _mdbModuleMap[m.name] = m;
  mdbRenderModuleBadges(_mdbModuleMap);
  return _mdbModuleMap;
}

function mdbHasModule(name) { return !!(_mdbModuleMap && _mdbModuleMap[name]); }

// Render a compact row of module+scope badges so operators see at a glance
// which modules this app exposes and which scopes each one enforces.
function mdbRenderModuleBadges(map) {
  const host = document.getElementById('mdbOpsModuleBadges');
  if (!host) return;
  const names = Object.keys(map || {}).sort();
  if (!names.length) { host.innerHTML = ''; return; }
  host.innerHTML = names.map(n => {
    const m = map[n];
    const scopes = new Set();
    for (const ep of (m.endpoints || [])) if (ep.scope) scopes.add(ep.scope);
    const scopeStr = Array.from(scopes).sort().join(' · ') || '*';
    return `<span class="badge" title="${esc(scopeStr)}"><strong>${esc(n)}</strong> <span class="sub">${esc(scopeStr)}</span></span>`;
  }).join(' ');
}

function mdbOpsSwitchTab(which) {
  document.querySelectorAll('#mdbOpsTabs button').forEach(b => b.classList.toggle('on', b.dataset.o === which));
  document.querySelectorAll('#mdbOpsSection .ops-panel').forEach(p => p.classList.remove('on'));
  const panel = document.getElementById({
    pending: 'mdbOpsPending', trash: 'mdbOpsTrash', history: 'mdbOpsHistory',
    audit: 'mdbOpsAudit', secrets: 'mdbOpsSecrets'
  }[which]);
  if (panel) panel.classList.add('on');
  if (which === 'audit') mdbAuditRefresh();
  if (which === 'secrets') mdbSecretsRefresh();
}

async function mdbSecretsRefresh() {
  const box = document.getElementById('mdbSecretsCurrent');
  if (!box) return;
  if (!mdbHasModule('secrets')) {
    box.textContent = 'secrets module is not enabled for this app.';
    return;
  }
  const r = await mdbOpsFetch('/secrets/current');
  if (!r.ok) {
    box.textContent = `Error ${r.status}: ${JSON.stringify(r.data, null, 2)}`;
    return;
  }
  box.textContent = JSON.stringify(r.data, null, 2);
}

document.addEventListener('click', (e) => {
  const b = e.target.closest?.('#mdbOpsTabs button[data-o]');
  if (b) mdbOpsSwitchTab(b.dataset.o);
});

let _mdbAuditFollowTimer = null;

async function mdbAuditRefresh() {
  const host = document.getElementById('mdbAuditList');
  if (!host) return;
  if (!mdbHasModule('audit')) {
    host.innerHTML = '<div class="sub">audit module is not enabled for this app.</div>';
    return;
  }
  const mod = (document.getElementById('mdbAuditModule')?.value || '').trim();
  const q = mod ? `/audit/recent?limit=50&module=${encodeURIComponent(mod)}` : '/audit/recent?limit=50';
  const r = await mdbOpsFetch(q);
  if (!r.ok) {
    host.innerHTML = `<div class="sub">Error ${r.status}: ${esc(JSON.stringify(r.data))}</div>`;
    return;
  }
  const rows = r.data?.entries || [];
  if (!rows.length) { host.innerHTML = '<div class="sub">No audit entries yet.</div>'; return; }
  host.innerHTML = rows.map(row => {
    const when = fmt(row.ts);
    const status = Number(row.status || 0);
    const cls = status >= 500 ? 'err' : status >= 400 ? 'warn' : 'ok';
    const who = row.principal_label
      ? `${esc(row.principal_label)}<span class="sub"> (${esc((row.principal_token_id || '').slice(0, 8))})</span>`
      : (row.principal_token_id ? `<span class="sub">${esc(row.principal_token_id.slice(0, 8))}</span>` : '<span class="sub">anon</span>');
    return `<div class="row">
      <div class="row-main">
        <span class="pill pill-${cls}">${status || '—'}</span>
        <strong>${esc(row.method || '')}</strong> <code>${esc(row.path || '')}</code>
        <span class="sub">${esc(row.module || '')}:${esc(row.endpoint || '')}</span>
      </div>
      <div class="row-sub">${who} &middot; ${when} &middot; ${Number(row.duration_ms || 0).toFixed(1)}ms</div>
    </div>`;
  }).join('');
}

function mdbAuditToggleFollow() {
  const btn = document.getElementById('mdbAuditFollowBtn');
  if (_mdbAuditFollowTimer) {
    clearInterval(_mdbAuditFollowTimer);
    _mdbAuditFollowTimer = null;
    if (btn) { btn.textContent = 'Follow'; btn.classList.remove('on'); }
  } else {
    _mdbAuditFollowTimer = setInterval(mdbAuditRefresh, 4000);
    if (btn) { btn.textContent = 'Following…'; btn.classList.add('on'); }
    mdbAuditRefresh();
  }
}

async function mdbRotateToken() {
  if (!mdbHasModule('secrets')) { toast('secrets module is not enabled', 'err'); return; }
  const label = (document.getElementById('mdbRotateLabel')?.value || '').trim() || null;
  const scopesRaw = (document.getElementById('mdbRotateScopes')?.value || '').trim();
  const scopes = scopesRaw ? scopesRaw.split(',').map(s => s.trim()).filter(Boolean) : null;
  if (!confirm('Rotate the admin token now? The current token will stop working immediately.')) return;
  const body = {};
  if (label) body.label = label;
  if (scopes) body.scopes = scopes;
  const r = await mdbOpsFetch('/secrets/rotate', 'POST', body);
  const out = document.getElementById('mdbRotateOut');
  if (!r.ok) {
    if (out) out.textContent = `Error ${r.status}: ${JSON.stringify(r.data, null, 2)}`;
    return;
  }
  // Adopt the new token immediately so subsequent admin calls in this
  // session keep working. The browser is the only place this value ever
  // lives in cleartext after rotation.
  if (r.data?.token) mdbSetAppToken(r.data.token);
  if (out) {
    const shown = Object.assign({}, r.data);
    out.textContent = JSON.stringify(shown, null, 2);
  }
  toast('Token rotated — copy it now, it will not be shown again.', 'ok');
}

async function mdbOpsRefresh() {
  const pendingBox = document.getElementById('mdbOpsPendingOut');
  const historyBox = document.getElementById('mdbOpsHistoryOut');
  const trashBox = document.getElementById('mdbOpsTrashList');
  if (!pendingBox) return;

  pendingBox.textContent = 'Loading…';
  historyBox.textContent = 'Loading…';
  if (trashBox) trashBox.textContent = 'Loading…';

  await mdbLoadModules();

  const wants = [];
  if (mdbHasModule('reconciler')) {
    wants.push(['pending', mdbOpsFetch('/reconciler/plan')]);
    wants.push(['history', mdbOpsFetch('/reconciler/manifest/history?limit=10')]);
  } else {
    pendingBox.textContent = 'reconciler module is disabled for this app.';
    historyBox.textContent = 'reconciler module is disabled for this app.';
  }
  if (mdbHasModule('trash')) {
    wants.push(['trash', mdbOpsFetch('/trash')]);
  } else if (trashBox) {
    trashBox.textContent = 'trash module is disabled for this app.';
  }

  const results = await Promise.all(wants.map(([, p]) => p));
  for (let i = 0; i < wants.length; i++) {
    const tag = wants[i][0]; const r = results[i];
    if (tag === 'pending') {
      pendingBox.textContent = r.ok ? JSON.stringify(r.data, null, 2) : `Error ${r.status}: ${JSON.stringify(r.data)}`;
    } else if (tag === 'history') {
      historyBox.textContent = r.ok ? JSON.stringify(r.data, null, 2) : `Error ${r.status}: ${JSON.stringify(r.data)}`;
    } else if (tag === 'trash' && trashBox) {
      if (!r.ok) {
        trashBox.textContent = `Error ${r.status}: ${JSON.stringify(r.data)}`;
      } else if (!(r.data || []).length) {
        trashBox.textContent = 'Trash is empty.';
      } else {
        trashBox.innerHTML = (r.data || []).map(row => {
          const id = esc(row._id);
          const kind = esc(row.kind);
          const orig = esc(row.original_name);
          const docs = esc(row.doc_count);
          const expires = fmt(row.expires_at);
          return `<div class="row">
            <div class="row-main"><strong>${kind}</strong> &mdash; ${orig}</div>
            <div class="row-sub">id=${id} docs=${docs} expires=${expires}</div>
            <div class="row-actions">
              <button class="btn btn-o btn-sm" onclick="mdbOpsTrashAction('${id}','restore',true)">Dry restore</button>
              <button class="btn btn-p btn-sm" onclick="mdbOpsTrashAction('${id}','restore',false)">Restore</button>
              <button class="btn btn-o btn-sm" onclick="mdbOpsTrashAction('${id}','purge',false)">Purge</button>
            </div>
          </div>`;
        }).join('');
      }
    }
  }
}

async function mdbOpsApply(dry) {
  if (!mdbHasModule('reconciler')) { toast('reconciler module is disabled', 'err'); return; }
  const confirmed = dry || confirm('Apply the current plan now?');
  if (!confirmed) return;
  const q = dry ? '/reconciler/apply?dry_run=true' : '/reconciler/apply?yes=true';
  const r = await mdbOpsFetch(q, 'POST');
  const box = document.getElementById('mdbOpsPendingOut');
  if (box) box.textContent = r.ok ? JSON.stringify(r.data, null, 2) : `Error ${r.status}: ${JSON.stringify(r.data)}`;
  if (r.ok && !dry) toast('Reconcile applied: ' + (r.data?.status || 'ok'));
  mdbOpsRefresh();
}

async function mdbOpsTrashAction(id, action, dry) {
  let path, method;
  if (action === 'restore') { path = `/trash/${id}/restore${dry ? '?dry_run=true' : ''}`; method = 'POST'; }
  else if (action === 'purge') { if (!confirm('Hard-drop this quarantined item?')) return; path = `/trash/${id}/purge`; method = 'POST'; }
  else return;
  const r = await mdbOpsFetch(path, method);
  toast(r.ok ? `${action}: ${r.data?.restored || r.data?.purged || 'ok'}` : `Error: ${r.data?.detail || r.status}`, r.ok ? 'ok' : 'err');
  mdbOpsRefresh();
}

async function loadAdminStats() {
  const [leads, appts, outboxPending] = await Promise.all([
    api('GET', '/api/leads/_count?scope=new'),
    api('GET', '/api/appointments/_count?scope=upcoming'),
    api('GET', '/api/outbox/_count?scope=pending'),
  ]);
  $('#adminStats').innerHTML = `
    <div class="stat"><div class="n">${leads.data?.count ?? 0}</div><div class="l">New leads</div></div>
    <div class="stat"><div class="n">${appts.data?.count ?? 0}</div><div class="l">Upcoming</div></div>
    <div class="stat"><div class="n">${outboxPending.data?.count ?? 0}</div><div class="l">Outbox pending</div></div>
    <div class="stat"><div class="n">${state.services.length}</div><div class="l">Services</div></div>
  `;
}

$$('#leadTabs button').forEach(b => b.onclick = () => {
  $$('#leadTabs button').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  state.leadFilter = b.dataset.s;
  loadLeads();
});

async function loadLeads() {
  const scope = state.leadFilter ? `?scope=${state.leadFilter}&sort=-created_at&limit=50` : '?sort=-created_at&limit=50';
  const r = await api('GET', '/api/leads' + scope);
  const leads = r.data?.data || [];
  $('#leadsCount').textContent = leads.length ? `(${leads.length})` : '';
  if (!leads.length) { $('#leadsList').innerHTML = '<div class="empty">No leads in this view yet.</div>'; return; }
  $('#leadsList').innerHTML = leads.map(l => `
    <div class="row">
      <div class="row-info">
        <div class="row-title">${esc(l.name || 'Unknown')} <span class="badge badge-${esc(l.status || 'new')}">${esc(l.status || 'new')}</span></div>
        <div class="row-sub"><span class="channel">${esc(l.channel || '?')}</span>${esc(l.contact || '')} \u00b7 ${fmt(l.created_at)}${l.message ? ' \u00b7 ' + esc(l.message).slice(0, 90) : ''}</div>
      </div>
      <div class="row-actions">
        ${l.status !== 'contacted' ? `<button class="btn btn-o btn-sm" onclick="setLeadStatus('${l._id}','contacted')">Contacted</button>` : ''}
        ${l.status !== 'won' ? `<button class="btn btn-s btn-sm" onclick="setLeadStatus('${l._id}','won')">Won</button>` : ''}
        ${l.status !== 'lost' ? `<button class="btn btn-o btn-sm" onclick="setLeadStatus('${l._id}','lost')">Lost</button>` : ''}
      </div>
    </div>`).join('');
}

async function setLeadStatus(id, status) {
  const r = await api('PATCH', `/api/leads/${id}`, { status });
  if (!r.ok) { toast(r.data?.detail || 'Update failed', 'err'); return; }
  toast('Updated');
  loadLeads(); loadAudit(); loadAdminStats();
}

$$('#apptTabs button').forEach(b => b.onclick = () => {
  $$('#apptTabs button').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  state.apptFilter = b.dataset.s;
  loadAppts();
});

async function loadAppts() {
  const scope = state.apptFilter ? `?scope=${state.apptFilter}&sort=scheduled_at&limit=50` : '?sort=scheduled_at&limit=50';
  const r = await api('GET', '/api/appointments' + scope);
  const appts = r.data?.data || [];
  if (!appts.length) { $('#apptsList').innerHTML = '<div class="empty">No appointments yet.</div>'; return; }
  const serviceMap = Object.fromEntries(state.services.map(s => [s._id, s.name]));
  $('#apptsList').innerHTML = appts.map(a => `
    <div class="row">
      <div class="row-info">
        <div class="row-title">${esc(serviceMap[a.service_id] || 'Service')} <span class="badge badge-${esc(a.status || 'requested')}">${esc(a.status || 'requested')}</span></div>
        <div class="row-sub"><span class="channel">${esc(a.channel || '?')}</span>${esc(a.contact || '')} \u00b7 ${fmt(a.scheduled_at)}${a.notes ? ' \u00b7 ' + esc(a.notes).slice(0, 80) : ''}</div>
      </div>
      <div class="row-actions">
        ${a.status === 'requested' ? `<button class="btn btn-s btn-sm" onclick="setApptStatus('${a._id}','confirmed')">Confirm</button>` : ''}
        ${a.status !== 'done' && a.status !== 'cancelled' ? `<button class="btn btn-o btn-sm" onclick="setApptStatus('${a._id}','done')">Done</button>` : ''}
        ${a.status !== 'cancelled' && a.status !== 'done' ? `<button class="btn btn-d btn-sm" onclick="setApptStatus('${a._id}','cancelled')">Cancel</button>` : ''}
      </div>
    </div>`).join('');
}

async function setApptStatus(id, status) {
  const r = await api('PATCH', `/api/appointments/${id}`, { status });
  if (!r.ok) { toast(r.data?.detail || 'Update failed', 'err'); return; }
  toast('Updated');
  loadAppts(); loadAudit(); loadAdminStats();
}

async function loadOutbox() {
  const r = await api('GET', '/api/outbox?sort=-created_at&limit=40');
  const items = r.data?.data || [];
  if (!items.length) { $('#outboxList').innerHTML = '<div class="empty">Outbox empty. Submit a lead on the home page to see the pipeline run.</div>'; return; }
  $('#outboxList').innerHTML = items.map(o => `
    <div class="row">
      <div class="row-info">
        <div class="row-title">${esc(o.template || '?')} <span class="badge badge-${esc(o.status || 'pending')}">${esc(o.status || 'pending')}</span></div>
        <div class="row-sub"><span class="channel">${esc(o.channel || '?')}</span>to <code>${esc(o.to || '')}</code> \u00b7 attempts ${o.attempts ?? 0} \u00b7 created ${fmt(o.created_at)}${o.delivered_at ? ' \u00b7 dispatched ' + fmt(o.delivered_at) : ''}</div>
      </div>
    </div>`).join('');
}

async function loadAdminServices() {
  const r = await api('GET', '/api/services?sort=name&limit=100');
  state.services = r.data?.data || [];
  if (!state.services.length) { $('#servicesList').innerHTML = '<div class="empty">No services yet.</div>'; return; }
  $('#servicesList').innerHTML = state.services.map(s => `
    <div class="row">
      <div class="row-info">
        <div class="row-title">${esc(s.name)} ${s.active === false ? '<span class="badge badge-lost">inactive</span>' : ''}</div>
        <div class="row-sub">${fmtMoney(s.price, s.currency)}${s.duration_minutes ? ' \u00b7 ' + s.duration_minutes + ' min' : ''}${s.description ? ' \u00b7 ' + esc(s.description).slice(0, 80) : ''}</div>
      </div>
      <div class="row-actions">
        <button class="btn btn-o btn-sm" onclick="toggleService('${s._id}', ${s.active === false})">${s.active === false ? 'Activate' : 'Deactivate'}</button>
        <button class="btn btn-d btn-sm" onclick="deleteService('${s._id}')">Delete</button>
      </div>
    </div>`).join('');
}

async function addService() {
  const name = $('#sName').value.trim();
  if (!name) { toast('Name is required', 'err'); return; }
  const payload = { name };
  const p = $('#sPrice').value.trim(); if (p) payload.price = Number(p);
  const c = $('#sCurrency').value.trim(); if (c) payload.currency = c;
  const d = $('#sDuration').value.trim(); if (d) payload.duration_minutes = Number(d);
  const desc = $('#sDesc').value.trim(); if (desc) payload.description = desc;
  const r = await api('POST', '/api/services', payload);
  if (!r.ok) { toast(r.data?.detail || 'Could not add service', 'err'); return; }
  $('#sName').value = ''; $('#sPrice').value = ''; $('#sDuration').value = ''; $('#sDesc').value = '';
  toast('Service added');
  loadAdminServices(); loadAdminStats();
}

async function toggleService(id, activate) {
  const r = await api('PATCH', `/api/services/${id}`, { active: activate });
  if (!r.ok) { toast(r.data?.detail || 'Update failed', 'err'); return; }
  loadAdminServices();
}

async function deleteService(id) {
  if (!confirm('Delete this service?')) return;
  const r = await api('DELETE', `/api/services/${id}`);
  if (!r.ok) { toast(r.data?.detail || 'Delete failed', 'err'); return; }
  toast('Deleted');
  loadAdminServices(); loadAdminStats();
}

// ------------------------------ admin: content (slides/about/testimonials/gallery/faqs) ------------------------------
$$('#contentTabs button').forEach(b => b.onclick = () => {
  $$('#contentTabs button').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  $$('.content-panel').forEach(p => p.classList.remove('on'));
  $('#panel-' + b.dataset.c).classList.add('on');
});

async function loadContent(col) {
  const r = await api('GET', `/api/${col}?sort=order&limit=50`);
  const items = r.data?.data || [];
  const list = $('#' + col + 'List');
  if (!items.length) { list.innerHTML = '<div class="empty">Nothing yet.</div>'; return; }
  list.innerHTML = items.map(it => renderContentRow(col, it)).join('');
}

function renderContentRow(col, it) {
  const thumb = it.image_url || it.avatar_url;
  const title = it.title || it.heading || it.author || it.question || it.name || '(untitled)';
  let sub = '';
  if (col === 'slides')              sub = `${esc(it.eyebrow || '')} \u00b7 order ${it.order ?? 0}${it.active === false ? ' \u00b7 inactive' : ''}`;
  else if (col === 'about_sections') sub = `icon: ${esc(it.icon || '\u2014')} \u00b7 ${esc((it.body || '').slice(0, 80))}`;
  else if (col === 'testimonials')   sub = `${esc(it.role || '')} \u00b7 ${stars(it.rating)} \u00b7 ${esc((it.quote || '').slice(0, 80))}`;
  else if (col === 'gallery')        sub = `${esc(it.category || '')} \u00b7 order ${it.order ?? 0}`;
  else if (col === 'faqs')           sub = esc((it.answer || '').slice(0, 100));
  else if (col === 'locations')      sub = `${esc([it.city, it.region].filter(Boolean).join(', ') || it.address_line1 || '')} \u00b7 ${Number(it.lat).toFixed(3)}, ${Number(it.lng).toFixed(3)}${it.is_primary ? ' \u00b7 primary' : ''}`;
  return `
    <div class="row">
      ${thumb ? `<img class="thumb" src="${attr(thumb)}" alt="" loading="lazy">` : ''}
      <div class="row-info">
        <div class="row-title">${esc(title)}</div>
        <div class="row-sub">${sub}</div>
      </div>
      <div class="row-actions">
        <button class="btn btn-d btn-sm" onclick="deleteContent('${col}','${it._id}')">Delete</button>
      </div>
    </div>`;
}

async function addContent(col) {
  const payloads = {
    slides: () => ({
      title: $('#slTitle').value.trim(),
      eyebrow: $('#slEyebrow').value.trim() || undefined,
      subtitle: $('#slSubtitle').value.trim() || undefined,
      image_url: $('#slImage').value.trim(),
      cta_label: $('#slCtaLabel').value.trim() || undefined,
      cta_href: $('#slCtaHref').value.trim() || undefined,
      order: Number($('#slOrder').value || 10),
      active: $('#slActive').value === 'true',
    }),
    about_sections: () => ({
      heading: $('#abHeading').value.trim(),
      icon: $('#abIcon').value,
      body: $('#abBody').value.trim(),
      image_url: $('#abImage').value.trim() || undefined,
      order: Number($('#abOrder').value || 10),
      flip: $('#abFlip').value === 'true',
    }),
    testimonials: () => ({
      author: $('#tmAuthor').value.trim(),
      role: $('#tmRole').value.trim() || undefined,
      quote: $('#tmQuote').value.trim(),
      avatar_url: $('#tmAvatar').value.trim() || undefined,
      rating: Number($('#tmRating').value || 5),
      order: Number($('#tmOrder').value || 10),
    }),
    gallery: () => ({
      title: $('#gaTitle').value.trim() || undefined,
      category: $('#gaCat').value.trim() || undefined,
      image_url: $('#gaImage').value.trim(),
      alt: $('#gaAlt').value.trim() || undefined,
      order: Number($('#gaOrder').value || 10),
    }),
    faqs: () => ({
      question: $('#fqQ').value.trim(),
      answer: $('#fqA').value.trim(),
      category: $('#fqCat').value.trim() || undefined,
      order: Number($('#fqOrder').value || 10),
    }),
    locations: () => ({
      name: $('#loName').value.trim(),
      address_line1: $('#loLine1').value.trim() || undefined,
      address_line2: $('#loLine2').value.trim() || undefined,
      city: $('#loCity').value.trim() || undefined,
      region: $('#loRegion').value.trim() || undefined,
      postcode: $('#loPost').value.trim() || undefined,
      country: $('#loCountry').value.trim() || undefined,
      lat: Number($('#loLat').value),
      lng: Number($('#loLng').value),
      phone: $('#loPhone').value.trim() || undefined,
      email: $('#loEmail').value.trim() || undefined,
      directions_url: $('#loDir').value.trim() || undefined,
      is_primary: $('#loPrimary').value === 'true',
      order: Number($('#loOrder').value || 10),
    }),
  };
  const payload = payloads[col]();
  Object.keys(payload).forEach(k => (payload[k] === undefined || Number.isNaN(payload[k])) && delete payload[k]);

  const required = {
    slides: ['title', 'image_url'],
    about_sections: ['heading', 'body'],
    testimonials: ['author', 'quote'],
    gallery: ['image_url'],
    faqs: ['question', 'answer'],
    locations: ['name', 'lat', 'lng'],
  }[col];
  for (const f of required) {
    if (!payload[f]) { toast(`Missing ${f}`, 'err'); return; }
  }

  const r = await api('POST', `/api/${col}`, payload);
  if (!r.ok) { toast(r.data?.detail || 'Could not save', 'err'); return; }
  toast('Added');
  loadContent(col);

  const resetMap = {
    slides: ['slTitle', 'slEyebrow', 'slSubtitle', 'slImage', 'slCtaLabel', 'slCtaHref'],
    about_sections: ['abHeading', 'abBody', 'abImage'],
    testimonials: ['tmAuthor', 'tmRole', 'tmQuote', 'tmAvatar'],
    gallery: ['gaTitle', 'gaCat', 'gaImage', 'gaAlt'],
    faqs: ['fqQ', 'fqA', 'fqCat'],
    locations: ['loName', 'loLine1', 'loLine2', 'loCity', 'loRegion', 'loPost', 'loLat', 'loLng', 'loPhone', 'loEmail', 'loDir'],
  };
  (resetMap[col] || []).forEach(id => { const el = $('#' + id); if (el) el.value = ''; });
}

async function deleteContent(col, id) {
  if (!confirm('Delete this item?')) return;
  const r = await api('DELETE', `/api/${col}/${id}`);
  if (!r.ok) { toast(r.data?.detail || 'Delete failed', 'err'); return; }
  toast('Deleted');
  loadContent(col);
}

async function loadAudit() {
  const r = await api('GET', '/api/audit_log?sort=-timestamp&limit=30');
  const events = r.data?.data || [];
  if (!events.length) { $('#auditList').innerHTML = '<div class="empty">No events yet.</div>'; return; }
  $('#auditList').innerHTML = events.map(e => `
    <div class="row">
      <div class="row-info">
        <div class="row-title">${esc(e.event || '?')}</div>
        <div class="row-sub">${esc(e.entity || '')} ${esc(String(e.entity_id || '')).slice(0, 10)}\u2026 \u00b7 by ${esc(e.actor || 'system')} \u00b7 ${fmt(e.timestamp)}</div>
      </div>
    </div>`).join('');
}

// ------------------------------ boot ------------------------------
(async () => {
  const copyYearEl = $('#copyYear');
  if (copyYearEl) copyYearEl.textContent = new Date().getFullYear();
  await refreshSession();
  initSlideshow();
  initGallery();
  initReveal();
  initScrollspy();
  // Honour hash deep-links — SPA views (#book / #admin) or section anchors (#services, #find-us, …).
  const hash = (location.hash || '').replace(/^#/, '');
  if (['home', 'book', 'admin'].includes(hash)) {
    go(hash);
  } else if (hash && (document.getElementById(hash) || NAV_SECTIONS.includes(hash))) {
    setTimeout(() => scrollToSection(hash), 120);
  }
})();
