/* Headless smoke test: real index.html + app.js, real committed data/ files. */
import { JSDOM } from 'jsdom';
import fs from 'fs';
import path from 'path';

import { fileURLToPath } from 'url';
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(REPO, 'index.html'), 'utf8');
const appjs = fs.readFileSync(path.join(REPO, 'app.js'), 'utf8');

const errors = [];
const warnings = [];

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
check('status bar shows the official feed date', /2026-09-25/.test(txt('statusText')), txt('statusText'));
check('status meta names the snapshot age', /snapshot/.test(txt('statusMeta')), txt('statusMeta'));
check('freshness pill filled', txt('freshnessPill').startsWith('snapshot'), txt('freshnessPill'));
check('build info shows archived counts', /archived dates: \d+/.test(txt('buildInfo')), txt('buildInfo'));
check('season selector populated', $('seasonSelect').innerHTML.includes('2026-27'));
check('archive chips rendered', $('archiveList').querySelectorAll('button.chip').length > 20,
  `${$('archiveList').querySelectorAll('button.chip').length} chips`);

// ---- date view (historical)
await window.NBAScoreboard.loadDate('2024-11-04');
check('date view status', /15 games archived for 2024-11-04/.test(txt('statusText')), txt('statusText'));
const dateRows = $('dateResult').querySelectorAll('.game-row').length;
check('15 archived rows rendered', dateRows === 15, `${dateRows} rows`);
check('quarter table shows a real score', $('dateResult').innerHTML.includes('116'));
check('row links to the official date page',
  $('dateResult').innerHTML.includes('nba.com/games?date=2024-11-04'));

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

// ---- game detail: archived box score + play-by-play
await window.NBAScoreboard.openGame('0022400154');
await new Promise((r) => setTimeout(r, 300));
const box = $('boxscoreContent').innerHTML;
const pbp = $('playbyplayContent').innerHTML;
check('box score rendered', box.includes('Okoro') && box.includes('116'));
check('minutes formatted (not raw ISO)', !/PT\d+M/.test(box), box.match(/PT\d+M[\d.]+S/)?.[0] || '');
check('play-by-play rendered', (pbp.match(/pbp-row/g) || []).length > 400, `${(pbp.match(/pbp-row/g) || []).length} rows`);
check('pbp shows Period Start action', pbp.includes('Period Start'));
// filters
$('periodFilter').value = '4';
$('periodFilter').dispatchEvent(new window.Event('change'));
const q4 = ($('playbyplayContent').innerHTML.match(/pbp-row/g) || []).length;
check('period filter narrows the list', q4 > 0 && q4 < 520, `${q4} Q4 rows`);
$('periodFilter').value = 'all';
$('periodFilter').dispatchEvent(new window.Event('change'));
$('searchActions').value = '3pt';
$('searchActions').dispatchEvent(new window.Event('input'));
const three = ($('playbyplayContent').innerHTML.match(/pbp-row/g) || []).length;
check('text filter narrows the list', three > 0 && three < 520, `${three} rows for “3pt”`);
check('filtered rows mention the query', /3PT/i.test($('playbyplayContent').innerHTML));
$('searchActions').value = '';
$('searchActions').dispatchEvent(new window.Event('input'));

// ---- Box score archive for a date without boxscore (should fall back, not crash)
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
check('back to live works', /No games scheduled for 2026-09-25/.test(txt('statusText')), txt('statusText'));

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
