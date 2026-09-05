/* =============================================================================
   SoundGrab — logique d'interface.

   Aucune dependance, aucune etape de build : le fichier est servi tel quel.
   L'etat vient entierement du serveur via le flux SSE /api/events ; le client
   ne conserve que ce qui est purement visuel (theme, cartes deja construites).
   ============================================================================= */
'use strict';

const $ = (sel) => document.querySelector(sel);

const els = {
  input: $('#url-input'),
  submit: $('#submit'),
  jobs: $('#jobs'),
  empty: $('#empty'),
  status: $('#status'),
  queueCount: $('#queue-count'),
  clear: $('#clear'),
  settings: $('#settings'),
  settingsWrap: $('#settings-wrap'),
  settingsToggle: $('#settings-toggle'),
  themeToggle: $('#theme-toggle'),
  toasts: $('#toasts'),
  srStatus: $('#sr-status'),
  template: $('#job-template'),
};

const STATUS_LABEL = {
  queued: 'en attente',
  resolving: 'analyse',
  downloading: 'en cours',
  done: 'terminé',
  error: 'erreur',
  cancelled: 'annulé',
};

const STAGE_LABEL = {
  analyse: 'Analyse',
  'téléchargement': 'Téléchargement',
  conversion: 'Conversion MP3',
  pochette: 'Pochette',
};

const KIND_ICON = { morceau: '#i-track', playlist: '#i-playlist', profil: '#i-profil' };
const ACTIVE = ['queued', 'resolving', 'downloading'];

/* --------------------------------------------------------------- requetes */

async function api(path, options) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch (_) { /* corps vide ou non JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* ------------------------------------------------------------------ format */

const tracks = (n) => `${n} morceau${n > 1 ? 'x' : ''}`;

/* N'ecrit que si la valeur change : evite d'interrompre une selection de texte
   en cours, le flux SSE repassant ici plusieurs fois par seconde. */
function setText(el, value) {
  if (el.textContent !== value) el.textContent = value;
}

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}`;
}

/* ------------------------------------------------------------- messages */

function toast(message, kind = 'ok') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  const text = document.createElement('span');
  text.textContent = message;
  el.append(text);
  els.toasts.append(el);

  // On retire apres l'animation de sortie, sinon la pile garde des noeuds morts.
  setTimeout(() => {
    el.classList.add('leaving');
    el.addEventListener('animationend', () => el.remove(), { once: true });
  }, kind === 'err' ? 7000 : 4000);
}

const fail = (err) => toast(err.message || String(err), 'err');

/* ------------------------------------------------------------------ theme */

const THEMES = ['auto', 'light', 'dark'];
const THEME_ICON = { auto: '#i-auto', light: '#i-sun', dark: '#i-moon' };
const THEME_TITLE = { auto: 'Thème : système', light: 'Thème : clair', dark: 'Thème : sombre' };

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('#theme-icon').setAttribute('href', THEME_ICON[theme]);
  els.themeToggle.title = THEME_TITLE[theme];
  els.themeToggle.setAttribute('aria-label', THEME_TITLE[theme]);
  try {
    localStorage.setItem('soundgrab.theme', theme);
  } catch (_) { /* mode prive : le theme ne survivra pas au rechargement */ }
}

els.themeToggle.addEventListener('click', () => {
  const current = document.documentElement.dataset.theme || 'auto';
  applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
});

/* ------------------------------------------------------------------ sante */

let health = null;
let connected = true;

function chip(text, cls) {
  const el = document.createElement('span');
  el.className = cls ? `chip ${cls}` : 'chip';
  el.textContent = text;
  return el;
}

function renderStatus() {
  const chips = [];
  if (!connected) chips.push(chip('reconnexion…', 'live'));
  if (health) {
    chips.push(chip(health.ffmpeg ? 'ffmpeg' : 'ffmpeg manquant', health.ffmpeg ? 'ok' : 'bad'));
    chips.push(chip(`yt-dlp ${health.ytdlp_version}`));
    if (health.free_gb !== null) chips.push(chip(`${health.free_gb} Go libres`));
  }
  els.status.replaceChildren(...chips);
}

async function loadHealth() {
  health = await api('/api/health');
  renderStatus();

  $('#ffmpeg-warning').hidden = Boolean(health.ffmpeg);
  $('#version').textContent = health.version ? `v${health.version}` : '';
  $('#foot-env').textContent = `Python ${health.python} · yt-dlp ${health.ytdlp_version}`;
  $('#foot-dir').textContent = health.output_exists
    ? health.output_dir
    : `${health.output_dir} (sera créé)`;
  $('#foot-dir').title = health.output_dir;
}

/* --------------------------------------------------------------- reglages */

let defaults = {};

/* Le débit ne s'applique qu'au réencodage : le laisser actif en « qualité
   d'origine » laisserait croire à un effet qu'il n'a pas. La valeur reste
   envoyée au serveur, un champ désactivé conservant la sienne. */
function syncQualityState() {
  const encoding = $('#cfg-format').value === 'mp3';
  const quality = $('#cfg-quality');
  quality.disabled = !encoding;
  quality.closest('.field').classList.toggle('is-off', !encoding);
}

function fillForm(values) {
  document.querySelectorAll('[data-cfg]').forEach((el) => {
    const value = values[el.dataset.cfg];
    if (value === undefined) return;
    if (el.type === 'checkbox') el.checked = Boolean(value);
    else el.value = value;
  });
  syncQualityState();
}

async function loadSettings() {
  const cfg = await api('/api/config');
  defaults = cfg.defaults;
  fillForm(cfg.values);
}

async function saveSettings() {
  const values = {};
  document.querySelectorAll('[data-cfg]').forEach((el) => {
    values[el.dataset.cfg] = el.type === 'checkbox' ? el.checked : el.value;
  });
  try {
    const saved = await api('/api/config', { method: 'POST', body: JSON.stringify({ values }) });
    fillForm(saved.values);  // le serveur borne certaines valeurs, on reflete son verdict
    toast('Réglages enregistrés.');
    loadHealth();
  } catch (err) {
    fail(err);
  }
}

function toggleSettings(force) {
  const open = force ?? !els.settingsWrap.classList.contains('open');
  els.settingsWrap.classList.toggle('open', open);
  els.settingsToggle.setAttribute('aria-expanded', String(open));
  // `inert` sort le panneau replie du parcours clavier et des lecteurs d'ecran.
  els.settingsWrap.inert = !open;
  if (open) els.settings.querySelector('input, select').focus({ preventScroll: true });
}

/* -------------------------------------------------------------- rendu job */

const cards = new Map();      // id -> element
const lastStatus = new Map(); // id -> statut, pour n'annoncer que les transitions

function buildCard(job) {
  const card = els.template.content.firstElementChild.cloneNode(true);
  card.querySelector('.job-cancel').addEventListener('click', () => {
    api(`/api/jobs/${job.id}/cancel`, { method: 'POST' }).catch(fail);
  });
  return card;
}

/* Ligne de gauche : ce que le job est en train de faire. */
function currentLine(job) {
  switch (job.status) {
    case 'queued':
      return 'En attente d\'un emplacement libre';
    case 'resolving':
      return 'Analyse de la source…';
    case 'downloading': {
      const stage = STAGE_LABEL[job.stage] || 'Téléchargement';
      return job.current ? `${stage} · ${job.current}` : `${stage}…`;
    }
    case 'error':
      return job.error || 'Échec';
    case 'cancelled':
      return 'Annulé';
    case 'done':
    default: {
      const parts = [];
      if (job.completed) parts.push(`${tracks(job.completed)} récupéré${job.completed > 1 ? 's' : ''}`);
      if (job.skipped) parts.push(`${job.skipped} déjà présent${job.skipped > 1 ? 's' : ''}`);
      if (job.failed) parts.push(`${job.failed} en échec`);
      return parts.length ? parts.join(' · ') : 'Rien de nouveau à récupérer';
    }
  }
}

/* Ligne de droite : les chiffres, en chasse fixe pour ne pas sautiller. */
function statsLine(job) {
  if (job.status === 'downloading') {
    const parts = [];
    if (job.total > 1) parts.push(`${job.completed}/${job.total}`);
    if (job.speed) parts.push(job.speed);
    if (job.eta) parts.push(job.eta);
    return parts.join(' · ');
  }
  if (job.finished) return `en ${formatDuration(job.finished - job.created)}`;
  return '';
}

function updateCard(card, job) {
  const isActive = ACTIVE.includes(job.status);
  const indeterminate = job.status === 'queued' || job.status === 'resolving';

  card.className = `job ${job.status}`;
  card.querySelector('.job-kind use')
    .setAttribute('href', KIND_ICON[job.kind] || '#i-track');

  const title = card.querySelector('.job-title');
  setText(title, job.title || job.url);
  title.title = job.url;

  setText(card.querySelector('.job-sub'),
    [job.uploader, job.total > 1 ? tracks(job.total) : ''].filter(Boolean).join(' · '));

  const state = card.querySelector('.state');
  setText(state, STATUS_LABEL[job.status] || job.status);
  state.className = `state ${job.status}`;

  card.querySelector('.job-cancel').hidden = !isActive;

  // Un job qui a echoue avant le premier octet n'a pas de progression a montrer :
  // une barre vide a 0 % n'apprendrait rien.
  card.querySelector('.progress-row').hidden = !isActive && !job.percent;

  const bar = card.querySelector('.progress');
  const fill = card.querySelector('.progress-fill');
  bar.classList.toggle('indeterminate', indeterminate);
  if (indeterminate) {
    fill.style.width = '';   // laisse la regle CSS piloter la barre
    bar.removeAttribute('aria-valuenow');
    bar.setAttribute('aria-valuetext', 'progression inconnue');
  } else {
    fill.style.width = `${job.percent || 0}%`;
    bar.setAttribute('aria-valuenow', String(Math.round(job.percent || 0)));
    bar.removeAttribute('aria-valuetext');
  }
  setText(card.querySelector('.progress-value'),
    indeterminate ? '' : `${Math.round(job.percent || 0)} %`);

  setText(card.querySelector('.job-current'), currentLine(job));
  setText(card.querySelector('.job-stats'), statsLine(job));

  // Le message final d'erreur est deja affiche a gauche : on ne le repete pas.
  const errors = job.errors || [];
  const box = card.querySelector('.job-errors');
  box.hidden = errors.length === 0;
  if (errors.length) {
    const s = errors.length > 1 ? 's' : '';
    setText(box.querySelector('summary'), `${errors.length} problème${s} rencontré${s}`);
    const list = box.querySelector('ul');
    if (list.childElementCount !== Math.min(errors.length, 30)) {
      list.replaceChildren(...errors.slice(0, 30).map((text) => {
        const li = document.createElement('li');
        li.textContent = text;
        return li;
      }));
    }
  }
}

/* Annonce vocale des seuls changements d'etat : annoncer chaque progression
   noierait un lecteur d'ecran sous des centaines de messages. */
function announce(job) {
  const before = lastStatus.get(job.id);
  if (before === job.status) return;
  lastStatus.set(job.id, job.status);
  if (before === undefined || ACTIVE.includes(job.status)) return;

  const name = job.title || job.url;
  if (job.status === 'done') {
    els.srStatus.textContent = `${name} : terminé, ${job.completed} morceaux.`;
  } else if (job.status === 'error') {
    els.srStatus.textContent = `${name} : échec.`;
  } else {
    els.srStatus.textContent = `${name} : annulé.`;
  }
}

function render(snapshot) {
  const seen = new Set();

  for (const job of snapshot.jobs) {
    seen.add(job.id);
    let card = cards.get(job.id);
    if (!card) {
      card = buildCard(job);
      cards.set(job.id, card);
    }
    updateCard(card, job);
    announce(job);
  }

  for (const [id, card] of cards) {
    if (!seen.has(id)) {
      card.remove();
      cards.delete(id);
      lastStatus.delete(id);
    }
  }

  // Les jobs arrivent deja tries du plus recent au plus ancien. On ne touche au
  // DOM que si l'ordre a change : reinserer les memes noeuds a chaque trame
  // relancerait les animations et ferait perdre le focus clavier en cours.
  const ordered = snapshot.jobs.map((j) => cards.get(j.id));
  const unchanged = ordered.length === els.jobs.childElementCount
    && ordered.every((node, i) => els.jobs.children[i] === node);
  if (!unchanged) els.jobs.replaceChildren(...ordered);

  const finished = snapshot.jobs.length - snapshot.active;
  els.empty.hidden = snapshot.jobs.length > 0;
  els.clear.hidden = finished === 0;
  els.queueCount.hidden = snapshot.jobs.length === 0;
  setText(els.queueCount, snapshot.active
    ? `${snapshot.active} en cours`
    : `${finished} terminé${finished > 1 ? 's' : ''}`);

  updateTitle(snapshot);
}

/* Progression visible depuis un autre onglet, sans avoir a revenir sur la page. */
function updateTitle(snapshot) {
  if (!snapshot.active) {
    document.title = 'SoundGrab';
    return;
  }
  const running = snapshot.jobs.filter((j) => ACTIVE.includes(j.status));
  document.title = running.length === 1
    ? `${Math.round(running[0].percent || 0)} % · SoundGrab`
    : `${running.length} en cours · SoundGrab`;
}

/* ---------------------------------------------------------------- flux SSE */

function connect() {
  const source = new EventSource('/api/events');

  source.onopen = () => {
    if (!connected) {
      connected = true;
      renderStatus();
      loadHealth().catch(() => { /* le flux compte plus que les pastilles */ });
    }
  };

  source.onmessage = (event) => render(JSON.parse(event.data));

  source.onerror = () => {
    // EventSource sait se reconnecter seul, mais garde parfois une connexion
    // morte : on ferme et on relance nous-memes pour eviter les doublons.
    source.close();
    if (connected) {
      connected = false;
      renderStatus();
    }
    setTimeout(connect, 2000);
  };
}

/* ------------------------------------------------------------------ actions */

async function submit() {
  const urls = els.input.value.trim();
  if (!urls) {
    els.input.focus({ preventScroll: true });
    return;
  }
  els.submit.disabled = true;
  try {
    const { created } = await api('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ urls }),
    });
    els.input.value = '';
    toast(created.length > 1 ? `${created.length} URL mises en file.` : 'URL mise en file.');
  } catch (err) {
    fail(err);
  } finally {
    els.submit.disabled = false;
    els.input.focus({ preventScroll: true });
  }
}

function appendUrl(text) {
  const trimmed = text.trim();
  if (!trimmed) return false;
  els.input.value = els.input.value.trim()
    ? `${els.input.value.trimEnd()}\n${trimmed}`
    : trimmed;
  return true;
}

els.submit.addEventListener('click', submit);
$('#cfg-format').addEventListener('change', syncQualityState);
$('#save-settings').addEventListener('click', saveSettings);
els.settingsToggle.addEventListener('click', () => toggleSettings());

$('#reset-settings').addEventListener('click', () => {
  fillForm(defaults);
  toast('Valeurs par défaut restaurées — reste à les enregistrer.');
});

$('#open-folder').addEventListener('click', () => {
  api('/api/open', { method: 'POST' }).catch(fail);
});

$('#clear').addEventListener('click', () => {
  api('/api/jobs/clear', { method: 'POST' }).catch(fail);
});

$('#paste').addEventListener('click', async () => {
  try {
    if (appendUrl(await navigator.clipboard.readText())) els.input.focus();
  } catch (_) {
    fail(new Error('Le navigateur a refusé l\'accès au presse-papier — utilise Ctrl+V.'));
  }
});

els.input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    submit();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && els.settingsWrap.classList.contains('open')) {
    toggleSettings(false);
    els.settingsToggle.focus();
  }
});

/* --------------------------------------------------------- glisser-deposer */

const dropzone = $('#dropzone');
let dragDepth = 0;  // dragleave se declenche aussi en passant sur un enfant

dropzone.addEventListener('dragenter', (event) => {
  event.preventDefault();
  dragDepth += 1;
  dropzone.classList.add('over');
});

dropzone.addEventListener('dragover', (event) => event.preventDefault());

dropzone.addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropzone.classList.remove('over');
});

dropzone.addEventListener('drop', (event) => {
  event.preventDefault();
  dragDepth = 0;
  dropzone.classList.remove('over');
  const text = event.dataTransfer.getData('text/uri-list')
    || event.dataTransfer.getData('text');
  if (appendUrl(text)) submit();
});

/* ------------------------------------------------------------- demarrage */

applyTheme(document.documentElement.dataset.theme || 'auto');
toggleSettings(false);
loadHealth().catch(fail);
loadSettings().catch(fail);
connect();
els.input.focus();
