/* Independent NBA scoreboard. Only NBA-owned URLs are used for statistics.
 * Static GitHub Pages reads same-origin snapshots validated by sync_nba_data.py;
 * an optional browser test may enable direct reads ONLY from cdn.nba.com.
 * No user-provided relay or third-party feed can supply scores.
 */
'use strict';

const OFFICIAL = Object.freeze({
  live: 'https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json',
  box: (id) => `https://cdn.nba.com/static/json/liveData/boxscore/boxscore_${id}.json`,
  pbp: (id) => `https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_${id}.json`,
  schedule: 'https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json',
  gamesPage: (date) => `https://www.nba.com/games?date=${date}`,
});
const PUBLISHED = Object.freeze({
  index: 'data/index.json', live: 'data/live/scoreboard.json',
  date: (d) => `data/scoreboard/${d}.json`,
  calendar: (year) => `data/calendar/${year}.json`,
  box: (id) => `data/games/${id}/boxscore.json`,
  pbp: (id) => `data/games/${id}/playbyplay.json`,
  schedule: (s) => `data/schedule/${s}.json`,
});
const state = {
  index: null, live: null, schedule: null, season: null, date: null, dateResultGames: null,
  dateKind: null, calendarCount: null, calendars: {}, view: 'rows',
  sourceMode: 'snapshot', isStale: false, readError: null, indexError: null,
  selectedGame: null, boxscore: null, playbyplay: null, detailSeq: 0, boardSeq: 0,
  orderDesc: true, timer: null, refreshing: null,
};
const el = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const validId = (id) => typeof id === 'string' && /^\d{10}$/.test(id);

function validDate(s) {
  if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const d = new Date(`${s}T12:00:00Z`);
  return !Number.isNaN(d.valueOf()) && d.toISOString().slice(0, 10) === s;
}
function nbaToday() {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}
function shiftISO(date, days) {
  const d = new Date(`${date}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
function timeAgo(iso) {
  const ms = new Date(iso).valueOf();
  if (!Number.isFinite(ms)) return 'unknown';
  const minutes = Math.max(0, Math.floor((Date.now() - ms) / 60000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} h ago`;
  return `${Math.floor(minutes / 1440)} d ago`;
}
function localTime(iso, options = { hour: 'numeric', minute: '2-digit' }) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString([], options);
}
function periodName(period) {
  const n = Number(period);
  return n > 4 ? `${n === 5 ? '' : n - 4}OT` : n > 0 ? `Q${n}` : '—';
}
function parseClock(value) {
  if (!value || value === '--:--') return '';
  const match = String(value).match(/^PT(?:(\d+)M)?(\d+(?:\.\d+)?)S$/);
  if (!match) return String(value);
  return `${Number(match[1] || 0)}:${String(Math.floor(Number(match[2]))).padStart(2, '0')}`;
}
function formatMinutes(iso) { return parseClock(iso); }
function logoImg(id, tricode) {
  const name = esc(tricode || '?');
  if (!/^\d+$/.test(String(id))) return `<span class="tri-badge">${name}</span>`;
  return `<img class="team-logo" loading="lazy" alt="" data-tricode="${name}" src="https://cdn.nba.com/logos/nba/${id}/primary/L/logo.svg">`;
}
function headshotImg(id) {
  return /^\d+$/.test(String(id)) && Number(id) > 0
    ? `<img class="headshot" loading="lazy" alt="" src="https://cdn.nba.com/headshots/nba/latest/260x190/${id}.png">`
    : '';
}
function officialGamePage(g) {
  if (!g || !validId(g.id) || typeof g.shareUrl !== 'string') return null;
  try {
    const u = new URL(g.shareUrl);
    return u.protocol === 'https:' && u.hostname === 'www.nba.com' && !u.search && !u.hash
      && new RegExp(`^/game/[a-z0-9-]+-${g.id}$`).test(u.pathname) ? u.href : null;
  } catch (_) { return null; }
}
function gameDate(g) { return g.dateEst || state.date || g.gameEt?.slice(0, 10) || state.live?.feedDate || nbaToday(); }
function sourceLink(url, label) { return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`; }

async function getJSON(url, fresh = false) {
  // Pages' CDN can cache static JSON. A minute-bucket query bounds the browser's
  // stale cache without generating a new URL on every click or hitting localhost.
  const target = fresh ? `${url}${url.includes('?') ? '&' : '?'}v=${Math.floor(Date.now() / 60000)}` : url;
  const response = await fetch(target, { cache: fresh ? 'no-store' : 'default' });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  return response.json();
}
function livePayload(payload, direct) {
  const sb = direct ? payload?.scoreboard : payload?.scoreboard?.scoreboard;
  if (!sb || !validDate(sb.gameDate) || !Array.isArray(sb.games)
      || sb.games.some((g) => !validId(g?.gameId) || !g.homeTeam?.teamId || !g.awayTeam?.teamId)
      || new Set(sb.games.map((g) => g.gameId)).size !== sb.games.length) {
    throw new Error('Unexpected NBA scoreboard schema; preserving last valid snapshot');
  }
  if (!direct && payload._sync?.source !== OFFICIAL.live) {
    throw new Error('Published feed has no matching official NBA provenance');
  }
  return { sb, capturedAt: direct ? new Date().toISOString() : payload._sync.fetchedAtUtc };
}

function teamLive(team) {
  return { id: team?.teamId, tricode: team?.teamTricode, city: team?.teamCity,
    name: team?.teamName, wins: team?.wins, losses: team?.losses, score: team?.score,
    periods: team?.periods || [] };
}
function normLiveGame(g) {
  return { id: g.gameId, source: 'live', status: g.gameStatus, statusText: g.gameStatusText || '',
    clock: parseClock(g.gameClock), period: g.period, regulation: g.regulationPeriods || 4,
    gameEt: g.gameEt, seriesText: g.seriesText || '', tv: [], arena: null,
    leaders: { home: g.gameLeaders?.homeLeaders, away: g.gameLeaders?.awayLeaders },
    home: teamLive(g.homeTeam), away: teamLive(g.awayTeam) };
}
function normCardGame(g) {
  const team = (t) => ({ id: t?.teamId, tricode: t?.tricode, name: t?.name,
    wins: t?.wins, losses: t?.losses, score: t?.score, periods: t?.periods || [] });
  return { id: g.gameId, source: 'date', status: g.gameStatus, statusText: g.gameStatusText || '',
    clock: parseClock(g.gameClock), period: g.period, regulation: 4,
    gameEt: g.gameTimeUtc, gameEtLabel: g.gameTimeEastern, seasonYear: g.seasonYear,
    seasonType: g.seasonType, shareUrl: g.shareUrl, seriesText: '', arena: null,
    tv: g.broadcasters || [], leaders: { home: g.home?.leader, away: g.away?.leader },
    home: team(g.home), away: team(g.away) };
}
function normScheduleGame(g) {
  return { id: g.gameId, source: 'schedule', status: g.gameStatus, statusText: g.gameStatusText || '',
    clock: '', period: 0, regulation: 4, gameEt: g.gameDateTimeUTC, dateEst: g.gameDateEst,
    arena: g.arena, seriesText: g.seriesText || '', tv: (g.broadcasters || []).map((b) => b.name).filter(Boolean),
    gameLabel: g.gameLabel, leaders: { home: null, away: null },
    home: { id: g.homeTeamId, tricode: g.homeTricode, city: g.homeCity,
      name: g.homeName, wins: g.homeWins, losses: g.homeLosses, score: g.homeScore, periods: [] },
    away: { id: g.awayTeamId, tricode: g.awayTricode, city: g.awayCity,
      name: g.awayName, wins: g.awayWins, losses: g.awayLosses, score: g.awayScore, periods: [] } };
}
function setStatus(kind, text, meta = '') {
  el('statusDot').className = `dot ${kind}`;
  el('statusText').textContent = text;
  el('statusMeta').textContent = meta;
}
function updateFreshness() {
  const live = state.live, hb = state.index?.heartbeat || {};
  const success = state.sourceMode === 'direct' ? live?.checkedAt
    : hb.lastSuccessfulLiveUtc || state.index?.live?.fetchedAtUtc || live?.capturedAt;
  const age = success ? Date.now() - new Date(success).valueOf() : Infinity;
  const captureAge = live?.capturedAt ? Date.now() - new Date(live.capturedAt).valueOf() : Infinity;
  const inProgress = live?.games?.some((g) => g.status === 2) || false;
  const feedOld = live && live.feedDate < nbaToday() && !inProgress;
  // A successful fetch may still return unchanged, old game data. Track both the
  // last confirmed NBA read AND the last *changed* scoreboard while a game is on.
  state.isStale = !!state.readError || (state.sourceMode !== 'direct' && !!state.indexError) || !Number.isFinite(age)
    || age < -300000 || age > (inProgress ? 20 : 120) * 60000
    || (inProgress && (!Number.isFinite(captureAge) || captureAge > 30 * 60000)) || !!feedOld;
  el('sourceModePill').textContent = state.sourceMode === 'direct' ? 'Direct cdn.nba.com' : 'Published NBA snapshot';
  el('provLive').textContent = live?.capturedAt ? `${localTime(live.capturedAt, { dateStyle: 'medium', timeStyle: 'short' })} (your time)` : 'Unavailable';
  el('lastVerified').textContent = success ? `${timeAgo(success)} (${localTime(success, { dateStyle: 'medium', timeStyle: 'short' })})` : 'Unavailable';
  el('freshnessPill').textContent = live?.capturedAt ? `Scores captured ${timeAgo(live.capturedAt)}` : 'No captured scores';
  el('liveDatePill').textContent = live?.feedDate ? `NBA date ${live.feedDate} (ET)` : 'No feed date';
  el('liveCountPill').textContent = live ? `${live.games.filter((g) => g.status === 2).length} in progress · ${live.games.length} games` : 'No feed';
  const issues = state.index?.issues || [];
  const warnings = [];
  if (state.isStale) warnings.push(`NBA score snapshot may be out of date (captured ${esc(timeAgo(live?.capturedAt))}; last confirmed live-feed check ${success ? `${esc(timeAgo(success))} at ${esc(success)}` : 'unavailable'}).`);
  if (issues.length) warnings.push(`${issues.length} ingestion irregularit${issues.length === 1 ? 'y' : 'ies'} flagged (including ${esc(issues[0].step || 'unknown step')}).`);
  if (state.readError || state.indexError) warnings.push(`The published data could not be refreshed: ${esc(String((state.readError || state.indexError).message || state.readError || state.indexError))}.`);
  const alert = el('freshnessAlert');
  alert.classList.toggle('hidden', warnings.length === 0);
  alert.innerHTML = warnings.length ? `${warnings.join(' ')} ${sourceLink(OFFICIAL.gamesPage(nbaToday()), 'Review today on NBA.com')} · <a href="data/verification/sync-log.json">Fetch log</a>` : '';
  el('buildInfo').textContent = `NBA feed: ${live?.feedDate || 'unavailable'} · captured: ${live?.capturedAt || 'unavailable'} · last confirmed check: ${success || 'unavailable'} · archived game dates: ${Object.values(state.index?.scoreboards || {}).filter((v) => v.gameCount > 0).length}`;
  if (state.date === null && live) {
    const msg = live.games.length ? `${live.games.length} NBA game${live.games.length === 1 ? '' : 's'} for ${live.feedDate}`
      : `No games in the NBA feed for ${live.feedDate}`;
    setStatus(state.isStale ? 'warn' : inProgress ? 'live' : 'ok', state.isStale ? `Snapshot may be stale · ${msg}` : msg,
      `Captured ${timeAgo(live.capturedAt)} · last confirmed check ${timeAgo(success)} · ${state.sourceMode === 'direct' ? 'direct NBA CDN' : 'published NBA snapshot'}`);
  }
}

function record(t) { return t?.wins == null || t?.losses == null ? '' : `${t.wins}-${t.losses}`; }
function shownScore(g, t) { return g.status === 1 ? '' : (t.score ?? ''); }
function periodScore(t, period) {
  return (t.periods || []).find((p) => Number(p?.period) === period)?.score;
}
function periodCount(g) {
  return Math.max(g.regulation || 4, 4,
    ...[...(g.home?.periods || []), ...(g.away?.periods || [])].map((p) => Number(p?.period) || 0));
}
function statusBadge(g) {
  if (g.status === 2) {
    const label = state.isStale && g.source === 'live' ? 'LAST SEEN LIVE' : 'LIVE';
    const clock = /half|break|intermission/i.test(g.statusText) ? g.statusText : `${periodName(g.period)} ${g.clock || ''}`;
    return `<span class="badge live">${label}</span><span class="clock">${esc(clock.trim())}</span>`;
  }
  if (g.status === 3) return `<span class="badge final">FINAL${Number(g.period) > 4 ? ` · ${periodName(g.period)}` : ''}</span>`;
  const tip = localTime(g.gameEt, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  return `<span class="badge">${esc(g.statusText || (g.status === 1 ? 'Scheduled' : 'Status unavailable'))}</span><span class="clock">${esc(tip ? `${tip} local` : '')}</span>`;
}
function leaderLine(leader, tricode) {
  if (!leader?.name || leader.points == null) return '';
  return `<span class="leader">${esc(tricode)} · ${esc(leader.name)} <strong>${esc(leader.points)} PTS</strong></span>`;
}
function teamRow(g, side) {
  const t = g[side], other = g[side === 'away' ? 'home' : 'away'];
  const winner = g.status === 3 && Number(t.score) > Number(other.score);
  let cells = '';
  for (let i = 1; i <= periodCount(g); i += 1) {
    const points = g.status === 1 ? null : periodScore(t, i);
    cells += `<td>${points == null ? '—' : esc(points)}</td>`;
  }
  return `<tr class="${winner ? 'winner' : ''}"><td class="team-cell"><span class="team-line">
    ${logoImg(t.id, t.tricode)}<span class="team-id">${esc(t.tricode || '—')}</span>
    <span class="team-name">${esc([t.city, t.name].filter(Boolean).join(' ') || t.name || '')}</span>
    <span class="record">${esc(record(t))}</span></span></td>${cells}
    <td class="total">${esc(shownScore(g, t) || (shownScore(g, t) === 0 ? 0 : '—'))}</td></tr>`;
}
function gameRow(g) {
  const header = Array.from({ length: periodCount(g) }, (_, i) => `<th>${periodName(i + 1)}</th>`).join('');
  const meta = [g.seriesText, g.gameLabel, g.tv?.length && [...new Set(g.tv)].join(', '), g.arena].filter(Boolean).map(esc);
  const leaders = [leaderLine(g.leaders?.away, g.away.tricode), leaderLine(g.leaders?.home, g.home.tricode)].filter(Boolean).join('<br>');
  return `<div class="game-row" data-game="${esc(g.id)}" tabindex="0" role="button" aria-label="${esc(g.away.tricode)} at ${esc(g.home.tricode)}, open game details">
    <div class="row-status">${statusBadge(g)}</div><div class="row-main"><table class="row-table">
      <thead><tr><th class="team-cell">Away / home</th>${header}<th class="total">T</th></tr></thead>
      <tbody>${teamRow(g, 'away')}${teamRow(g, 'home')}</tbody></table></div>
    <div class="row-meta">${meta.length ? `<div>${meta.join(' · ')}</div>` : ''}${leaders ? `<div>${leaders}</div>` : ''}</div></div>`;
}
function gameCard(g) {
  return `<div class="game-card" data-game="${esc(g.id)}" tabindex="0" role="button" aria-label="${esc(g.away.tricode)} at ${esc(g.home.tricode)}, open game details">
    <div class="card-status">${statusBadge(g)}</div><div class="card-teams">
      ${['away', 'home'].map((side) => `<div class="card-team">${logoImg(g[side].id, g[side].tricode)}<span>${esc(g[side].tricode)}</span><b>${esc(shownScore(g, g[side]) ?? '')}</b></div>`).join('')}
    </div><div class="card-meta">${esc(g.seriesText || g.tv?.[0] || g.gameLabel || '')}</div></div>`;
}
function filterGames(games) {
  const query = el('gameSearch').value.trim().toLowerCase();
  if (!query) return games;
  return games.filter((g) => [g.id, g.home.tricode, g.away.tricode, g.home.city, g.away.city,
    g.home.name, g.away.name, g.leaders?.home?.name, g.leaders?.away?.name]
    .filter(Boolean).join(' ').toLowerCase().includes(query));
}
function renderGames(games, emptyMessage) {
  const filtered = filterGames(games);
  let rows, cards;
  if (!games.length) rows = cards = `<div class="card">${emptyMessage}</div>`;
  else if (!filtered.length) rows = cards = `<div class="card">No games match “${esc(el('gameSearch').value)}”.</div>`;
  else { rows = filtered.map(gameRow).join(''); cards = filtered.map(gameCard).join(''); }
  el('gamesStrip').innerHTML = rows;
  el('gamesGrid').innerHTML = cards;
  el('gamesStrip').classList.toggle('hidden', state.view !== 'rows');
  el('gamesGrid').classList.toggle('hidden', state.view !== 'cards');
}
function shownGames() { return state.date ? state.dateResultGames || [] : state.live?.games || []; }
function renderBoard() {
  const games = shownGames();
  const msg = state.date ? state.dateKind === 'archive'
    ? `The captured NBA.com game cards contained no games for ${esc(state.date)}. ${sourceLink(OFFICIAL.gamesPage(state.date), 'Check NBA.com')}`
    : state.dateKind === 'calendar-zero'
      ? `NBA.com's year calendar explicitly lists zero games for ${esc(state.date)}. ${sourceLink(OFFICIAL.gamesPage(state.date), 'Check NBA.com')}`
      : state.dateKind === 'calendar-known'
        ? `NBA.com's year calendar lists ${esc(state.calendarCount)} games for ${esc(state.date)}, but this site's scoreboard for the date has not been captured. ${sourceLink(OFFICIAL.gamesPage(state.date), 'View scores on NBA.com')}`
        : `No scoreboard is published here for ${esc(state.date)}. This does not confirm that no games occurred. ${sourceLink(OFFICIAL.gamesPage(state.date), 'Check NBA.com')}`
    : `The official NBA feed lists no games for ${esc(state.live?.feedDate || 'this date')}. ${sourceLink(OFFICIAL.gamesPage(state.live?.feedDate || nbaToday()), 'NBA.com games')}`;
  renderGames(games, msg);
  renderUpcoming();
}
function setView(view) {
  state.view = view;
  for (const [id, type] of [['stripViewBtn', 'rows'], ['cardsViewBtn', 'cards']]) {
    el(id).classList.toggle('active', view === type);
    el(id).setAttribute('aria-pressed', String(view === type));
  }
  renderBoard();
}

async function loadLive({ quiet = false } = {}) {
  if (!quiet && state.date === null) setStatus('loading', 'Reading the NBA scoreboard…');
  try {
    const direct = state.sourceMode === 'direct';
    const payload = await getJSON(direct ? OFFICIAL.live : PUBLISHED.live, true);
    const { sb, capturedAt: fetchedAt } = livePayload(payload, direct);
    const fingerprint = direct ? JSON.stringify([sb.gameDate, sb.games]) : null;
    // A successful direct read is not proof the NBA game state changed. Preserve
    // the last-change time until a score/clock/status actually changes.
    const capturedAt = direct && state.live?.mode === 'direct' && state.live.fingerprint === fingerprint
      ? state.live.capturedAt : fetchedAt;
    state.live = { games: sb.games.map(normLiveGame), feedDate: sb.gameDate,
      capturedAt, checkedAt: fetchedAt, fingerprint, mode: direct ? 'direct' : 'snapshot' };
    state.readError = null;
    updateFreshness();
    if (state.date === null) {
      el('viewDatePill').textContent = `${sb.gameDate} (ET)`;
      renderBoard();
      if (state.schedule && el('scheduleRange').value === 'next') renderSchedule();
    }
    return state.live.games;
  } catch (error) {
    if (state.sourceMode === 'direct') {
      state.sourceMode = 'snapshot';
      el('directResult').textContent = `Direct NBA CDN read stopped working; back to the published snapshot (${error.message}).`;
      return loadLive({ quiet });
    }
    state.readError = error;
    if (!state.live && state.date === null) {
      el('gamesStrip').innerHTML = '<div class="card">No verified NBA scoreboard is available right now. Check the fetch log or NBA.com; scores are not guessed.</div>';
      el('gamesGrid').innerHTML = '';
      setStatus('error', 'NBA data unavailable', String(error.message || error));
    }
    updateFreshness();
    return null;
  }
}
async function testDirectAccess() {
  el('directResult').textContent = 'Checking your browser against cdn.nba.com…';
  try {
    const payload = await getJSON(OFFICIAL.live, true);
    livePayload(payload, true);
    state.sourceMode = 'direct';
    el('directResult').textContent = 'NBA feed readable from this browser. Direct mode: checks about every 10 seconds while the page is open.';
    startTimer();
    await loadLive();
  } catch (error) {
    state.sourceMode = 'snapshot';
    el('directResult').textContent = `NBA CDN blocked or changed its schema (${error.message}); using the published NBA snapshot.`;
    startTimer();
    updateFreshness();
  }
}
function closeDetail() { ++state.detailSeq; state.selectedGame = null; el('gameDetail').classList.add('hidden'); }
function showDateResult(text) { el('dateResult').innerHTML = text; }
async function nbaYearCount(date) {
  const year = date.slice(0, 4);
  const manifest = state.index?.calendars?.[year];
  if (!manifest || manifest.path !== PUBLISHED.calendar(year)) return null;
  let calendar = state.calendars[year];
  if (!calendar || calendar._sync?.fetchedAtUtc !== manifest.fetchedAtUtc) {
    calendar = await getJSON(PUBLISHED.calendar(year), true);
    const sourceDate = calendar._sync?.source?.match(/^https:\/\/www\.nba\.com\/games\?date=(\d{4}-\d{2}-\d{2})$/)?.[1];
    if (calendar.year !== year || !validDate(sourceDate) || sourceDate.slice(0, 4) !== year
        || manifest.source !== calendar._sync.source
        || !calendar.dateCounts || typeof calendar.dateCounts !== 'object' || Array.isArray(calendar.dateCounts)
        || Object.keys(calendar.dateCounts).length !== manifest.knownDates) {
      throw new Error('Published NBA year calendar failed validation');
    }
    state.calendars[year] = calendar;
  }
  // NBA's year map is sparse. An absent key is unknown, NEVER a no-game date.
  const count = calendar.dateCounts[date];
  if (count == null) return null;
  if (!Number.isInteger(count) || count < 0) throw new Error('Invalid NBA year calendar count');
  return count;
}
async function loadDate(date) {
  if (!validDate(date)) {
    showDateResult('<span class="muted">Enter a valid YYYY-MM-DD calendar date.</span>');
    return false;
  }
  if (date === nbaToday() && date === state.live?.feedDate) { backToLive(); return true; }
  const seq = ++state.boardSeq;
  state.date = date;
  state.dateKind = null;
  state.calendarCount = null;
  state.dateResultGames = null;
  closeDetail();
  el('dateInput').value = date;
  el('nbaGamesLink').href = OFFICIAL.gamesPage(date);
  el('viewDatePill').textContent = `${date} (ET)`;
  showDateResult('<span class="loading">Loading captured NBA date…</span>');
  el('gamesStrip').innerHTML = '<div class="loading">Loading date…</div>';
  el('gamesGrid').innerHTML = '<div class="loading">Loading date…</div>';
  setStatus('loading', `Looking up ${date}…`);
  let games = [], calendarCount = null, calendarError = null;
  const listed = state.index?.scoreboards?.[date];
  try {
    if (listed) {
      const payload = await getJSON(PUBLISHED.date(date), true);
      if (payload.gameDate !== date || !Array.isArray(payload.games)
          || payload.games.length !== payload.gameCount || payload._sync?.source !== OFFICIAL.gamesPage(date)
          || payload.games.some((g) => !validId(g.gameId))) throw new Error('NBA date archive failed validation');
      games = payload.games.map(normCardGame);
    } else {
      games = (state.schedule?.games || []).filter((g) => g.dateEst === date);
      if (!games.length && date < nbaToday()) {
        try { calendarCount = await nbaYearCount(date); }
        catch (error) { calendarError = error; } // no count is safer than an invented zero
      }
    }
    if (seq !== state.boardSeq) return false; // a newer date/today was selected
    state.dateResultGames = games;
    state.calendarCount = calendarCount;
    state.dateKind = listed ? 'archive' : games.length ? 'schedule' : calendarCount === 0 ? 'calendar-zero'
      : calendarCount > 0 ? 'calendar-known' : 'missing';
    if (listed) {
      showDateResult(`<p>${games.length} game${games.length === 1 ? '' : 's'} captured from NBA.com for ${date}. ${sourceLink(OFFICIAL.gamesPage(date), 'Compare on NBA.com')}</p>`);
      setStatus('ok', `${games.length} NBA.com games captured for ${date}`, 'Historical snapshot, not a live feed');
    } else if (games.length) {
      showDateResult(`<p>No captured date cards yet. These ${games.length} matchups are in the ${sourceLink(OFFICIAL.schedule, 'NBA’s published season schedule')}; scores and times can change.</p>`);
      setStatus('ok', `${games.length} NBA-scheduled games for ${date}`, 'The schedule is not a result or live feed');
    } else if (calendarCount === 0) {
      showDateResult(`<p>NBA.com's year calendar explicitly records zero games for ${date}; the full date card was not captured here. ${sourceLink(OFFICIAL.gamesPage(date), 'Verify on NBA.com')}</p>`);
      setStatus('ok', `NBA calendar: no games on ${date}`, 'Explicit date count; missing year-map entries stay unknown');
    } else if (calendarCount > 0) {
      showDateResult(`<p>NBA.com's year calendar lists ${calendarCount} game${calendarCount === 1 ? '' : 's'} for ${date}, but no date scoreboard is archived here yet. ${sourceLink(OFFICIAL.gamesPage(date), 'See scores on NBA.com')}</p>`);
      setStatus('warn', `${date}: ${calendarCount} NBA games, scores not archived`, 'Do not infer results from the count');
    } else {
      showDateResult(`<p>This date has not been captured. That does <strong>not</strong> mean no games were played. ${sourceLink(OFFICIAL.gamesPage(date), 'Check NBA.com for this date')}</p>${calendarError ? `<p class="note">NBA year calendar unavailable: ${esc(calendarError.message)}</p>` : ''}`);
      setStatus('warn', `${date} is not archived`, 'No official game count or score is available here');
    }
    el('liveNote').textContent = listed ? `Historical NBA.com snapshot for ${date}; click a game for available detail.`
      : games.length ? 'Scheduled matchups, not verified scores.'
        : 'NBA year counts are sparse; no game is invented for an uncaptured date.';
    renderBoard();
    el('live').scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    return !!listed || !!games.length || calendarCount === 0;
  } catch (error) {
    if (seq !== state.boardSeq) return false;
    state.dateResultGames = [];
    state.dateKind = 'error';
    renderBoard();
    showDateResult(`<p>NBA data for ${esc(date)} could not be read. No games are inferred. ${sourceLink(OFFICIAL.gamesPage(date), 'Open NBA.com')}</p><p class="note">${esc(error.message)}</p>`);
    setStatus('error', `Could not load ${date}`, String(error.message || error));
    return false;
  }
}
function shiftDate(days) { return loadDate(shiftISO(state.date || state.live?.feedDate || nbaToday(), days)); }
function backToLive() {
  ++state.boardSeq;
  closeDetail();
  state.date = null;
  state.dateKind = null;
  state.calendarCount = null;
  state.dateResultGames = null;
  el('viewDatePill').textContent = `${state.live?.feedDate || nbaToday()} (ET)`;
  el('liveNote').textContent = 'Official NBA scores and quarters. Captures may lag a live game; check the time above.';
  showDateResult('');
  renderBoard();
  loadLive();
}

function renderUpcoming() {
  const target = el('upcomingPreview');
  target.classList.add('hidden');
  if (state.date || state.live?.games.length || !state.schedule?.games.length) return;
  const upcoming = state.schedule.games.filter((g) => g.dateEst >= nbaToday() && g.status === 1)
    .sort((a, b) => (a.gameEt || '').localeCompare(b.gameEt || '')).slice(0, 3);
  if (!upcoming.length) return;
  target.classList.remove('hidden');
  target.innerHTML = `<h3>Next on the NBA schedule</h3><div class="games-grid">${upcoming.map(gameCard).join('')}</div>
    <p class="note">Scheduled, not live. ${sourceLink(OFFICIAL.schedule, 'Official NBA schedule')}</p>`;
}
function findGame(id) {
  const preferred = state.date ? state.dateResultGames : state.live?.games;
  return preferred?.find((g) => g.id === id)
    || state.schedule?.games.find((g) => g.id === id)
    || { id, away: {}, home: {}, leaders: {}, source: 'unknown', status: null };
}
function tabSelect(name) {
  document.querySelectorAll('.detail-tabs [data-tab]').forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    el(`${button.dataset.tab}Panel`).classList.toggle('active', active);
  });
}
async function readGameDetail(id, kind) {
  const direct = state.sourceMode === 'direct';
  const raw = await getJSON(direct ? OFFICIAL[kind](id) : PUBLISHED[kind](id), true);
  if (kind === 'box') {
    if (!raw?.game || raw.game.gameId !== id || !raw.game.homeTeam || !raw.game.awayTeam) throw new Error('Invalid NBA box score');
    return direct ? { ...raw, _sync: { source: OFFICIAL.box(id), fetchedAtUtc: new Date().toISOString() } } : raw;
  }
  const actions = direct ? raw?.game?.actions : raw?.actions;
  const actualId = direct ? raw?.game?.gameId : raw?.gameId;
  if (actualId !== id || !Array.isArray(actions)) throw new Error('Invalid NBA play-by-play');
  return direct ? { gameId: id, actions, actionCount: actions.length,
    _sync: { source: OFFICIAL.pbp(id), fetchedAtUtc: new Date().toISOString() } } : raw;
}
async function openGame(id, refresh = false) {
  if (!validId(id) || (refresh && state.selectedGame?.id !== id)) return;
  const g = refresh ? state.selectedGame : findGame(id);
  const seq = ++state.detailSeq;
  if (!refresh) {
    state.selectedGame = g;
    state.boxscore = null;
    state.playbyplay = null;
    state.orderDesc = true;
    el('pbpOrderBtn').textContent = 'Latest first';
    el('pbpOrderBtn').setAttribute('aria-pressed', 'true');
    el('searchActions').value = '';
    el('periodFilter').value = 'all';
    el('teamFilter').value = 'all';
    tabSelect('boxscore');
    el('gameDetail').classList.remove('hidden');
    el('detailTitle').textContent = `${g.away.tricode || 'Away'} @ ${g.home.tricode || 'Home'} · ${id}`;
    const page = officialGamePage(g) || OFFICIAL.gamesPage(gameDate(g));
    el('detailEndpoints').innerHTML = `${sourceLink(page, 'Official NBA game / date page')} · ${sourceLink(OFFICIAL.box(id), 'Raw NBA box score')} · ${sourceLink(OFFICIAL.pbp(id), 'Raw NBA play-by-play')}`;
    el('boxscoreContent').innerHTML = '<div class="loading">Loading official player statistics…</div>';
    el('playbyplayContent').innerHTML = '<div class="loading">Loading official actions…</div>';
    renderInfo(g);
    el('gameDetail').scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }
  const [box, pbp] = await Promise.allSettled([readGameDetail(id, 'box'), readGameDetail(id, 'pbp')]);
  if (seq !== state.detailSeq) return; // never paint a previous game's delayed response
  if (box.status === 'fulfilled') {
    const changed = box.value._sync?.contentHash !== state.boxscore?._sync?.contentHash || !refresh;
    state.boxscore = box.value;
    if (changed) renderBoxscore(box.value);
  } else if (!refresh || !state.boxscore) el('boxscoreContent').innerHTML = boxscoreFallback(g, box.reason);
  if (pbp.status === 'fulfilled') {
    const changed = pbp.value._sync?.contentHash !== state.playbyplay?._sync?.contentHash || !refresh;
    state.playbyplay = pbp.value;
    if (changed) renderPlayByPlay(pbp.value);
  } else if (!refresh || !state.playbyplay) el('playbyplayContent').innerHTML = `<div class="card">
    <h3>Play-by-play unavailable here</h3><p>The NBA detail file is not in this snapshot (or cannot be read). No actions are invented.</p>
    <p class="note">${esc(pbp.reason?.message || pbp.reason || '')} · ${sourceLink(officialGamePage(g) ? `${officialGamePage(g)}/play-by-play` : OFFICIAL.pbp(id), 'Review on NBA.com')}</p></div>`;
}
function boxscoreFallback(g, error) {
  const side = (name) => {
    const t = g[name] || {};
    const periods = Array.from({ length: periodCount(g) }, (_, i) => {
      const score = g.status === 1 ? null : periodScore(t, i + 1);
      return `<td>${score == null ? '—' : esc(score)}</td>`;
    }).join('');
    return `<tr><td>${esc(t.tricode || name)}</td>${periods}<td><b>${esc(shownScore(g, t) ?? '')}</b></td></tr>`;
  };
  return `<div class="card"><h3>${g.status === 1 ? 'Box score begins at tip-off' : 'Player stats unavailable in this snapshot'}</h3>
    <p>Only the official scoreboard line is shown; missing player statistics are not estimated.</p>
    <div class="table-wrap"><table class="box-table"><thead><tr><th>Team</th>${Array.from({ length: periodCount(g) }, (_, i) => `<th>${periodName(i + 1)}</th>`).join('')}<th>T</th></tr></thead>
      <tbody>${side('away')}${side('home')}</tbody></table></div>
    <p class="note">${esc(error?.message || '')} · ${sourceLink(officialGamePage(g) ? `${officialGamePage(g)}/box-score` : OFFICIAL.gamesPage(gameDate(g)), 'Check NBA.com')}</p></div>`;
}
function statCell(player, key) { const value = player.statistics?.[key]; return value == null ? '—' : esc(value); }
function renderBoxscore(box) {
  const g = box.game;
  const teamTable = (team) => {
    const t = g[`${team}Team`];
    const rows = (t.players || []).map((p) => `<tr><td class="player-cell">${headshotImg(p.personId)}<span>${esc(p.name || p.nameI || '—')}${p.jerseyNum ? ` <small class="muted">#${esc(p.jerseyNum)}</small>` : ''}</span></td>
      <td>${esc(formatMinutes(p.statistics?.minutes) || p.statistics?.comment || '—')}</td>
      <td><strong>${statCell(p, 'points')}</strong></td><td>${statCell(p, 'reboundsTotal')}</td><td>${statCell(p, 'assists')}</td>
      <td>${statCell(p, 'steals')}</td><td>${statCell(p, 'blocks')}</td><td>${statCell(p, 'turnovers')}</td>
      <td>${statCell(p, 'fieldGoalsMade')}-${statCell(p, 'fieldGoalsAttempted')}</td>
      <td>${statCell(p, 'threePointersMade')}-${statCell(p, 'threePointersAttempted')}</td>
      <td>${statCell(p, 'freeThrowsMade')}-${statCell(p, 'freeThrowsAttempted')}</td><td>${statCell(p, 'plusMinusPoints')}</td></tr>`).join('');
    const s = t.statistics || {};
    return `<div class="card"><h3>${esc(t.teamTricode)} · ${esc([t.teamCity, t.teamName].filter(Boolean).join(' '))} ${esc(t.score ?? '')}</h3>
      ${rows ? `<div class="table-wrap"><table class="box-table"><thead><tr><th>Player</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th><th>TO</th><th>FG</th><th>3PT</th><th>FT</th><th>+/-</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : '<p class="note">The official box score has no player lines yet.</p>'}
      <p class="note">Team totals · ${esc(s.fieldGoalsMade ?? '—')}/${esc(s.fieldGoalsAttempted ?? '—')} FG · ${esc(s.threePointersMade ?? '—')}/${esc(s.threePointersAttempted ?? '—')} 3PT · ${esc(s.reboundsTotal ?? '—')} REB · ${esc(s.assists ?? '—')} AST</p></div>`;
  };
  el('boxscoreContent').innerHTML = `<div class="card meta-card"><strong>${esc(g.gameStatusText || 'NBA box score')}${g.gameStatus === 2 ? ' · in progress' : ''}</strong>
    <span class="muted">Captured ${esc(box._sync?.fetchedAtUtc || 'direct from NBA')} · ${sourceLink(box._sync?.source || OFFICIAL.box(g.gameId), 'NBA source')}</span></div>
    ${teamTable('away')}${teamTable('home')}`;
}
function renderPlayByPlay(pbp) {
  const actions = pbp.actions;
  const previousPeriod = el('periodFilter').value, previousTeam = el('teamFilter').value;
  const periods = [...new Set(actions.map((a) => a.period).filter((p) => Number(p) > 0))].sort((a, b) => a - b);
  const teams = [...new Set(actions.map((a) => a.teamTricode).filter(Boolean))];
  el('periodFilter').innerHTML = '<option value="all">All periods</option>' + periods.map((p) => `<option value="${esc(p)}">${esc(periodName(p))}</option>`).join('');
  el('teamFilter').innerHTML = '<option value="all">Both teams</option>' + teams.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
  el('periodFilter').value = periods.some((p) => String(p) === previousPeriod) ? previousPeriod : 'all';
  el('teamFilter').value = teams.includes(previousTeam) ? previousTeam : 'all';
  paintActions();
}
function paintActions() {
  const pbp = state.playbyplay;
  if (!pbp) return;
  const period = el('periodFilter').value, team = el('teamFilter').value;
  const query = el('searchActions').value.trim().toLowerCase();
  const actions = pbp.actions || [];
  const matching = actions.filter((a) => (period === 'all' || String(a.period) === period)
    && (team === 'all' || a.teamTricode === team)
    && (!query || `${a.description || ''} ${a.actionType || ''} ${a.subType || ''}`.toLowerCase().includes(query)));
  const ordered = state.orderDesc ? [...matching].reverse() : matching;
  el('playbyplayContent').innerHTML = `<div class="card"><p class="note">${matching.length} of ${actions.length} official actions · captured ${esc(pbp._sync?.fetchedAtUtc || 'direct from NBA')} · ${sourceLink(pbp._sync?.source || OFFICIAL.pbp(pbp.gameId), 'NBA play-by-play')}</p>
    ${ordered.length ? `<div class="pbp">${ordered.map((a) => `<div class="pbp-row"><span class="pbp-time">${esc(periodName(a.period))} ${esc(parseClock(a.clock))}</span>
      <span class="pbp-team">${esc(a.teamTricode || '')}</span><span>${esc(a.description || a.actionType || '')}</span>
      <span class="pbp-score">${a.scoreAway != null && a.scoreHome != null ? `${esc(a.scoreAway)}–${esc(a.scoreHome)}` : ''}</span></div>`).join('')}</div>`
      : '<p>No matching actions in the published NBA feed yet.</p>'}</div>`;
}
function renderInfo(g) {
  const rows = [
    ['Game ID', g.id], ['Status', g.statusText || '—'], ['NBA date (ET)', gameDate(g)],
    ['Local tip-off', localTime(g.gameEt, { dateStyle: 'medium', timeStyle: 'short' }) || '—'],
    ['Season', g.seasonYear ? `${g.seasonYear} ${g.seasonType || ''}` : '—'],
    ['Arena', g.arena || '—'], ['Broadcast', g.tv?.length ? [...new Set(g.tv)].join(', ') : '—'],
  ];
  el('infoContent').innerHTML = `<div class="card"><table class="kv"><tbody>${rows.map(([key, value]) => `<tr><th>${esc(key)}</th><td>${esc(value)}</td></tr>`).join('')}</tbody></table>
    <p class="note">${sourceLink(officialGamePage(g) || OFFICIAL.gamesPage(gameDate(g)), 'Official NBA.com game / date page')}<br>
      ${sourceLink(OFFICIAL.box(g.id), 'Official raw box score')}<br>${sourceLink(OFFICIAL.pbp(g.id), 'Official raw play-by-play')}</p></div>`;
}

function scheduleRows() {
  let games = state.schedule?.games || [];
  const query = el('scheduleFilter').value.trim().toLowerCase();
  if (query) games = games.filter((g) => [g.id, g.away.name, g.home.name, g.away.tricode, g.home.tricode]
    .filter(Boolean).join(' ').toLowerCase().includes(query));
  const byDate = new Map();
  for (const g of games) {
    if (el('scheduleRange').value === 'next' && g.dateEst < nbaToday()) continue;
    if (!byDate.has(g.dateEst)) byDate.set(g.dateEst, []);
    byDate.get(g.dateEst).push(g);
  }
  const dates = [...byDate.keys()].sort();
  if (el('scheduleRange').value === 'next') dates.splice(7);
  return dates.map((date) => `<div class="schedule-day"><h3>${esc(localTime(`${date}T12:00:00Z`, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }))} · ${byDate.get(date).length} games</h3>
    ${byDate.get(date).sort((a, b) => (a.gameEt || '').localeCompare(b.gameEt || '')).map((g) => {
      const live = state.live?.feedDate === date && state.live.games.find((item) => item.id === g.id);
      const current = live || g;
      return `<div class="schedule-row" data-game="${esc(g.id)}" role="button" tabindex="0" aria-label="${esc(g.away.tricode)} at ${esc(g.home.tricode)}, open game details">
        <span class="sched-time">${esc(localTime(g.gameEt, { hour: 'numeric', minute: '2-digit' }) || 'TBD')} local</span>
        <span class="sched-teams">${esc(g.away.tricode)} @ ${esc(g.home.tricode)}</span>
        <span class="sched-status">${esc(current.statusText || (current.status === 3 ? 'Final' : 'Scheduled'))}${g.gameLabel ? ` · ${esc(g.gameLabel)}` : ''}</span>
        <span class="sched-arena">${esc(g.arena || '')}</span></div>`;
    }).join('')}</div>`).join('');
}
function renderSchedule() {
  if (!state.schedule) return;
  const rows = scheduleRows();
  el('scheduleContent').innerHTML = rows || `<div class="card">No matching ${el('scheduleRange').value === 'next' ? 'upcoming ' : ''}games in this published season. Try “Full published season” or ${sourceLink(OFFICIAL.schedule, 'NBA schedule')}.</div>`;
  renderUpcoming();
}
async function loadSchedule(season) {
  el('scheduleContent').innerHTML = '<div class="loading">Loading published NBA schedule…</div>';
  try {
    const payload = await getJSON(PUBLISHED.schedule(season), true);
    if (payload.season !== season || !Array.isArray(payload.games) || payload._sync?.source !== OFFICIAL.schedule
        || payload.gameCount !== payload.games.length) throw new Error('Unexpected NBA schedule schema');
    if (state.season !== season) return;
    if (payload.games.some((g) => !validId(g.gameId) || !validDate(g.gameDateEst))) throw new Error('Invalid NBA schedule game identity or date');
    state.schedule = { season, games: payload.games.map(normScheduleGame) };
    renderSchedule();
    if (state.dateKind === 'missing' && state.schedule.games.some((g) => g.dateEst === state.date)) loadDate(state.date);
  } catch (error) {
    if (state.season === season) el('scheduleContent').innerHTML = `<div class="card">Published NBA schedule unavailable (${esc(error.message)}). ${sourceLink(OFFICIAL.schedule, 'Check NBA schedule')}</div>`;
  }
}
function populateIndex() {
  const schedules = Object.keys(state.index?.schedules || {}).sort();
  const select = el('seasonSelect');
  select.innerHTML = schedules.length ? schedules.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('') : '<option value="">No season published</option>';
  if (!schedules.includes(state.season)) state.season = schedules.at(-1) || null;
  if (state.season) select.value = state.season;
  const entries = Object.entries(state.index?.scoreboards || {});
  const gameDates = entries.filter(([, v]) => v.gameCount > 0).sort((a, b) => b[0].localeCompare(a[0]));
  const knownGames = Object.values(state.index?.calendars || {}).reduce((sum, year) => sum + (year.gameDates || 0), 0);
  el('archiveCount').textContent = `${gameDates.length} captured game dates · ${entries.length - gameDates.length} checked no-game dates${knownGames ? ` · ${knownGames} NBA calendar game dates (counts only)` : ''}`;
  el('archiveList').innerHTML = gameDates.slice(0, 80).map(([d, meta]) => `<button type="button" class="chip" data-date="${esc(d)}">${esc(d)} · ${esc(meta.gameCount)} ${meta.gameCount === 1 ? 'game' : 'games'}</button>`).join('')
    || '<span class="muted">No historical dates have been published yet.</span>';
}
function startTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  if (el('autoRefresh').checked) state.timer = setInterval(() => refreshAll(true), state.sourceMode === 'direct' ? 10000 : 60000);
}
async function refreshAll(quiet = false) {
  if (state.refreshing) return state.refreshing;
  state.refreshing = (async () => {
    try {
      state.index = await getJSON(PUBLISHED.index, true);
      state.indexError = null;
      populateIndex();
    } catch (error) { state.indexError = error; }
    if (state.date === null) {
      await loadLive({ quiet });
      if (state.selectedGame?.status === 2 && state.date === null) {
        const freshGame = state.live?.games.find((g) => g.id === state.selectedGame.id);
        if (freshGame) {
          state.selectedGame = freshGame;
          await openGame(freshGame.id, true);
        }
      }
    }
    updateFreshness();
  })().finally(() => { state.refreshing = null; });
  return state.refreshing;
}
function bindEvents() {
  el('refreshBtn').addEventListener('click', () => refreshAll());
  el('autoRefresh').addEventListener('change', startTimer);
  el('testDirectBtn').addEventListener('click', testDirectAccess);
  el('stripViewBtn').addEventListener('click', () => setView('rows'));
  el('cardsViewBtn').addEventListener('click', () => setView('cards'));
  el('gameSearch').addEventListener('input', renderBoard);
  el('prevDayBtn').addEventListener('click', () => shiftDate(-1));
  el('todayBtn').addEventListener('click', backToLive);
  el('nextDayBtn').addEventListener('click', () => shiftDate(1));
  el('loadByDateBtn').addEventListener('click', () => loadDate(el('dateInput').value));
  el('yesterdayBtn').addEventListener('click', () => loadDate(shiftISO(nbaToday(), -1)));
  el('dateInput').addEventListener('change', (event) => {
    if (validDate(event.target.value)) el('nbaGamesLink').href = OFFICIAL.gamesPage(event.target.value);
  });
  el('seasonSelect').addEventListener('change', (event) => {
    state.season = event.target.value;
    state.schedule = null;
    loadSchedule(state.season);
  });
  el('scheduleRange').addEventListener('change', renderSchedule);
  el('scheduleFilter').addEventListener('input', renderSchedule);
  el('closeDetail').addEventListener('click', closeDetail);
  document.querySelectorAll('.detail-tabs [data-tab]').forEach((button) => button.addEventListener('click', () => tabSelect(button.dataset.tab)));
  ['periodFilter', 'teamFilter'].forEach((id) => el(id).addEventListener('change', paintActions));
  el('searchActions').addEventListener('input', paintActions);
  el('pbpOrderBtn').addEventListener('click', () => {
    state.orderDesc = !state.orderDesc;
    el('pbpOrderBtn').textContent = state.orderDesc ? 'Latest first' : 'Oldest first';
    el('pbpOrderBtn').setAttribute('aria-pressed', String(state.orderDesc));
    paintActions();
  });
  document.addEventListener('click', (event) => {
    const date = event.target.closest('[data-date]');
    if (date) { loadDate(date.dataset.date); return; }
    const game = event.target.closest('[data-game]');
    if (game) openGame(game.dataset.game);
  });
  document.addEventListener('keydown', (event) => {
    const game = event.target.closest('[data-game]');
    if (game && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault(); openGame(game.dataset.game);
    }
  });
  document.addEventListener('error', (event) => {
    if (event.target.matches?.('img.team-logo')) {
      const label = document.createElement('span');
      label.className = 'tri-badge';
      label.textContent = event.target.dataset.tricode || '?';
      event.target.replaceWith(label);
    }
    if (event.target.matches?.('img.headshot')) event.target.remove();
  }, true);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && el('autoRefresh').checked) refreshAll(true);
  });
}
async function init() {
  bindEvents();
  const yesterday = shiftISO(nbaToday(), -1);
  el('dateInput').value = yesterday;
  el('nbaGamesLink').href = OFFICIAL.gamesPage(yesterday);
  try {
    state.index = await getJSON(PUBLISHED.index, true);
    populateIndex();
  } catch (error) {
    state.indexError = error;
    el('scheduleContent').innerHTML = '<div class="card">No NBA schedule has been published yet.</div>';
  }
  await Promise.all([loadLive(), ...(state.season ? [loadSchedule(state.season)] : [])]);
  updateFreshness();
  startTimer();
}
document.addEventListener('DOMContentLoaded', init);
// Read-only hooks for offline UI regression tests and browser troubleshooting.
window.NBAScoreboard = { loadLive, loadDate, openGame, loadSchedule, backToLive, refreshAll, state };
