/* Headless smoke test: real index.html + app.js, real committed data/ files.
 *
 * Requires Node >= 22 (jsdom 30 pulls an undici build that needs it); the CI workflow
 * pins that version so this never fails for a mysterious reason.
 */
import fs from 'fs';
import path from 'path';

let JSDOM;
try {
  ({ JSDOM } = await import('jsdom'));
} catch (e) {
  console.error(`Could not load jsdom on Node ${process.version}: ${e.message}`);
  console.error('Run this with Node >= 22 (the CI workflow pins it), after: npm --prefix tests install');
  process.exit(1);
}

import { fileURLToPath } from 'url';
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(REPO, 'index.html'), 'utf8');
const appjs = fs.readFileSync(path.join(REPO, 'app.js'), 'utf8');

const errors = [];
const warnings = [];

const index = JSON.parse(fs.readFileSync(path.join(REPO, 'data/index.json'), 'utf8'));
const live = JSON.parse(fs.readFileSync(path.join(REPO, 'data/live/scoreboard.json'), 'utf8'));
const feedDate = live.feedDate;
const archivedDate = index.scoreboards['2024-11-04'] ? '2024-11-04' : Object.keys(index.scoreboards).sort().filter((d) => index.scoreboards[d].gameCount > 1).pop();
const archivedCount = index.scoreboards[archivedDate].gameCount;
// Playoff games are never pruned, so their detail archive is always present.
const detailGame = Object.keys(index.games).filter((g) => g.startsWith('004')).pop() || Object.keys(index.games).pop();

const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://buffedlizard55-lab.github.io/NBASCOREBOARD/', pretendToBeVisual: true });
const { window } = dom;
window.addEventListener('error', (e) => errors.push(`window error: ${e.message}`));

// fetch shim → read from ./data in the repo
window.fetch = async (url) => {
  const rel = String(url).replace(/^https?:\/\/[^/]+\/NBASCOREBOARD\//, '').replace(/^\.\//, '');
  const file = path.join(REPO, rel);
  if (!fs.existsSync(file)) return { ok: false, status: 404, async json() { throw new Error('not found'); } };
  const body = fs.readFileSync(file, 'utf8');
  return { ok: true, status: 200, async json() { return JSON.parse(body); } };
};
window.console.info = () => {};
window.console.warn = (...a) => warnings.push(a.join(' '));
window.scrollTo = () => {};

window.document.addEventListener('DOMContentLoaded', () => {});

// run the app
window.eval(appjs);
// jsdom fires DOMContentLoaded on its own during parse; if init already missed it, call it
await new Promise((r) => setTimeout(r, 900));

const doc = window.document;
const $ = (id) => doc.getElementById(id);
const txt = (id) => ($(id)?.textContent || '').trim().replace(/\s+/g, ' ');
const report = [];
const check = (name, cond, detail = '') => report.push(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? ' — ' + detail : ''}`);

// ---- live view
check('status bar shows the official feed date', txt('statusText').includes(feedDate), txt('statusText'));
check('status meta names the snapshot age', /snapshot/.test(txt('statusMeta')), txt('statusMeta'));
check('freshness pill filled', txt('freshnessPill').startsWith('snapshot'), txt('freshnessPill'));
check('build info shows archived counts', /archived dates: \d+/.test(txt('buildInfo')), txt('buildInfo'));
check('season selector populated', $('seasonSelect').innerHTML.includes(Object.keys(index.schedules).pop()));
check('archive chips rendered', $('archiveList').querySelectorAll('button.chip').length === Object.keys(index.scoreboards).length,
  `${$('archiveList').querySelectorAll('button.chip').length} chips for ${Object.keys(index.scoreboards).length} archived dates`);

// ---- date view (historical)
await window.NBAScoreboard.loadDate(archivedDate);
check('date view status', txt('statusText').includes(`${archivedCount} games archived for ${archivedDate}`), txt('statusText'));
const dateRows = $('dateResult').querySelectorAll('.game-row').length;
check('archived rows rendered', dateRows === archivedCount, `${dateRows} rows for ${archivedCount} games`);
check('row links to the official date page',
  $('dateResult').innerHTML.includes(`nba.com/games?date=${archivedDate}`));

// ---- a pre-2019 date (no box score archive available, quarters only)
await window.NBAScoreboard.loadDate('1996-06-16');
check('1996 date renders', /1 games archived for 1996-06-16/.test(txt('statusText')), txt('statusText'));
check('1996 quarter scores present', $('dateResult').innerHTML.includes('87'));

// ---- a future date (scheduled game, no "Invalid Date")
await window.NBAScoreboard.loadDate('2026-10-03');
const future = $('dateResult').innerHTML;
check('future game renders', future.includes('Raptors') || future.includes('TOR'), '');
check('future game shows the ET tip-off text', future.includes('7:00 pm ET'));
check('future game shows a local time from real UTC', /Oct/.test(future) || /\d+:\d\d/.test(future));

// ---- unarchived date → graceful message + official link
await window.NBAScoreboard.loadDate('1985-06-09');
check('unarchived date explains itself', /not archived yet/.test(txt('statusText')), txt('statusText'));
check('unarchived date links to nba.com',
  $('dateResult').innerHTML.includes('nba.com/games?date=1985-06-09'));
check('stale rows cleared', $('gamesStrip').innerHTML.includes('Nothing archived') || $('gamesStrip').innerHTML.includes('No games'), $('gamesStrip').innerHTML.slice(0, 80));

// ---- game detail: an archived game (playoff games always keep their full detail)
await window.NBAScoreboard.openGame(detailGame);
await new Promise((r) => setTimeout(r, 400));
const box = $('boxscoreContent').innerHTML;
const pbp = $('playbyplayContent').innerHTML;
const archived = index.games[detailGame];
check('box score rendered', box.includes(String(archived.homeScore)) && box.includes(String(archived.awayScore)),
  `${archived.awayScore}-${archived.homeScore} ${archived.away}@${archived.home}`);
check('minutes formatted (not raw ISO)', !/PT\d+M/.test(box), box.match(/PT\d+M[\d.]+S/)?.[0] || '');
const pbpRows = (pbp.match(/pbp-row/g) || []).length;
check('play-by-play rendered', pbpRows > 200, `${pbpRows} rows for ${detailGame}`);
check('pbp shows the official source link', pbp.includes('cdn.nba.com/static/json/liveData/playbyplay'));
// filters
$('periodFilter').value = '4';
$('periodFilter').dispatchEvent(new window.Event('change'));
const q4 = ($('playbyplayContent').innerHTML.match(/pbp-row/g) || []).length;
check('period filter narrows the list', q4 > 0 && q4 < pbpRows, `${q4} Q4 rows of ${pbpRows}`);
$('periodFilter').value = 'all';
$('periodFilter').dispatchEvent(new window.Event('change'));
$('searchActions').value = '3pt';
$('searchActions').dispatchEvent(new window.Event('input'));
const three = ($('playbyplayContent').innerHTML.match(/pbp-row/g) || []).length;
check('text filter narrows the list', three > 0 && three < pbpRows, `${three} rows for “3pt”`);
check('filtered rows mention the query', /3PT/i.test($('playbyplayContent').innerHTML));
$('searchActions').value = '';
$('searchActions').dispatchEvent(new window.Event('input'));

// ---- A game with no archive (1996 finals) must fall back, not crash
await window.NBAScoreboard.openGame('0049500068');
await new Promise((r) => setTimeout(r, 300));
check('unarchived box score falls back to the date row',
  $('boxscoreContent').innerHTML.includes('not archived yet'));
check('unarchived pbp falls back with an official link',
  $('playbyplayContent').innerHTML.includes('cdn.nba.com'));

// ---- schedule
check('schedule rendered', $('scheduleContent').querySelectorAll('.schedule-day').length > 100,
  `${$('scheduleContent').querySelectorAll('.schedule-day').length} days`);
$('scheduleFilter').value = 'Lakers';
$('scheduleFilter').dispatchEvent(new window.Event('input'));
await new Promise((r) => setTimeout(r, 250));
const lakersDays = $('scheduleContent').querySelectorAll('.schedule-day').length;
check('schedule filter works', lakersDays > 0 && lakersDays < 200, `${lakersDays} days with Lakers`);
$('scheduleFilter').value = '';
$('scheduleFilter').dispatchEvent(new window.Event('input'));

// ---- back to live
window.NBAScoreboard.backToLive();
await new Promise((r) => setTimeout(r, 300));
check('back to live works', txt('statusText').includes(feedDate), txt('statusText'));

// ---- direct-access self-test (expected to be blocked from this origin)
$('testDirectBtn').dispatchEvent(new window.Event('click'));
await new Promise((r) => setTimeout(r, 300));
check('direct test reports blocked result', /blocked/.test(txt('directResult')) && /snapshot/.test(txt('sourceModePill')),
  txt('directResult').slice(0, 60));

console.log(report.join('\n'));
console.log('\nconsole warnings:', warnings.length);
console.log('uncaught errors:', errors.length ? errors : 'none');
const failed = report.filter((r) => r.startsWith('FAIL'));
console.log(`\n${report.length - failed.length}/${report.length} checks passed`);
process.exit(failed.length ? 1 : 0);
