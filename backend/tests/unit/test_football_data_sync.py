"""
Daily league-CSV sync: stores new data, never overwrites good files with
bad downloads, and the pipeline only runs it once a day.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import football_data_sync as fds
import main

HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365H,B365D,B365A\n"


def csv(n, team="Arsenal"):
    return HEADER + "".join(f"E0,{d:02d}/08/2026,{team},Chelsea,1,0,H,2.0,3.4,3.9\n" for d in range(1, n + 1))


def run(responses, tmp_path, seasons=("2627",), divisions=("E0",)):
    """responses: {url suffix: (status, bytes)}"""
    def handler(request):
        for suffix, (status, body) in responses.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(status, content=body)
        return httpx.Response(404)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return asyncio.run(fds.sync(str(tmp_path), divisions=divisions, seasons=seasons, client=client))


class TestSeasons:
    @pytest.mark.parametrize("d,code", [(date(2026, 9, 23), "2627"), (date(2026, 6, 30), "2526"),
                                       (date(2026, 7, 1), "2627"), (date(2099, 12, 1), "9900")])
    def test_season_code(self, d, code):
        assert fds.season_code(d) == code

    def test_recent_seasons(self):
        assert fds.recent_seasons(date(2026, 9, 23), 3) == ["2627", "2526", "2425"]


class TestSync:
    def test_writes_a_new_file(self, tmp_path):
        report = run({"/2627/E0.csv": (200, csv(3).encode("utf-8-sig"))}, tmp_path)
        assert report["updated"] == ["E0_2627.csv"]
        saved = (tmp_path / "E0_2627.csv").read_text(encoding="utf-8")
        assert saved.startswith("Div,") and saved.count("Arsenal") == 3  # BOM removed

    def test_unchanged_file_is_not_rewritten(self, tmp_path):
        (tmp_path / "E0_2627.csv").write_text(csv(3), encoding="utf-8")
        report = run({"/2627/E0.csv": (200, csv(3).encode())}, tmp_path)
        assert report["unchanged"] == ["E0_2627.csv"] and not report["updated"]

    def test_newer_data_replaces_older(self, tmp_path):
        (tmp_path / "E0_2627.csv").write_text(csv(2), encoding="utf-8")
        assert run({"/2627/E0.csv": (200, csv(5).encode())}, tmp_path)["updated"] == ["E0_2627.csv"]

    def test_a_shorter_download_never_replaces_the_file(self, tmp_path):
        (tmp_path / "E0_2627.csv").write_text(csv(5), encoding="utf-8")
        report = run({"/2627/E0.csv": (200, csv(2).encode())}, tmp_path)
        assert report["failed"] and (tmp_path / "E0_2627.csv").read_text().count("Arsenal") == 5

    @pytest.mark.parametrize("body", [b"<html>Maintenance</html>", b"a,b\n1,2\n", HEADER.encode()])
    def test_rejects_anything_but_results(self, tmp_path, body):
        report = run({"/2627/E0.csv": (200, body)}, tmp_path)
        assert report["failed"] and not (tmp_path / "E0_2627.csv").exists()

    def test_unpublished_season_is_skipped(self, tmp_path):
        assert run({}, tmp_path)["skipped"] == ["E0_2627.csv"]

    def test_server_errors_are_reported(self, tmp_path):
        assert run({"/2627/E0.csv": (503, b"")}, tmp_path)["failed"] == ["E0_2627.csv: HTTP 503"]

    def test_windows_1252_files_are_stored_as_utf8(self, tmp_path):
        run({"/2627/E0.csv": (200, csv(2, team="Nürnberg").encode("cp1252"))}, tmp_path)
        assert "Nürnberg" in (tmp_path / "E0_2627.csv").read_text(encoding="utf-8")


class TestPipelineThrottle:
    @pytest.fixture
    def calls(self, monkeypatch):
        calls = []

        async def fake_sync(dest):
            calls.append(dest)
            return {"updated": [], "unchanged": ["E0_2627.csv"], "skipped": [], "failed": []}
        monkeypatch.setattr(fds, "sync", fake_sync)
        monkeypatch.setattr(main, "_football_sync", {"at": None, "report": None})
        monkeypatch.delenv("FOOTBALL_DATA_SYNC", raising=False)
        return calls

    def test_runs_once_a_day(self, calls):
        asyncio.run(main._sync_football_data())
        asyncio.run(main._sync_football_data())
        assert len(calls) == 1
        main._football_sync["at"] = datetime.now(timezone.utc) - timedelta(hours=main.FOOTBALL_DATA_SYNC_HOURS + 1)
        asyncio.run(main._sync_football_data())
        assert len(calls) == 2

    def test_can_be_turned_off(self, calls, monkeypatch):
        monkeypatch.setenv("FOOTBALL_DATA_SYNC", "0")
        asyncio.run(main._sync_football_data())
        assert calls == []


class TestDataStatus:
    def test_admin_only(self, monkeypatch):
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
        assert TestClient(main.app).get("/api/admin/data-status").status_code == 403

    def test_reports_leagues_and_thin_teams(self, monkeypatch, tmp_path):
        from tests.unit.test_team_names import _season
        from predictor import LeaguePredictor
        (tmp_path / "E0_2627.csv").write_text(csv(4), encoding="utf-8")
        m = LeaguePredictor()
        m.train(_season(["Man United", "Arsenal", "Wolves", "Leeds"], rounds=2))
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
        monkeypatch.setattr(main, "FOOTBALL_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(main, "_predictor", m)
        monkeypatch.setattr(main, "_predictions_cache", [
            {"home": "Manchester United FC", "away": "Paris FC", "league": "PL"}])
        r = TestClient(main.app).get("/api/admin/data-status", params={"secret": "s3cret"}).json()
        assert r["leagues"]["E0"]["latest_match"] == "2026-08-04"
        assert r["renamed"] == {"Manchester United FC": "Man United"}
        assert [t["team"] for t in r["thin_history"]] == ["Paris FC"]
