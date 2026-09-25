#!/usr/bin/env python3
"""Offline NBA ingestion tests using committed NBA examples + synthetic transitions.

Mocks NEVER write to data/ or pretend a network fetch is an NBA verification.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_nba_data as sync  # noqa: E402


def fixture(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


RAW_SCOREBOARD = fixture("data/live/scoreboard.json")["scoreboard"]
BOX = fixture("data/games/0022400154/boxscore.json")
PBP = fixture("data/games/0022400154/playbyplay.json")
CARD = fixture("data/verification/cards-probe.json")["dates"][0]["firstCard"]
GAME_ID = BOX["game"]["gameId"]
META = {"url": "official NBA test fixture", "httpStatus": 200, "bytes": 100,
        "sourceSha256": "a" * 64, "attempts": 1}


def log(time="2026-09-25T12:01:00+00:00"):
    return {"runStartedUtc": time, "mode": "live", "steps": []}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = pathlib.Path(self.tmp.name) / "data"
        self.data.mkdir()
        p1 = patch.object(sync, "DATA", str(self.data))
        p2 = patch.object(sync, "LOG_PATH", str(self.data / "verification" / "sync-log.json"))
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def read(self, name):
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def nba_fetch(self, live=None, box=None, pbp=None):
        """Return only explicitly supplied NBA-shaped fixtures to the mocked client."""
        def fetch(url, headers, *args, **kwargs):
            if url == sync.LIVE_SCOREBOARD_URL: return copy.deepcopy(live), dict(META, url=url)
            if url == sync.live_boxscore_url(GAME_ID): return copy.deepcopy(box), dict(META, url=url)
            if url == sync.live_playbyplay_url(GAME_ID): return copy.deepcopy(pbp), dict(META, url=url)
            return None, {"url": url, "httpStatus": 404, "error": "mocked missing file"}
        return fetch

    def live_example(self):
        game = copy.deepcopy(BOX["game"])
        game["gameStatus"] = 2
        game["gameStatusText"] = "Q4"
        game["gameClock"] = "PT02M12.00S"
        board = copy.deepcopy(RAW_SCOREBOARD)
        board["scoreboard"]["games"] = [game]
        board["scoreboard"]["gameDate"] = "2024-11-04"
        return board, {"game": copy.deepcopy(game)}, {"game": {"gameId": GAME_ID, "actions": copy.deepcopy(PBP["actions"])}}

    def test_live_updates_complete_stats_before_final(self):
        board, box, pbp = self.live_example()
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(board, box, pbp)):
            result = sync.sync_live(log())
        self.assertTrue(result)
        self.assertEqual(self.read("live/scoreboard.json")["gameCount"], 1)
        self.assertEqual(self.read(f"games/{GAME_ID}/boxscore.json")["game"]["gameStatus"], 2)
        self.assertEqual(self.read(f"games/{GAME_ID}/playbyplay.json")["actionCount"], len(PBP["actions"]))
        self.assertGreater(len(PBP["actions"]), 80)
        self.assertFalse((self.data / "live/plays.json").exists(), "no truncated last-80-actions feed")

    def test_invalid_board_preserves_last_verified_snapshot(self):
        board, box, pbp = self.live_example()
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(board, box, pbp)):
            self.assertTrue(sync.sync_live(log()))
        before = (self.data / "live/scoreboard.json").read_bytes()
        for invalid in ({"scoreboard": {"gameDate": "2026-09-25"}},
                        {"scoreboard": {"gameDate": "2026-09-25", "games": [{"gameId": "bad"}]}},
                        {"scoreboard": {"gameDate": "2026-02-30", "games": []}}):
            run = log()
            with patch.object(sync, "http_get", side_effect=self.nba_fetch(live=invalid)):
                self.assertFalse(sync.sync_live(run))
            self.assertEqual(run["steps"][0]["result"], "INVALID")
            self.assertEqual((self.data / "live/scoreboard.json").read_bytes(), before)

    def test_swapped_pbp_rejected_and_missing_pbp_retried(self):
        _, box, pbp = self.live_example()
        box["game"]["gameStatus"] = 3
        bad_pbp = copy.deepcopy(pbp)
        bad_pbp["game"]["gameId"] = "0022400155"
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=bad_pbp)):
            run = log()
            sync.archive_game(GAME_ID, run)
        self.assertTrue((self.data / "games" / GAME_ID / "boxscore.json").exists())
        self.assertFalse((self.data / "games" / GAME_ID / "playbyplay.json").exists())
        self.assertEqual(run["steps"][-1]["result"], "INVALID")
        yesterday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
        digest = {"games": [{"gameId": GAME_ID, "gameStatus": 3, "seasonYear": "2024-25",
                              "home": {"teamId": box["game"]["homeTeam"]["teamId"], "score": 116},
                              "away": {"teamId": box["game"]["awayTeam"]["teamId"], "score": 114}}]}
        sync.write_json(str(self.data / "scoreboard" / f"{yesterday}.json"), digest)
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            run = log()
            sync.sync_archive_pending(run, days=1)
        self.assertEqual(self.read(f"games/{GAME_ID}/playbyplay.json")["actionCount"], len(PBP["actions"]))
        self.assertIn("OK", [s["result"] for s in run["steps"] if s["step"] == "verifyScore"])

    def test_final_mismatched_pbp_score_is_not_published(self):
        board, box, pbp = self.live_example()
        box["game"]["gameStatus"] = 3
        pbp["game"]["actions"][-1]["scoreHome"] = "200"
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            run = log()
            sync.archive_game(GAME_ID, run)
        self.assertEqual(run["steps"][-1]["result"], "MISMATCH")
        self.assertFalse((self.data / "games" / GAME_ID / "playbyplay.json").exists())
        # An NBA CDN transition may publish Final box first and only later the
        # Game End action. Retire provisional PBP and retry next tick, never
        # ship a final box with incomplete actions as a finished game.
        box["game"]["gameStatus"] = 2
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            sync.archive_game(GAME_ID, log(), force=True)
        box["game"]["gameStatus"] = 3
        bad = copy.deepcopy(pbp)
        bad["game"]["actions"][-1]["scoreHome"] = "200"
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=bad)):
            run = log()
            sync.archive_game(GAME_ID, run, force=True, expected=board["scoreboard"]["games"][0] | {"gameStatus": 3})
        self.assertFalse((self.data / "games" / GAME_ID / "playbyplay.json").exists())
        self.assertIn("retireIncompletePlaybyplay", [s["step"] for s in run["steps"]])
        pbp["game"]["actions"][-1]["scoreHome"] = "116"
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            sync.archive_game(GAME_ID, log())
        self.assertEqual(self.read(f"games/{GAME_ID}/playbyplay.json")["actionCount"], len(PBP["actions"]))

    def test_final_scoreboard_and_cdn_box_mismatch_is_flagged_not_archived(self):
        board, box, pbp = self.live_example()
        expected = board["scoreboard"]["games"][0]
        expected["gameStatus"] = 3
        box["game"]["gameStatus"] = 3
        box["game"]["homeTeam"]["score"] = 200
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            run = log()
            sync.archive_game(GAME_ID, run, force=True, expected=expected)
        self.assertEqual(run["steps"][-1]["result"], "MISMATCH")
        self.assertFalse((self.data / "games" / GAME_ID / "boxscore.json").exists())

    def html(self, modules, selected_date=None, calendar=None):
        props = {"gameCardFeed": {"modules": modules}}
        if selected_date is not None:
            props["selectedDate"] = selected_date
        if calendar is not None:
            props["allGamesInCurrentYear"] = calendar
        next_data = {"props": {"pageProps": props}}
        return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'

    def test_date_pages_fail_closed_and_never_silently_drop_cards(self):
        good = copy.deepcopy(CARD)
        valid = self.html([{"cards": [good]}])
        with patch.object(sync, "http_get_html", return_value=(valid, dict(META))):
            rows, _ = sync.cards_for_date("2024-11-04", log())
        self.assertEqual([r["gameId"] for r in rows], [GAME_ID])
        cases = [self.html([{"cards": [good, {}]}]), self.html([{"cards": [good, good]}]),
                 self.html([{"cards": [dict(good, cardData=dict(good["cardData"], gameTimeEastern="2024-11-05T19:00:00Z"))]}]),
                 self.html([], selected_date="2024-11-05"),
                 '<script id="__NEXT_DATA__">{"props":{"pageProps":{}}}</script>']
        for html in cases:
            run = log()
            with patch.object(sync, "http_get_html", return_value=(html, dict(META))):
                rows, _ = sync.cards_for_date("2024-11-04", run)
            self.assertIsNone(rows)
            self.assertEqual(run["steps"][-1]["result"], "INVALID")
        with patch.object(sync, "http_get_html", return_value=(self.html([]), dict(META))):
            rows, _ = sync.cards_for_date("2026-09-24", log())
        self.assertEqual(rows, [], "a schema-valid empty NBA card feed is a checked no-card date")

    def test_utc_yesterday_in_progress_is_refetched_until_final(self):
        card = copy.deepcopy(CARD)
        card["cardData"]["gameStatus"] = 2
        time = card["cardData"]["gameTimeEastern"][:10]
        with patch.object(sync, "http_get_html", return_value=(self.html([{"cards": [card]}]), dict(META))):
            self.assertTrue(sync.sync_date_digest(time, log()))
        self.assertEqual(self.read(f"scoreboard/{time}.json")["games"][0]["gameStatus"], 2)
        card["cardData"]["gameStatus"] = 3
        with patch.object(sync, "http_get_html", return_value=(self.html([{"cards": [card]}]), dict(META))) as fetch:
            self.assertTrue(sync.sync_date_digest(time, log()))
            self.assertTrue(fetch.called)
        self.assertEqual(self.read(f"scoreboard/{time}.json")["games"][0]["gameStatus"], 3)
        with patch.object(sync, "http_get_html", side_effect=AssertionError("Final must not churn")):
            self.assertTrue(sync.sync_date_digest(time, log()))

    def test_mismatched_team_or_score_flagged_against_official_box(self):
        _, box, pbp = self.live_example()
        box["game"]["gameStatus"] = 3
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            sync.archive_game(GAME_ID, log())
        digest = {"games": [{"gameId": GAME_ID, "gameStatus": 3, "home": {"teamId": 123, "score": 116},
                              "away": {"teamId": box["game"]["awayTeam"]["teamId"], "score": 114}}]}
        sync.write_json(str(self.data / "scoreboard" / "2024-11-04.json"), digest)
        run = log()
        sync.verify_digest_against_boxscore("2024-11-04", run)
        self.assertEqual(run["steps"][0]["result"], "MISMATCH")

    def test_sparse_nba_year_calendar_checks_counts_and_only_skips_explicit_zeros(self):
        date = "2024-11-04"
        counts = {"2024": {date: 1, "2024-11-05": 0}}
        good = self.html([{"cards": [CARD]}], selected_date=date, calendar=counts)
        with patch.object(sync, "http_get_html", return_value=(good, dict(META))):
            self.assertTrue(sync.sync_date_digest(date, log()))
        calendar = self.read("calendar/2024.json")
        self.assertEqual(calendar["dateCounts"], counts["2024"])
        self.assertEqual(calendar["_sync"]["source"], sync.games_page_url(date))
        self.assertEqual(sync.known_calendar_count("2024-11-05"), 0)
        self.assertIsNone(sync.known_calendar_count("2024-11-06"), "missing is NOT zero")
        sync.build_index(log())
        self.assertEqual(self.read("index.json")["calendars"]["2024"]["gameDates"], 1)
        cursor = self.data / "verification" / "backfill-cursor.json"
        sync.write_json(str(cursor), {"nextDate": "2024-11-05", "earliestTarget": "2024-11-05"})
        with patch.object(sync, "http_get_html", side_effect=AssertionError("explicit NBA zero needs no extra page fetch")):
            sync.backfill_history_step(log(), batch=1)
        self.assertEqual(self.read("verification/backfill-cursor.json")["lastBatchExplicitZeroSkips"], 1)
        self.assertFalse((self.data / "scoreboard/2024-11-05.json").exists())
        unknown = self.html([], selected_date="2024-11-06", calendar=counts)
        sync.write_json(str(cursor), {"nextDate": "2024-11-06", "earliestTarget": "2024-11-06"})
        with patch.object(sync, "http_get_html", return_value=(unknown, dict(META))) as fetch:
            sync.backfill_history_step(log(), batch=1)
            self.assertTrue(fetch.called, "an omitted date cannot be skipped as empty")
        self.assertEqual(self.read("scoreboard/2024-11-06.json")["gameCount"], 0)
        mismatch = self.html([{"cards": [CARD]}], selected_date=date,
                             calendar={"2024": {date: 2}})
        before = (self.data / "scoreboard" / f"{date}.json").read_bytes()
        with patch.object(sync, "http_get_html", return_value=(mismatch, dict(META))):
            run = log()
            self.assertFalse(sync.sync_date_digest(date, run, force=True))
        self.assertEqual(run["steps"][-1]["result"], "MISMATCH")
        self.assertEqual((self.data / "scoreboard" / f"{date}.json").read_bytes(), before)

    def test_priority_queue_fetches_only_nba_declared_game_dates_and_retries_mismatch(self):
        date = "2024-11-04"
        counts = {"2024": {date: 1, "2024-11-05": 0}}
        sync.write_json(str(self.data / "calendar" / "2024.json"), {"year": "2024", "dateCounts": counts["2024"]})
        official = self.html([{"cards": [CARD]}], selected_date=date, calendar=counts)
        with patch.object(sync, "http_get_html", return_value=(official, dict(META))) as fetch:
            sync.backfill_known_game_days(log(), batch=1)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.read(f"scoreboard/{date}.json")["gameCount"], 1)
        with patch.object(sync, "http_get_html", side_effect=AssertionError("already archived/zero dates must be skipped")):
            sync.backfill_known_game_days(log(), batch=1)
        (self.data / "scoreboard" / f"{date}.json").unlink()
        bad = self.html([{"cards": [CARD]}], selected_date=date, calendar={"2024": {date: 2}})
        with patch.object(sync, "http_get_html", return_value=(bad, dict(META))):
            run = log()
            sync.backfill_known_game_days(run, batch=1)
        self.assertFalse((self.data / "scoreboard" / f"{date}.json").exists())
        self.assertEqual(run["steps"][-1]["result"], "MISMATCH")

    def test_known_schedule_prevents_false_empty_night_and_rechecks_cached_zero(self):
        date = "2024-11-04"
        empty = self.html([], selected_date=date)
        with patch.object(sync, "http_get_html", return_value=(empty, dict(META))):
            self.assertTrue(sync.sync_date_digest(date, log()))
        old_file = (self.data / "scoreboard" / f"{date}.json").read_bytes()
        sync.write_json(str(self.data / "schedule" / "2024-25.json"),
                        {"games": [{"gameDateEst": date, "gameId": GAME_ID}]})
        self.assertEqual(sync.known_schedule_game_ids(date), {GAME_ID})
        with patch.object(sync, "http_get_html", return_value=(empty, dict(META))) as fetch:
            run = log()
            self.assertFalse(sync.sync_date_digest(date, run))
            self.assertTrue(fetch.called, "a newly published schedule invalidates cached zero")
        self.assertEqual(run["steps"][-1]["result"], "MISMATCH")
        self.assertEqual((self.data / "scoreboard" / f"{date}.json").read_bytes(), old_file)
        with patch.object(sync, "http_get_html", return_value=(self.html([{"cards": [CARD]}]), dict(META))):
            self.assertTrue(sync.sync_date_digest(date, log()))
        self.assertEqual(self.read(f"scoreboard/{date}.json")["gameCount"], 1)
        board = {"scoreboard": {"gameDate": date, "games": []}}
        self.assertIn("empty live feed", sync.scoreboard_error(board))
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(live=board)):
            run = log()
            self.assertFalse(sync.sync_live(run))
        self.assertEqual(run["steps"][0]["result"], "INVALID")
        self.assertFalse((self.data / "live/scoreboard.json").exists())

    def test_cached_provisional_detail_upgrades_and_final_card_prevents_bad_box(self):
        board, box, pbp = self.live_example()
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            sync.archive_game(GAME_ID, log())
        self.assertEqual(self.read(f"games/{GAME_ID}/boxscore.json")["game"]["gameStatus"], 2)
        box["game"]["gameStatus"] = 3
        card = sync.compact_card(CARD["cardData"])
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            sync.archive_game(GAME_ID, log(), expected=card)  # no force: must NOT skip provisional
        self.assertEqual(self.read(f"games/{GAME_ID}/boxscore.json")["game"]["gameStatus"], 3)
        card["home"]["score"] = 200
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)) as fetch:
            run = log()
            sync.archive_game(GAME_ID, run, expected=card)
        self.assertTrue(fetch.called, "a newly contradictory card must not silently skip archived detail")
        self.assertEqual(run["steps"][-1]["result"], "MISMATCH")
        yesterday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
        sync.write_json(str(self.data / "scoreboard" / f"{yesterday}.json"), {"games": [card]})
        with patch.object(sync, "http_get", side_effect=self.nba_fetch(box=box, pbp=pbp)):
            run = log()
            sync.sync_archive_pending(run, days=1)
        self.assertIn("MISMATCH", [s["result"] for s in run["steps"]])
        self.assertEqual(self.read(f"games/{GAME_ID}/boxscore.json")["game"]["homeTeam"]["score"], 116)

    def test_noop_hour_does_not_rewrite_manifest_or_fake_fetch_log(self):
        one = log("2026-09-25T12:01:00+00:00")
        one["steps"].append({"step": "live", "result": "OK", "written": False})
        one["summary"] = {"ok": 1, "failed": 0, "written": 0}
        self.assertTrue(sync.append_log(one))
        sync.build_index(one)
        previous = (self.data / "index.json").read_bytes()
        two = copy.deepcopy(one)
        two["runStartedUtc"] = "2026-09-25T12:30:00+00:00"
        self.assertFalse(sync.append_log(two))
        sync.build_index(two)
        self.assertEqual((self.data / "index.json").read_bytes(), previous)
        three = copy.deepcopy(two)
        three["runStartedUtc"] = "2026-09-25T13:00:00+00:00"
        self.assertTrue(sync.append_log(three))
        sync.build_index(three)
        self.assertEqual(self.read("index.json")["heartbeat"]["lastSuccessfulLiveUtc"], three["runStartedUtc"])
        self.assertEqual(self.read("verification/sync-log.json")["runs"], [])

    def test_dates_and_game_ids_have_real_shapes(self):
        self.assertTrue(sync.valid_date("2024-02-29"))
        self.assertFalse(sync.valid_date("2024-02-30"))
        self.assertFalse(sync.valid_date("../../data"))
        self.assertTrue(sync.valid_game_id(GAME_ID))
        self.assertFalse(sync.valid_game_id("1234"))


if __name__ == "__main__":
    unittest.main()
