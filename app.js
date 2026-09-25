/* NBA Scoreboard — official data only.
 *
 * The browser never calls NBA endpoints directly: measured evidence shows the NBA CDN
 * answers 403 to any page whose Origin is not nba.com, NBA's S3 mirror sends no CORS
 * header, and stats.nba.com is unreachable from cloud infrastructure. Everything shown
 * here is published by the GitHub Actions pipeline (scripts/sync_nba_data.py) and read
 * same-origin from ./data/.
 *
 * Optional: a visitor can point the page at their own relay (a tiny pass-through they
 * host) to get true live refreshes straight from the official feeds. See README.
 */
'use strict';

const OFFICIAL = {
  live: 'https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json',
  box: (id) => `https://cdn.nba.com/static/json/liveData/boxscore/boxscore_${id}.json`,
  pbp: (id) => `https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_${id}.json`,
  schedule: 'https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json',
  gamesPage: (d) => `https://www.nba.com/games?date=${d}`,
  standings: 'https://www.nba.com/standings',
};

const PUBLISHED = {
  index: 'data/index.json',
  live: 'data/live/scoreboard.json',
  plays: 'data/live/plays.json',
  date: (d) => `data/scoreboard/${d}.json`,
  box: (id) => `data/games/${id}/boxscore.json`,
  pbp: (id) => `data/games/${id}/playbyplay.json`,
  schedule: (s) => `data/schedule/${s}.json`,
};

const state = {
  index: null,
  live: null,
  plays: null,
  schedule: null,
  season: null,
  view: 'strip',
  date: null,          // date currently displayed in the board
  selectedGame: null,  // normalized game object
  boxscore: null,
  playbyplay: null,
  timer: null,
  relay: readRelay(),
};

const el = (id) => document.getElementById(id);

/* ------------------------------------------------------------------ helpers */

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function readRelay() {
  try {
    const qs = new URLSearchParams(location.search).get('relay');
    if (qs) { localStorage.setItem('relayUrl', qs); return qs; }
    return localStorage.getItem('relayUrl') || '';
  } catch (e) { return ''; }
}

function withRelay(url) {
  if (!state.relay) return url;
  const sep = state.relay.includes('?') ? '&' : '?';
  return `${state.relay}${sep}url=${encodeURIComponent(url)}`;
}

async function getJSON(url, { relay = false, noCache = false } = {}) {
  const target = relay ? withRelay(url) : url;
  const opts = noCache ? { cache: 'no-cache' } : {};
  const res = await fetch(target, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${target}`);
  return res.json();
}

function parseClock(clock) {
  if (!clock || typeof clock !== 'string' || clock === '--:--') return '';
  const m = clock.match(/PT(?:(\d+)M)?([\d.]+)S/);
  if (!m) return clock;
  return `${parseInt(m[1] || '0', 10)}:${String(Math.floor(parseFloat(m[2] || '0'))).padStart(2, '0')}`;
}

function periodName(n) {
  const p = Number(n) || 0;
  if (p <= 4) return `Q${p}`;
  return p === 5 ? 'OT' : `${p - 4}OT`;
}

function timeAgo(iso) {
  if (!iso) return 'unknown';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'unknown';
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 36) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

function teamLogo(teamId) {
  if (!teamId) return '';
  return `https://cdn.nba.com/logos/nba/${teamId}/primary/L/logo.svg`;
}

function headshot(personId) {
  if (!personId) return '';
  return `https://cdn.nba.com/headshots/nba/latest/260x190/${personId}.png`;
}

function logoImg(teamId, tricode) {
  if (!teamId) return `<span class="tri-badge">${esc(tricode || '?')}</span>`;
  return `<img class="team-logo" loading="lazy" alt="" src="${teamLogo(teamId)}"
    onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'tri-badge',textContent:'${esc(tricode || '')}'}))">`;
}

/* ---------------------------------------------------------- normalisation */

function record(t) {
  if (t.wins == null || t.losses == null) return '';
  return `${t.wins}-${t.losses}`;
}

function normLiveGame(g) {
  return {
    id: g.gameId,
    code: g.gameCode,
    source: 'live',
    status: g.gameStatus,
    statusText: g.gameStatusText || '',
    clock: parseClock(g.gameClock),
    period: g.period,
    regulation: g.regulationPeriods || 4,
    seriesText: g.seriesText || '',
    gameEt: g.gameEt,
    tv: [],
    leaders: {
      home: g.gameLeaders?.homeLeaders || null,
      away: g.gameLeaders?.awayLeaders || null,
    },
    home: {
      id: g.homeTeam?.teamId, tricode: g.homeTeam?.teamTricode,
      city: g.homeTeam?.teamCity, name: g.homeTeam?.teamName,
      wins: g.homeTeam?.wins, losses: g.homeTeam?.losses,
      score: g.homeTeam?.score,
      periods: (g.homeTeam?.periods || []).map((p) => p.score),
      inBonus: g.homeTeam?.inBonus, timeouts: g.homeTeam?.timeoutsRemaining,
    },
    away: {
      id: g.awayTeam?.teamId, tricode: g.awayTeam?.teamTricode,
      city: g.awayTeam?.teamCity, name: g.awayTeam?.teamName,
      wins: g.awayTeam?.wins, losses: g.awayTeam?.losses,
      score: g.awayTeam?.score,
      periods: (g.awayTeam?.periods || []).map((p) => p.score),
      inBonus: g.awayTeam?.inBonus, timeouts: g.awayTeam?.timeoutsRemaining,
    },
  };
}

function normCardGame(g) {
  return {
    id: g.gameId,
    code: g.gameCode || g.shareUrl || '',
    source: 'date',
    status: g.gameStatus,
    statusText: g.gameStatusText || '',
    clock: parseClock(g.gameClock),
    period: g.period,
    regulation: 4,
    seriesText: '',
    gameEt: g.gameTimeEastern || g.gameTimeUtc,
    seasonYear: g.seasonYear,
    seasonType: g.seasonType,
    shareUrl: g.shareUrl,
    tv: g.broadcasters || [],
    leaders: {
      home: g.home?.leader || null,
      away: g.away?.leader || null,
    },
    home: {
      id: g.home?.teamId, tricode: g.home?.tricode, city: g.home?.name ? null : null,
      name: g.home?.name, wins: g.home?.wins, losses: g.home?.losses,
      score: g.home?.score,
      periods: (g.home?.periods || []).map((p) => p.score),
      inBonus: g.home?.inBonus, timeouts: g.home?.timeoutsRemaining,
    },
    away: {
      id: g.away?.teamId, tricode: g.away?.tricode,
      name: g.away?.name, wins: g.away?.wins, losses: g.away?.losses,
      score: g.away?.score,
      periods: (g.away?.periods || []).map((p) => p.score),
      inBonus: g.away?.inBonus, timeouts: g.away?.timeoutsRemaining,
    },
  };
}

function normScheduleGame(g) {
  return {
    id: g.gameId,
    code: g.gameCode,
    source: 'schedule',
    status: g.gameStatus,
    statusText: g.gameStatusText || '',
    clock: '',
    period: 0,
    regulation: 4,
    seriesText: g.seriesText || '',
    gameEt: g.gameDateTimeUTC,
    dateEst: g.gameDateEst,
    arena: g.arena,
    gameLabel: g.gameLabel,
    gameSubLabel: g.gameSubLabel,
    tv: (g.broadcasters || []).map((b) => b.name).filter(Boolean),
    leaders: { home: null, away: null },
    home: { id: g.homeTeamId, tricode: g.homeTricode, name: g.homeName, city: g.homeCity,
            wins: g.homeWins, losses: g.homeLosses, score: g.homeScore, periods: [] },
    away: { id: g.awayTeamId, tricode: g.awayTricode, name: g.awayName, city: g.awayCity,
            wins: g.awayWins, losses: g.awayLosses, score: g.awayScore, periods: [] },
  };
}

/* ------------------------------------------------------------------ status */

function setStatus(kind, text, meta = '') {
  el('statusDot').className = `dot ${kind}`;
  el('statusText').textContent = text;
  el('statusMeta').textContent = meta;
}

function updateFreshness() {
  const live = state.index?.live;
  const pill = el('freshnessPill');
  if (!live) { pill.textContent = 'no published data yet'; return; }
  const age = live.fetchedAtUtc ? timeAgo(live.fetchedAtUtc) : 'unknown';
  pill.textContent = `snapshot ${age}`;
  el('provLive').textContent = `${live.fetchedAtUtc || '—'} (UTC) · ${live.gameCount ?? 0} games`;
  el('liveDatePill').textContent = live.feedDate ? `feed date ${live.feedDate}` : '—';
  el('liveCountPill').textContent = `${live.liveCount ?? 0} live / ${live.gameCount ?? 0} games`;
  el('buildInfo').textContent = `Published data: ${live.fetchedAtUtc || '—'} · archived dates: ${Object.keys(state.index?.scoreboards || {}).length} · archived games: ${Object.keys(state.index?.games || {}).length}${state.relay ? ' · relay: ' + state.relay : ''}`;
}

/* -------------------------------------------------------------- rendering */

function statusBadgeFor(g) {
  if (g.status === 2) {
    const clock = g.clock || '';
    return `<span class="badge live">● LIVE</span><span class="clock">${esc(periodName(g.period))} ${esc(clock)}</span>`;
  }
  if (g.status === 3) {
    return `<span class="badge final">FINAL</span><span class="clock">${esc(g.statusText.replace('Final', '') || '')}</span>`;
  }
  return `<span class="badge sched">${esc(g.statusText || 'Scheduled')}</span><span class="clock">${esc(g.gameEt ? new Date(g.gameEt).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '')}</span>`;
}

function periodsHeader(g) {
  const count = Math.max(g.regulation || 4, g.home.periods.length, g.away.periods.length);
  let html = '';
  for (let i = 1; i <= count; i += 1) html += `<th>${esc(periodName(i))}</th>`;
  return html + '<th class="total">T</th>';
}

function periodsCells(g, side) {
  const count = Math.max(g.regulation || 4, g.home.periods.length, g.away.periods.length);
  let html = '';
  for (let i = 0; i < count; i += 1) {
    const v = side.periods[i];
    html += `<td>${v == null ? '' : esc(v)}</td>`;
  }
  return html + `<td class="total">${esc(side.score ?? '')}</td>`;
}

function leaderLine(g, side) {
  const l = g.leaders?.[side];
  if (!l || !l.name) return '';
  return `<span class="leader">${esc(l.name)} <b>${esc(l.points ?? 0)}</b> pts${l.rebounds != null ? `, ${esc(l.rebounds)} reb` : ''}${l.assists != null ? `, ${esc(l.assists)} ast` : ''}</span>`;
}

function teamRow(g, side) {
  const t = g[side];
  const other = g[side === 'home' ? 'away' : 'home'];
  const winning = t.score != null && other.score != null && Number(t.score) > Number(other.score);
  return `<tr class="${winning && g.status === 3 ? 'winner' : ''}">
    <td class="team-cell">${logoImg(t.id, t.tricode)}
      <span class="team-name"><b>${esc(t.tricode)}</b> <span class="muted">${esc([t.city, t.name].filter(Boolean).join(' ') || t.name || '')}</span></span>
      <span class="record">${esc(record(t))}</span>
      ${t.inBonus === true || t.inBonus === '1' ? '<span class="bonus">BONUS</span>' : ''}
    </td>
    ${periodsCells(g, t)}
  </tr>`;
}

function gameRow(g) {
  const meta = [];
  if (g.seriesText) meta.push(esc(g.seriesText));
  if (g.tv && g.tv.length) meta.push(`📺 ${esc([...new Set(g.tv)].join(', '))}`);
  if (g.arena) meta.push(esc(g.arena));
  const leaders = [leaderLine(g, 'away'), leaderLine(g, 'home')].filter(Boolean).join(' · ');
  return `<div class="game-row" data-game="${esc(g.id)}" tabindex="0" role="button">
    <div class="row-status">${statusBadgeFor(g)}</div>
    <table class="row-table"><thead><tr><th class="team-cell"></th>${periodsHeader(g)}</tr></thead>
      <tbody>${teamRow(g, 'away')}${teamRow(g, 'home')}</tbody></table>
    <div class="row-meta">${meta.length ? `<div class="row-sub">${meta.join(' · ')}</div>` : ''}${leaders ? `<div class="row-sub leaders">${leaders}</div>` : ''}</div>
  </div>`;
}

function gameCard(g) {
  return `<div class="game-card" data-game="${esc(g.id)}" tabindex="0" role="button">
    <div class="card-status">${statusBadgeFor(g)}</div>
    <div class="card-teams">
      <div class="card-team">${logoImg(g.away.id, g.away.tricode)}<span>${esc(g.away.tricode)}</span><b>${esc(g.away.score ?? '')}</b></div>
      <div class="card-team">${logoImg(g.home.id, g.home.tricode)}<span>${esc(g.home.tricode)}</span><b>${esc(g.home.score ?? '')}</b></div>
    </div>
    <div class="card-meta">${esc(g.seriesText || g.tv?.[0] || '')}</div>
  </div>`;
}

function renderGames(games, emptyMsg) {
  const filtered = filterGames(games);
  const strip = el('gamesStrip');
  const grid = el('gamesGrid');
  if (!games.length) {
    const html = `<div class="card">${emptyMsg}</div>`;
    strip.innerHTML = html; grid.innerHTML = html; return;
  }
  if (!filtered.length) {
    const html = `<div class="card">No games match “${esc(el('gameSearch').value)}”.</div>`;
    strip.innerHTML = html; grid.innerHTML = html; return;
  }
  strip.innerHTML = filtered.map(gameRow).join('');
  grid.innerHTML = filtered.map(gameCard).join('');
  strip.classList.toggle('hidden', state.view !== 'strip');
  grid.classList.toggle('hidden', state.view !== 'cards');
  document.querySelectorAll('[data-game]').forEach((node) => {
    node.addEventListener('click', () => openGame(node.dataset.game));
    node.addEventListener('keydown', (e) => { if (e.key === 'Enter') openGame(node.dataset.game); });
  });
}

function filterGames(games) {
  const q = (el('gameSearch').value || '').trim().toLowerCase();
  if (!q) return games;
  return games.filter((g) => {
    const hay = [g.home.tricode, g.away.tricode, g.home.name, g.away.name,
                 g.leaders?.home?.name, g.leaders?.away?.name, g.id, g.seriesText]
      .filter(Boolean).join(' ').toLowerCase();
    return hay.includes(q);
  });
}

/* --------------------------------------------------------------- live view */

async function loadLive({ quiet = false } = {}) {
  if (!quiet) setStatus('loading', 'Reading published live feed…', PUBLISHED.live);
  try {
    const payload = await getJSON(PUBLISHED.live, { noCache: true });
    const sb = payload.scoreboard?.scoreboard || payload.official?.scoreboard || {};
    const games = (sb.games || []).map(normLiveGame);
    state.live = { games, feedDate: sb.gameDate, fetchedAtUtc: payload._sync?.fetchedAtUtc, source: payload._sync?.source };
    state.date = null;
    el('viewDatePill').textContent = 'Live feed (today)';
    const liveCount = games.filter((g) => g.status === 2).length;
    const when = state.live.feedDate || 'unknown date';
    const age = payload._sync?.fetchedAtUtc ? timeAgo(payload._sync.fetchedAtUtc) : 'unknown';
    const msg = games.length
      ? `${games.length} games published for ${when}${liveCount ? ` · ${liveCount} in progress` : ''}`
      : `No games scheduled for ${when} (official feed)`;
    if (!quiet) setStatus(liveCount ? 'live' : 'ok', msg, `snapshot ${age} · source cdn.nba.com live feed`);
    renderGames(games, `<h3>No games today</h3><p>The official feed (${esc(OFFICIAL.live)}) reports no games for ${esc(when)}.
      Use <a href="#schedule">Schedule</a> to look ahead, or <a href="#date">By date</a> for a past night.</p>`);
    loadPlays();
    return games;
  } catch (e) {
    if (!quiet) setStatus('error', `Could not read ${PUBLISHED.live}`, String(e.message || e));
    const html = `<div class="card"><h3>No published live feed yet</h3>
      <p>The pipeline has not committed <code>${esc(PUBLISHED.live)}</code> in this checkout. Every fetch it makes is recorded in
      <a href="${PUBLISHED.index}">data/index.json</a> and <a href="data/verification/sync-log.json">sync-log.json</a>.</p></div>`;
    el('gamesStrip').innerHTML = html;
    el('gamesGrid').innerHTML = html;
    return [];
  }
}

async function loadPlays() {
  try {
    const plays = await getJSON(PUBLISHED.plays, { noCache: true });
    state.plays = plays;
  } catch (e) { state.plays = null; }
}

/* ------------------------------------------------------------- date views */

async function loadDate(dateStr) {
  state.date = dateStr;
  el('viewDatePill').textContent = `Date view: ${dateStr}`;
  el('dateInput').value = dateStr;
  el('nbaGamesLink').href = OFFICIAL.gamesPage(dateStr);
  const box = el('dateResult');
  box.innerHTML = `<div class="loading">Reading archived scoreboard for ${esc(dateStr)}…</div>`;
  setStatus('loading', `Reading published scoreboard for ${dateStr}…`, PUBLISHED.date(dateStr));
  try {
    const payload = await getJSON(PUBLISHED.date(dateStr));
    const games = (payload.games || []).map(normCardGame);
    state.dateResultGames = games;
    if (!games.length) {
      box.innerHTML = `<div class="card"><h3>No games on ${esc(dateStr)}</h3>
        <p>The official page for that date lists no games (offseason or travel day).</p>
        <p class="note">Checked: <a href="${esc(OFFICIAL.gamesPage(dateStr))}" target="_blank" rel="noopener">${esc(OFFICIAL.gamesPage(dateStr))}</a></p></div>`;
      renderGames([], `<h3>No games on ${esc(dateStr)}</h3>`);
      return;
    }
    box.innerHTML = gameRowList(games, dateStr, payload._sync);
    bindGameRows(box);
    renderGames(games, '');
    el('liveNote').textContent = `Showing the archived official scoreboard for ${dateStr}. Rows are copied from nba.com/games for that night.`;
    setStatus('ok', `${games.length} games archived for ${dateStr}`, `source ${OFFICIAL.gamesPage(dateStr)}`);
  } catch (e) {
    box.innerHTML = `<div class="card"><h3>${esc(dateStr)} is not archived yet</h3>
      <p>The pipeline stores official date pages as it works backwards through the calendar, so this date may simply not have been reached yet.
      You can always read it directly on NBA.com:</p>
      <p><a class="btn secondary" href="${esc(OFFICIAL.gamesPage(dateStr))}" target="_blank" rel="noopener">Open nba.com/games?date=${esc(dateStr)} ↗</a></p>
      <p class="note">Requested file: <code>${esc(PUBLISHED.date(dateStr))}</code> — ${esc(String(e.message || e))}</p></div>`;
    setStatus('warn', `${dateStr} not archived yet`, 'open the official page, or run the backfill workflow for that date');
  }
}

function gameRowList(games, dateStr, sync) {
  return `<div class="card">
    <h3>${esc(dateStr)} — ${games.length} game${games.length === 1 ? '' : 's'}</h3>
    ${games.map(gameRow).join('')}
    <p class="note">Source: <a href="${esc(OFFICIAL.gamesPage(dateStr))}" target="_blank" rel="noopener">nba.com/games?date=${esc(dateStr)}</a>${sync?.fetchedAtUtc ? ` · captured ${esc(sync.fetchedAtUtc)} UTC` : ''}</p>
  </div>`;
}

function bindGameRows(root) {
  root.querySelectorAll('[data-game]').forEach((node) => {
    node.addEventListener('click', () => openGame(node.dataset.game));
  });
}

/* ------------------------------------------------------------- game detail */

function findGame(gameId) {
  const pools = [];
  if (state.live?.games) pools.push(...state.live.games);
  if (state.schedule?.games) pools.push(...state.schedule.games.map(normScheduleGame));
  if (state.dateResultGames) pools.push(...state.dateResultGames);
  return pools.find((g) => String(g.id) === String(gameId)) || { id: gameId, home: {}, away: {}, leaders: {} };
}

async function openGame(gameId) {
  const g = findGame(gameId);
  state.selectedGame = g;
  el('gameDetail').classList.remove('hidden');
  el('detailTitle').textContent = `${g.away?.tricode || ''} @ ${g.home?.tricode || ''} · ${gameId}`;
  el('detailEndpoints').innerHTML = `Official endpoints: <code>${esc(OFFICIAL.box(gameId))}</code> · <code>${esc(OFFICIAL.pbp(gameId))}</code>
    ${g.shareUrl ? ` · <a href="${esc(g.shareUrl)}" target="_blank" rel="noopener">NBA.com game page ↗</a>` : ''}`;
  el('boxscoreContent').innerHTML = '<div class="loading">Loading archived box score…</div>';
  el('playbyplayContent').innerHTML = '<div class="loading">Loading archived play-by-play…</div>';
  renderInfoPanel(g);
  el('gameDetail').scrollIntoView?.({ behavior: 'smooth', block: 'start' });

  try {
    const box = await getJSON(PUBLISHED.box(gameId));
    state.boxscore = box;
    renderBoxscore(box);
  } catch (e) {
    state.boxscore = null;
    el('boxscoreContent').innerHTML = boxscoreFallback(g, e);
  }
  try {
    const pbp = await getJSON(PUBLISHED.pbp(gameId));
    state.playbyplay = pbp;
    renderPlayByPlay(pbp);
  } catch (e) {
    state.playbyplay = null;
    el('playbyplayContent').innerHTML = `<div class="card"><h3>Play-by-play not archived for this game</h3>
      <p>${esc(String(e.message || e))}</p>
      <p>The pipeline archives official play-by-play for games from 2019-20 onwards once they are final, and keeps today's games refreshed in
      <a href="${PUBLISHED.plays}">data/live/plays.json</a>.</p>
      <p><a href="${esc(OFFICIAL.pbp(gameId))}" target="_blank" rel="noopener">Open the official feed ↗</a></p></div>`;
  }
}

function boxscoreFallback(g, err) {
  const home = g.home || {}, away = g.away || {};
  const rows = (t) => (t.periods || []).map((s, i) => `<td>${esc(s)}</td>`).join('');
  return `<div class="card"><h3>Box score not archived yet</h3>
    <p>${esc(String(err.message || err))}</p>
    <table class="box-table"><thead><tr><th>Team</th>${(home.periods || []).map((_, i) => `<th>${esc(periodName(i + 1))}</th>`).join('')}<th>T</th></tr></thead>
      <tbody>
        <tr><td>${esc(away.tricode || '')}</td>${rows(away)}<td><b>${esc(away.score ?? '')}</b></td></tr>
        <tr><td>${esc(home.tricode || '')}</td>${rows(home)}<td><b>${esc(home.score ?? '')}</b></td></tr>
      </tbody></table>
    <p class="note">Quarter scores and leaders above come from the official date page. Full player statistics and play-by-play are archived for games from 2019-20 onward.</p></div>`;
}

function statCell(player, key) {
  const st = player.statistics || {};
  const v = st[key];
  return v == null ? '' : esc(v);
}

function renderBoxscore(box) {
  const g = box.game || box;
  const teamTable = (side) => {
    const t = side === 'home' ? g.homeTeam : g.awayTeam;
    if (!t) return '';
    const players = t.players || [];
    const starters = players.filter((p) => p.starter === true || p.played === '1' && p.starter);
    const rows = players.map((p) => `<tr>
        <td class="player-cell">
          <img class="headshot" loading="lazy" alt="" src="${headshot(p.personId)}" onerror="this.style.display='none'">
          <span>${esc(p.name || p.nameI || '')}${p.jerseyNum ? ` <span class="muted">#${esc(p.jerseyNum)}</span>` : ''}</span>
          ${p.position ? `<span class="muted">${esc(p.position)}</span>` : ''}
        </td>
        <td>${statCell(p, 'minutes')}</td><td><b>${statCell(p, 'points')}</b></td>
        <td>${statCell(p, 'reboundsTotal')}</td><td>${statCell(p, 'assists')}</td>
        <td>${statCell(p, 'steals')}</td><td>${statCell(p, 'blocks')}</td>
        <td>${statCell(p, 'turnovers')}</td>
        <td>${statCell(p, 'fieldGoalsMade')}-${statCell(p, 'fieldGoalsAttempted')}</td>
        <td>${statCell(p, 'threePointersMade')}-${statCell(p, 'threePointersAttempted')}</td>
        <td>${statCell(p, 'freeThrowsMade')}-${statCell(p, 'freeThrowsAttempted')}</td>
        <td>${statCell(p, 'plusMinusPoints')}</td>
      </tr>`).join('');
    const st = t.statistics || {};
    return `<div class="card"><h3>${esc(t.teamTricode)} — ${esc([t.teamCity, t.teamName].filter(Boolean).join(' '))} <span class="muted">${esc(t.score ?? '')}</span></h3>
      <div class="table-wrap"><table class="box-table">
        <thead><tr><th>Player</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th><th>TO</th><th>FG</th><th>3P</th><th>FT</th><th>+/-</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="note">Team: ${esc(st.fieldGoalsMade ?? '')}-${esc(st.fieldGoalsAttempted ?? '')} FG ·
        ${esc(st.threePointersMade ?? '')}-${esc(st.threePointersAttempted ?? '')} 3P ·
        ${esc(st.freeThrowsMade ?? '')}-${esc(st.freeThrowsAttempted ?? '')} FT ·
        ${esc(st.reboundsTotal ?? '')} REB · ${esc(st.assists ?? '')} AST · ${esc(st.turnovers ?? '')} TO</p></div>`;
  };
  el('boxscoreContent').innerHTML = `<div class="card meta-card">
      <div><b>${esc(g.statusText || '')}</b>${g.gameEt ? ` · ${esc(new Date(g.gameEt).toLocaleString())}` : ''}</div>
      <div>${esc([(g.arena || {}).arenaName, (g.arena || {}).arenaCity].filter(Boolean).join(', '))}${g.attendance ? ` · attendance ${esc(g.attendance)}` : ''}</div>
      <div class="note">Source: <a href="${esc(box._sync?.source || '')}" target="_blank" rel="noopener">${esc(box._sync?.source || 'official box score')}</a> · captured ${esc(box._sync?.fetchedAtUtc || '')} UTC · sha256 ${esc((box._sync?.contentHash || '').slice(0, 12))}…</div>
    </div>${teamTable('away')}${teamTable('home')}`;
}

function renderPlayByPlay(pbp) {
  const actions = pbp.actions || [];
  const periods = [...new Set(actions.map((a) => a.period))].sort((a, b) => a - b);
  el('periodFilter').innerHTML = '<option value="all">All periods</option>' + periods.map((p) => `<option value="${p}">${esc(periodName(p))}</option>`).join('');
  const teams = [...new Set(actions.map((a) => a.teamTricode).filter(Boolean))];
  el('teamFilter').innerHTML = '<option value="all">Both teams</option>' + teams.map((t) => `<option value="${t}">${esc(t)}</option>`).join('');
  paintActions(actions);
}

function paintActions(actions) {
  const period = el('periodFilter').value;
  const team = el('teamFilter').value;
  const q = (el('searchActions').value || '').toLowerCase();
  const rows = actions.filter((a) => (period === 'all' || String(a.period) === period)
    && (team === 'all' || a.teamTricode === team)
    && (!q || String(a.description || '').toLowerCase().includes(q)));
  el('playbyplayContent').innerHTML = `<div class="card">
    <div class="note">${rows.length} of ${actions.length} actions${state.playbyplay?._sync?.source ? ` · source <a href="${esc(state.playbyplay._sync.source)}" target="_blank" rel="noopener">official play-by-play</a>` : ''}</div>
    <div class="pbp">${rows.map((a) => `<div class="pbp-row">
      <span class="pbp-time">${esc(periodName(a.period))} ${esc(parseClock(a.clock))}</span>
      <span class="pbp-team ${esc(a.teamTricode || '')}">${esc(a.teamTricode || '')}</span>
      <span class="pbp-desc">${esc(a.description || '')}</span>
      <span class="pbp-score">${a.scoreAway != null ? `${esc(a.scoreAway)}–${esc(a.scoreHome)}` : ''}</span>
    </div>`).join('')}</div></div>`;
}

function renderInfoPanel(g) {
  const rows = [
    ['Game ID', g.id],
    ['Date', g.gameEt ? new Date(g.gameEt).toLocaleString() : (g.dateEst || '—')],
    ['Season', g.seasonYear ? `${g.seasonYear} ${g.seasonType || ''}` : '—'],
    ['Series', g.seriesText || '—'],
    ['Arena', g.arena || '—'],
    ['TV', (g.tv && g.tv.length) ? [...new Set(g.tv)].join(', ') : '—'],
  ];
  el('infoContent').innerHTML = `<div class="card"><table class="kv">${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table>
    <p class="note">Official box score: <a href="${esc(OFFICIAL.box(g.id))}" target="_blank" rel="noopener">${esc(OFFICIAL.box(g.id))}</a></p>
    <p class="note">Official play-by-play: <a href="${esc(OFFICIAL.pbp(g.id))}" target="_blank" rel="noopener">${esc(OFFICIAL.pbp(g.id))}</a></p></div>`;
}

/* --------------------------------------------------------------- schedule */

async function loadSchedule(season) {
  el('scheduleContent').innerHTML = '<div class="loading">Reading published schedule…</div>';
  try {
    const payload = await getJSON(PUBLISHED.schedule(season));
    const games = (payload.games || []).map(normScheduleGame);
    state.schedule = { season, games };
    const q = (el('scheduleFilter').value || '').toLowerCase();
    const shown = q ? games.filter((g) => `${g.away.tricode} ${g.home.tricode} ${g.away.name} ${g.home.name} ${g.id}`.toLowerCase().includes(q)) : games;
    const byDate = {};
    shown.forEach((g) => { (byDate[g.dateEst] = byDate[g.dateEst] || []).push(g); });
    const dates = Object.keys(byDate).sort();
    el('scheduleContent').innerHTML = dates.length ? dates.map((d) => `<div class="schedule-day">
        <h4>${esc(new Date(`${d}T12:00:00Z`).toDateString())}</h4>
        ${byDate[d].map((g) => `<div class="schedule-row" data-game="${esc(g.id)}">
          <span class="sched-time">${esc(g.gameEt ? new Date(g.gameEt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '')}</span>
          <span class="sched-teams">${esc(g.away.tricode)} @ ${esc(g.home.tricode)}</span>
          <span class="sched-status">${esc(g.gameStatusText || '')}${g.gameLabel ? ` · ${esc(g.gameLabel)}` : ''}</span>
          <span class="sched-arena muted">${esc(g.arena || '')}</span>
          ${g.tv.length ? `<span class="sched-tv">📺 ${esc(g.tv.join(', '))}</span>` : ''}
        </div>`).join('')}`).join('') : '<div class="card">No games match.</div>';
    bindGameRows(el('scheduleContent'));
    setStatus('ok', `${games.length} games in the ${season} official schedule`, `source ${OFFICIAL.schedule}`);
  } catch (e) {
    el('scheduleContent').innerHTML = `<div class="card">Could not read <code>${esc(PUBLISHED.schedule(season))}</code> — ${esc(String(e.message || e))}</div>`;
  }
}

/* -------------------------------------------------------------------- init */

function initViews() {
  document.querySelectorAll('.tab[data-tab]').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab[data-tab]').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
      tab.classList.add('active');
      el(`${tab.dataset.tab}Panel`).classList.add('active');
    });
  });
  el('stripViewBtn').addEventListener('click', () => setView('strip'));
  el('cardsViewBtn').addEventListener('click', () => setView('cards'));
  el('gameSearch').addEventListener('input', () => {
    if (state.date && state.dateResultGames) renderGames(state.dateResultGames, '');
    else if (state.live) renderGames(state.live.games, '');
  });
  el('closeDetail').addEventListener('click', () => el('gameDetail').classList.add('hidden'));
  el('refreshBtn').addEventListener('click', () => { refreshAll(); });
  el('autoRefresh').addEventListener('change', () => { state.timer ? stopTimer() : startTimer(); });
  el('prevDayBtn').addEventListener('click', () => shiftDate(-1));
  el('nextDayBtn').addEventListener('click', () => shiftDate(1));
  el('todayBtn').addEventListener('click', () => backToLive());
  el('loadByDateBtn').addEventListener('click', () => el('dateInput').value && loadDate(el('dateInput').value));
  el('yesterdayBtn').addEventListener('click', () => {
    const d = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
    loadDate(d);
  });
  el('lastFinalsBtn').addEventListener('click', () => loadDate('2024-06-17'));
  el('dateInput').addEventListener('change', (e) => { el('nbaGamesLink').href = OFFICIAL.gamesPage(e.target.value); });
  el('scheduleFilter').addEventListener('input', () => state.schedule && loadSchedule(state.season));
  el('seasonSelect').addEventListener('change', (e) => { state.season = e.target.value; loadSchedule(state.season); });
  ['periodFilter', 'teamFilter'].forEach((id) => el(id).addEventListener('change', () => state.playbyplay && paintActions(state.playbyplay.actions || [])));
  el('searchActions').addEventListener('input', () => state.playbyplay && paintActions(state.playbyplay.actions || []));
  document.querySelectorAll('.nav a').forEach((a) => a.addEventListener('click', () => {
    document.querySelectorAll('.nav a').forEach((n) => n.classList.remove('active'));
    a.classList.add('active');
  }));
}

function setView(view) {
  state.view = view;
  el('stripViewBtn').classList.toggle('active', view === 'strip');
  el('cardsViewBtn').classList.toggle('active', view === 'cards');
  el('stripViewBtn').setAttribute('aria-pressed', view === 'strip');
  el('cardsViewBtn').setAttribute('aria-pressed', view === 'cards');
  if (state.date && state.dateResultGames) renderGames(state.dateResultGames, '');
  else if (state.live) renderGames(state.live.games, '');
}

function shiftDate(days) {
  const base = state.date ? new Date(`${state.date}T12:00:00Z`) : new Date();
  base.setUTCDate(base.getUTCDate() + days);
  loadDate(base.toISOString().slice(0, 10));
}

function backToLive() {
  state.date = null;
  el('dateResult').innerHTML = '';
  el('viewDatePill').textContent = 'Live feed (today)';
  el('liveNote').textContent = 'Rows show the official quarter-by-quarter line: status & clock, both teams with records, Q1–Q4 + OT, totals, top scorers, TV.';
  loadLive();
}

function startTimer() { stopTimer(); state.timer = setInterval(() => refreshAll(true), 60000); }
function stopTimer() { if (state.timer) clearInterval(state.timer); state.timer = null; }

async function refreshAll(quiet = false) {
  state.index = await getIndex();
  if (!state.date) await loadLive({ quiet });
  updateFreshness();
}

async function getIndex() {
  try { return await getJSON(PUBLISHED.index, { noCache: true }); } catch (e) { return state.index; }
}

async function init() {
  initViews();
  state.index = await getIndex();
  if (state.index) {
    const seasons = Object.keys(state.index.schedules || {});
    el('seasonSelect').innerHTML = seasons.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join('') || '<option>none published</option>';
    if (seasons.length) {
      state.season = seasons[seasons.length - 1];
      el('seasonSelect').value = state.season;
      loadSchedule(state.season);
    } else {
      el('scheduleContent').innerHTML = '<div class="card">No schedule published yet.</div>';
    }
    const dates = Object.keys(state.index.scoreboards || {}).sort().reverse();
    el('archiveCount').textContent = `${dates.length} dates`;
    el('archiveList').innerHTML = dates.slice(0, 120).map((d) => `<button class="chip" data-date="${esc(d)}">${esc(d)}${state.index.scoreboards[d].gameCount ? ` · ${state.index.scoreboards[d].gameCount}g` : ''}</button>`).join('');
    el('archiveList').querySelectorAll('[data-date]').forEach((b) => b.addEventListener('click', () => loadDate(b.dataset.date)));
  }
  el('dateInput').value = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
  el('nbaGamesLink').href = OFFICIAL.gamesPage(el('dateInput').value);
  await loadLive();
  updateFreshness();
  startTimer();
  console.info('[nba-scoreboard] relay:', state.relay || 'none (using published snapshots)');
}

document.addEventListener('DOMContentLoaded', init);

/* Expose a few handles for debugging from the browser console and for UI tests. */
window.NBAScoreboard = { loadDate, openGame, loadLive, loadSchedule, backToLive, state };
