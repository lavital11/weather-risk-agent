"""HURDAT2 update: picking the newest release, validating the file, all-or-nothing table swap.
No network: listing text and file contents are hand-made."""

import pytest

from weather_risk.db import connect
from weather_risk.ingest.hurdat2 import HurdatError, find_latest, parse, release_date, swap_table

# File names as they appeared in the NHC folder listing on 2026-11-22.
LISTING = " ".join(f'<a href="{n}">' for n in [
    "hurdat2-1851-2024-040225.txt", "hurdat2-1851-2025-02272026.txt", "hurdat2-1851-2025-091226.txt",
    "hurdat2-1851-2025-092326.txt", "hurdat2-nepac-1949-2025-092926.txt", "hurdat2-format-atlantic.pdf"])

H = "AL011995,              ALPHA,      2,\n"
P1 = "19950601, 0000,  , TS, 25.0N,  80.0W,  40, 1000\n"
P2 = "19950601, 0600,  , HU, 26.0N,  81.0W,  70,  990\n"


def test_release_date_handles_both_formats():
    assert str(release_date("092326")) == "2026-09-23"
    assert str(release_date("02272026")) == "2026-02-27"


def test_find_latest_picks_newest_year_then_latest_release_and_ignores_pacific():
    assert find_latest(LISTING) == ("hurdat2-1851-2025-092326.txt", 2025)
    assert find_latest("<html>nothing here</html>") is None


def test_parse_valid_file():
    rows = parse(H + P1 + P2, expected_year=1995)
    assert [(r[0], r[3], r[5], r[6], r[7]) for r in rows] == [("AL011995", 1, "TS", 25.0, -80.0),
                                                            ("AL011995", 2, "HU", 26.0, -81.0)]


def test_parse_rejects_point_count_mismatch_wrong_year_and_garbage():
    with pytest.raises(HurdatError, match="declares 2 points, found 1"):
        parse(H + P1, expected_year=1995)
    with pytest.raises(HurdatError, match="should cover 1996"):
        parse(H + P1 + P2, expected_year=1996)
    with pytest.raises(HurdatError, match="cannot parse"):
        parse(H + P1 + "19950601, 0600,  , HU, garbage\n", expected_year=1995)


def test_swap_is_all_or_nothing(tmp_path):
    conn = connect(tmp_path / "t.db")
    swap_table(conn, parse(H + P1 + P2), "file-1")
    assert conn.execute("SELECT COUNT(*) FROM storm_track_points").fetchone()[0] == 2
    bad = [("AL021996", "BETA", 1996, 1, "x", "TS", 1.0, 1.0, 40), (None, "BAD", 1996, 2, "x", "TS", 1.0, 1.0, 40)]
    with pytest.raises(Exception):
        swap_table(conn, bad, "file-2")          # NOT NULL violation halfway through the insert
    ids = {r[0] for r in conn.execute("SELECT storm_id FROM storm_track_points")}
    assert ids == {"AL011995"}                   # old data kept, no half-new table
    swap_table(conn, [("AL021996", "BETA", 1996, 1, "x", "TS", 1.0, 1.0, 40)], "file-3")
    ids = {r[0] for r in conn.execute("SELECT storm_id FROM storm_track_points")}
    assert ids == {"AL021996"}                   # a storm missing from the new file doesn't stay behind
