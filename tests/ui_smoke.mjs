/* jsdom regression suite: the real page against committed NBA data and explicit
 * synthetic in-progress responses. Synthetic data is ONLY in memory in this test.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => JSON.parse(fs.readFileSync(path.join(ROOT, name), 'utf8'));
const index = read('data/index.json');
const live = read('data/live/scoreboard.json');
const archived = read('data/scoreboard/2024-11-04.json');
const old = read('data/scoreboard/1996-06-16.json');
const future = read('data/scoreboard/2026-10-03.json');
const box = read('data/games/0022400154/boxscore.json');
const pbp = read('data/games/0022400154/playbyplay.json');
const OFFICIAL = 'https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json';
const dom = new JSDOM(fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8'), {
  runScripts: 'outside-only', url: 'https://buffedlizard55-lab.github.io/NBASCOREBOARD/', pretendToBeVisual: true,
});
const { window } = dom, doc = window.document;
const $ = (id) => doc.getElementById(id);
const text = (id) => ($(id)?.textContent || '').replace(/\s+/g, ' ').trim();
const errors = [];
window.addEventListener('error', (e) => errors.push(e.message));
window.scrollTo = () => {};
window.console.info = () => {};
const wait = async (condition, label) => {
  for (let attempt = 0; attempt < 150; attempt++) {
    if (condition()) return;
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error(`Timed out: ${label}`);
};
let mockSnapshot = null, mockIndex = null, mockBox = null, mockPbp = null, directAllowed = false;
window.fetch = async (url) => {
  const target = String(url);
  const pathname = target.replace(/^https?:\/\/[^/]+\/NBASCOREBOARD\//, '').split('?')[0];
  let data;
  if (target.startsWith('https://cdn.nba.com/')) {
    if (!directAllowed) return { ok: false, status: 403 };
    if (target.startsWith(OFFICIAL)) data = mockSnapshot.scoreboard;
    else if (target.includes('boxscore_0022400154')) data = mockBox;
    else if (target.includes('playbyplay_0022400154')) data = { game: { gameId: pbp.gameId, actions: pbp.actions } };
    else return { ok: false, status: 404 };
  } else if (pathname === 'data/live/scoreboard.json' && mockSnapshot) data = mockSnapshot;
  else if (pathname === 'data/index.json' && mockIndex) data = mockIndex;
  else if (pathname === 'data/games/0022400154/boxscore.json' && mockBox) data = mockBox;
  else if (pathname === 'data/games/0022400154/playbyplay.json' && mockPbp) data = mockPbp;
  else {
    const file = path.resolve(ROOT, pathname);
    if (!file.startsWith(ROOT + path.sep) || !fs.existsSync(file)) return { ok: false, status: 404 };
    data = read(pathname);
  }
  return { ok: true, status: 200, async json() { return structuredClone(data); } };
};
window.eval(fs.readFileSync(path.join(ROOT, 'app.js'), 'utf8'));
await wait(() => window.NBAScoreboard.state.live && window.NBAScoreboard.state.schedule, 'initial NBA data');
const app = window.NBAScoreboard;
const checks = [];
const check = (label, okay, context = '') => checks.push({ label, okay: !!okay, context });
const click = (id) => $(id).dispatchEvent(new window.Event('click', { bubbles: true }));
const change = (id) => $(id).dispatchEvent(new window.Event('change', { bubbles: true }));
const input = (id) => $(id).dispatchEvent(new window.Event('input', { bubbles: true }));

// Pass 1: a static Pages reader should not mistake snapshots for a direct stream.
check('feed date visible with ET label', text('liveDatePill').includes(`${live.feedDate} (ET)`), text('liveDatePill'));
check('capture time shown, not a guessed current score', text('provLive').length > 5 && text('freshnessPill').includes('captured'));
check('fetch evidence and NBA data links are present', doc.querySelector('#sources a[href="data/verification/sync-log.json"]')
  && doc.querySelector('#sources a[href="https://www.nba.com/games?date=1996-06-16"]'));
check('only game dates offered as archive shortcuts', $('archiveList').querySelectorAll('button').length ===
  Object.values(index.scoreboards).filter((s) => s.gameCount > 0).length);
check('schedule is bounded to 7 game days by default', $('scheduleContent').querySelectorAll('.schedule-day').length === 7);
$('scheduleRange').value = 'all'; change('scheduleRange');
check('full official season is available on request', $('scheduleContent').querySelectorAll('.schedule-day').length > 100);
$('scheduleRange').value = 'next'; change('scheduleRange');
$('scheduleFilter').value = 'Lakers'; input('scheduleFilter');
check('schedule team search reduces dates', $('scheduleContent').querySelectorAll('.schedule-day').length <= 7
  && $('scheduleContent').textContent.includes('LAL'));
$('scheduleFilter').value = ''; input('scheduleFilter');

// A missing date never asserts there were no games, and cannot escape data/.
await app.loadDate('1985-06-09');
check('unarchived date not mislabelled no games', /not been captured/.test(text('dateResult'))
  && !/No games on 1985/.test(text('gamesStrip')), text('dateResult'));
check('NBA.com review link for missing date', $('dateResult').innerHTML.includes('nba.com/games?date=1985-06-09'));
check('path traversal / invalid calendar dates rejected', (await app.loadDate('../index')) === false
  && text('dateResult').includes('valid YYYY-MM-DD') && (await app.loadDate('2026-02-30')) === false);

await app.loadDate('2024-11-04');
check('15 archived NBA.com game cards rendered', $('gamesStrip').querySelectorAll('.game-row').length === archived.gameCount);
check('score on card matches committed official digest', $('gamesStrip').textContent.includes('116')
  && $('gamesStrip').textContent.includes('114'));
check('historic source URL visible', $('dateResult').innerHTML.includes('nba.com/games?date=2024-11-04'));
$('gameSearch').value = 'Cavaliers'; input('gameSearch');
check('team search filters score rows', $('gamesStrip').querySelectorAll('.game-row').length >= 1
  && $('gamesStrip').querySelectorAll('.game-row').length < archived.gameCount);
$('gameSearch').value = ''; input('gameSearch');
click('cardsViewBtn');
check('cards toggle is accessible', $('cardsViewBtn').getAttribute('aria-pressed') === 'true'
  && $('gamesGrid').querySelectorAll('.game-card').length === archived.gameCount);
click('stripViewBtn');
await app.openGame('0022400154');
check('selected game opens correct away/home (not a stale season-schedule row)', text('detailTitle').includes('MIL @ CLE'));
check('player stats render from archived NBA box score', $('boxscoreContent').textContent.includes('Darius Garland')
  && $('boxscoreContent').textContent.includes(String(box.game.homeTeam.score)));
const count = $('playbyplayContent').querySelectorAll('.pbp-row').length;
check('complete official PBP displayed with source link', count === pbp.actions.length &&
  $('playbyplayContent').innerHTML.includes('cdn.nba.com/static/json/liveData/playbyplay'), `${count} vs ${pbp.actions.length}`);
check('latest action shown first', $('playbyplayContent').querySelector('.pbp-row')?.textContent.includes('Game End'));
click('pbpOrderBtn');
check('oldest first toggle works', $('playbyplayContent').querySelector('.pbp-row')?.textContent.includes('Period Start'));
$('periodFilter').value = '4'; change('periodFilter');
check('Q4 filter narrows plays', $('playbyplayContent').querySelectorAll('.pbp-row').length > 0
  && $('playbyplayContent').querySelectorAll('.pbp-row').length < count);
$('periodFilter').value = 'all'; change('periodFilter');
$('searchActions').value = '3PT'; input('searchActions');
check('text filter finds official play descriptions', $('playbyplayContent').querySelectorAll('.pbp-row').length > 0
  && $('playbyplayContent').querySelectorAll('.pbp-row').length < count);
$('searchActions').value = ''; input('searchActions');

await app.loadDate('1996-06-16');
check('older Finals date has official scores, not fabricated stats', text('gamesStrip').includes('75')
  && text('gamesStrip').includes('87') && old.gameCount === 1);
await app.openGame('0049500068');
check('older game reports unavailable detailed files', text('boxscoreContent').includes('Player stats unavailable')
  && text('playbyplayContent').includes('unavailable here'));
await app.loadDate('2026-10-03');
check('future preseason matchup is available', text('gamesStrip').includes('MIA') && future.gameCount === 1);
check('pregame scores are not displayed as actual 0–0', $('gamesStrip').querySelector('.total')?.textContent === 'T'
  && [...$('gamesStrip').querySelectorAll('td.total')].every((e) => e.textContent === '—'));
check('correct ET tip text and local converted time visible', text('gamesStrip').includes('7:00 pm ET')
  && text('gamesStrip').includes('Oct'));
await app.loadDate('2026-10-04');
check('not-yet-archived date falls back to official season schedule', text('dateResult').includes('published season schedule'));

// A late response for an older date or game must never overwrite a newer choice.
const originalFetch = window.fetch;
let releaseDate, dateStarted = false;
const delayedDate = new Promise((r) => { releaseDate = r; });
window.fetch = async (url) => {
  if (String(url).includes('data/scoreboard/2024-11-04.json')) { dateStarted = true; await delayedDate; }
  return originalFetch(url);
};
const oldRequest = app.loadDate('2024-11-04');
await wait(() => dateStarted, 'slow date request started');
await app.loadDate('1996-06-16');
releaseDate(); await oldRequest;
check('out-of-order date response cannot replace a newer date', text('viewDatePill').includes('1996-06-16')
  && $('gamesStrip').querySelectorAll('.game-row').length === 1);
window.fetch = originalFetch;
await app.loadDate('2024-11-04');
let releaseBox, boxStarted = false;
const delayedBox = new Promise((r) => { releaseBox = r; });
window.fetch = async (url) => {
  if (String(url).includes('data/games/0022400154/boxscore.json')) { boxStarted = true; await delayedBox; }
  return originalFetch(url);
};
const oldGame = app.openGame('0022400154');
await wait(() => boxStarted, 'slow game request started');
await app.openGame('0022400155');
releaseBox(); await oldGame;
check('out-of-order game detail cannot replace a newer game', text('detailTitle').includes('0022400155')
  && text('boxscoreContent').includes('Wizards') && !text('boxscoreContent').includes('Cavaliers'));
window.fetch = originalFetch;

// Synthetic live case: official-shaped samples from an archived NBA game, changed
// IN MEMORY to an in-progress state. This tests the user journey without guessing
// whether actual games are underway on the day the test runs.
const id = '0022400154';
const now = new Date().toISOString();
const liveGame = {
  gameId: id, gameStatus: 2, gameStatusText: 'Q4', gameClock: 'PT02M12.00S', period: 4,
  gameEt: now, homeTeam: box.game.homeTeam, awayTeam: box.game.awayTeam,
  gameLeaders: { homeLeaders: { name: 'Darius Garland', points: '39' } },
};
mockSnapshot = { _sync: { source: OFFICIAL, fetchedAtUtc: now },
  scoreboard: { scoreboard: { gameDate: new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()).replace(/\//g, '-'), games: [liveGame] } } };
// Use the app's NBA date from the committed feed if the test runner is in another
// calendar year; the in-memory game is synthetic and is never written to data/.
mockSnapshot.scoreboard.scoreboard.gameDate = live.feedDate;
mockIndex = structuredClone(index);
mockIndex.heartbeat = { lastCheckedUtc: now, lastSuccessfulLiveUtc: now, lastResult: 'OK', mode: 'live' };
mockIndex.live = { ...index.live, fetchedAtUtc: now, feedDate: live.feedDate, gameCount: 1, liveCount: 1 };
mockIndex.issues = [];
mockBox = structuredClone(box); mockBox.game.gameStatus = 2;
mockPbp = structuredClone(pbp);
app.backToLive();
await wait(() => app.state.live?.games.length === 1 && app.state.date === null, 'synthetic live game');
check('in-progress scoreboard has period, clock and NBA team scores', text('gamesStrip').includes('LIVE')
  && text('gamesStrip').includes('Q4') && text('gamesStrip').includes('2:12'));
await app.openGame(id);
check('live game has player box score BEFORE final', text('boxscoreContent').includes('Darius Garland'));
check('live game has all actions, not last 80', $('playbyplayContent').querySelectorAll('.pbp-row').length === pbp.actions.length);
mockSnapshot._sync.fetchedAtUtc = new Date(Date.now() - 2 * 3600 * 1000).toISOString();
await app.loadLive({ quiet: true });
check('old game state is labelled LAST SEEN LIVE even if the last NBA check is recent',
  app.state.isStale && text('gamesStrip').includes('LAST SEEN LIVE') && !$('freshnessAlert').classList.contains('hidden'));
mockSnapshot._sync.fetchedAtUtc = now;
await app.loadLive({ quiet: true });
const dateBefore = app.state.live.feedDate;
mockSnapshot = { ...mockSnapshot, scoreboard: { scoreboard: { gameDate: dateBefore, games: null } } };
await app.loadLive({ quiet: true });
check('malformed NBA feed cannot turn a game night into zero games', app.state.live.games.length === 1
  && !/No games in the NBA feed/.test(text('gamesStrip')));
mockSnapshot = { ...mockSnapshot, scoreboard: { scoreboard: { gameDate: dateBefore, games: [liveGame] } } };
await app.loadLive({ quiet: true });

// Browser-to-NBA path only enables direct mode when the raw official shape passes.
directAllowed = true;
click('testDirectBtn');
await wait(() => app.state.sourceMode === 'direct', 'direct mode after successful test');
await app.openGame(id);
check('direct raw NBA PBP uses game.actions (not empty)', $('playbyplayContent').querySelectorAll('.pbp-row').length === pbp.actions.length);
check('direct mode has only the NBA CDN as a source (no relay)', text('sourceModePill').includes('cdn.nba.com')
  && !fs.readFileSync(path.join(ROOT, 'app.js'), 'utf8').includes('relayUrl'));
directAllowed = false;
await app.loadLive({ quiet: true });
check('direct failure safely falls back to official published snapshot', app.state.sourceMode === 'snapshot'
  && text('directResult').includes('back to the published snapshot'));

for (const { label, okay, context } of checks) console.log(`${okay ? 'PASS' : 'FAIL'}  ${label}${context ? ` — ${context}` : ''}`);
console.log(`\n${checks.filter((c) => c.okay).length}/${checks.length} checks passed; uncaught errors: ${errors.length}`);
if (errors.length) console.error(errors);
process.exit(checks.some((c) => !c.okay) || errors.length ? 1 : 0);
