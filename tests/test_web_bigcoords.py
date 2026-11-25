"""End-to-end checks for 64-bit coordinates and super-64-bit area display."""

from __future__ import annotations

import pytest

from adjcheck import service
from adjcheck.app import create_app
from adjcheck.db import Database
from adjcheck.geometry import COORD_MAX, COORD_MIN, Rect
from adjcheck.models import BatchEntry, Stats

INT64_SPAN = 2**64 - 1  # width of [COORD_MIN, COORD_MAX]


@pytest.fixture()
def client(tmp_path):
    app = create_app(database_path=str(tmp_path / "web-big.sqlite3"), seed_demo=False)
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client, app


def _create(client, name, extent, sheet_lines):
    coords = extent.as_tuple() if isinstance(extent, Rect) else tuple(extent)
    x0, y0, x1, y1 = coords
    return client.post(
        "/projects",
        data={
            "name": name,
            "x0": str(x0),
            "y0": str(y0),
            "x1": str(x1),
            "y1": str(y1),
            "sheets": sheet_lines,
        },
        follow_redirects=True,
    )


def test_index_renders_super_64bit_area_and_detail_matches(client):
    c, app = client
    db: Database = app.extensions["adjcheck_db"]
    # missing strip of height 1 over the full int64 x span -> area = 2^64-1
    extent = Rect(COORD_MIN, 0, COORD_MAX, 2)
    service.create_project(
        db,
        "HUGE",
        extent,
        [BatchEntry("S1", Rect(COORD_MIN, 0, COORD_MAX, 1))],
    )
    expected_area = INT64_SPAN
    assert expected_area > 2**63

    # the old SQL SUM((x1-x0)*(y1-y0)) raised "integer overflow" here
    index = c.get("/")
    assert index.status_code == 200
    assert str(expected_area) in index.data.decode()

    detail = c.get("/projects/HUGE")
    assert detail.status_code == 200
    body = detail.data.decode()
    assert str(expected_area) in body
    # exact extreme coordinates are shown verbatim
    assert str(COORD_MIN) in body and str(COORD_MAX) in body
    # the SVG is replaced by an explanatory note, not a broken viewBox
    assert "无法按比例准确绘制示意图" in body

    state = c.get("/api/projects/HUGE/state").get_json()
    assert state["stats"]["missing_area"] == expected_area
    assert state["issues"][0]["area"] == expected_area
    assert state["project"]["extent"] == [COORD_MIN, 0, COORD_MAX, 2]


def test_one_square_metre_correction_reflected_even_above_64bit_total(client):
    c, app = client
    db: Database = app.extensions["adjcheck_db"]
    service.create_project(
        db,
        "HUGE2",
        Rect(COORD_MIN, 0, COORD_MAX, 2),
        [BatchEntry("S1", Rect(COORD_MIN, 1, COORD_MAX, 2))],
    )
    before = c.get("/api/projects/HUGE2/state").get_json()
    assert before["stats"]["missing_area"] == INT64_SPAN

    # patch exactly one square metre at the negative extreme
    batch = [{"no": "S2", "x0": COORD_MIN, "y0": 0, "x1": COORD_MIN + 1, "y1": 1}]
    check = c.post("/api/projects/HUGE2/check", json={"batch": batch, "version": 0})
    plan = check.get_json()
    assert check.status_code == 200 and plan["accepted"] is True
    assert plan["eliminated"]["missing_area"] == 1
    assert plan["after"]["missing_area"] == INT64_SPAN - 1

    applied = c.post("/api/projects/HUGE2/apply", json={"batch": batch, "version": 0})
    assert applied.status_code == 200 and applied.get_json()["applied"] is True
    after = c.get("/api/projects/HUGE2/state").get_json()
    assert after["project"]["version"] == 1
    assert after["stats"]["missing_area"] == INT64_SPAN - 1
    # the stored patch kept its exact 1-metre width
    patch = next(s for s in after["sheets"] if s["no"] == "S2")
    assert patch["rect"] == [COORD_MIN, 0, COORD_MIN + 1, 1]
    assert patch["rect"][2] - patch["rect"][0] == 1


def test_api_keeps_exact_coordinates_beyond_float_safe_integer(client):
    c, _ = client
    base = 2**53 + 1  # adjacent integers here collapse under JavaScript Number
    resp = _create(
        c,
        "BIGWEB",
        Rect(base - 10, 0, base + 11, 10),
        f"S1 {base - 10} 0 {base} 10\nS3 {base + 1} 0 {base + 11} 10\n",
    )
    assert resp.status_code == 200
    state = c.get("/api/projects/BIGWEB/state").get_json()
    # the gap is exactly one metre wide; full digits survive the JSON round trip
    missing = state["issues"]
    assert len(missing) == 1
    assert missing[0]["rect"] == [base, 0, base + 1, 10]
    assert missing[0]["rect"][2] - missing[0]["rect"][0] == 1
    assert state["sheets"][0]["rect"][2] == base

    # fill the 1-metre gap through the JSON API and verify width stays 1 m
    batch = [{"no": "S2", "x0": base, "y0": 0, "x1": base + 1, "y1": 10}]
    applied = c.post("/api/projects/BIGWEB/apply", json={"batch": batch, "version": 0})
    assert applied.get_json()["applied"] is True
    state2 = c.get("/api/projects/BIGWEB/state").get_json()
    assert state2["stats"]["problem_area"] == 0
    s2 = next(s for s in state2["sheets"] if s["no"] == "S2")
    assert s2["rect"] == [base, 0, base + 1, 10]


def test_out_of_range_inputs_rejected_with_reason_and_no_side_effects(client):
    c, app = client
    db: Database = app.extensions["adjcheck_db"]
    service.create_project(
        db,
        "P",
        Rect(0, 0, 100, 100),
        [BatchEntry("A", Rect(0, 0, 50, 100)), BatchEntry("B", Rect(60, 0, 100, 100))],
    )

    # 1. project creation with an out-of-range extent is refused, nothing created
    resp = _create(
        c,
        "BADRANGE",
        (0, 0, COORD_MAX + 1, 1),
        "A 0 0 1 1",
    )
    body = resp.data.decode()
    assert "超出有符号 64 位" in body
    assert db.get_project("BADRANGE") is None

    # 2. JSON precheck over the limit returns 400 with the bound reason
    resp = c.post(
        "/api/projects/P/check",
        json={"batch": [{"no": "A", "x0": 0, "y0": 0, "x1": COORD_MAX + 1, "y1": 100}],
              "version": 0},
    )
    assert resp.status_code == 400
    payload = resp.get_json()
    assert any("超出有符号 64 位" in r for r in payload["reasons"])

    # 3. non-integer JSON coordinates are refused rather than coerced
    resp = c.post(
        "/api/projects/P/check",
        json={"batch": [{"no": "A", "x0": 0, "y0": 0, "x1": 1.5, "y1": 100}],
              "version": 0},
    )
    assert resp.status_code == 400 and "整数" in resp.get_json()["error"]
    resp = c.post(
        "/api/projects/P/check",
        json={"batch": [{"no": "A", "x0": True, "y0": 0, "x1": 1, "y1": 100}],
              "version": 0},
    )
    assert resp.status_code == 400 and "整数" in resp.get_json()["error"]

    # catalog, verification result and version are untouched
    state = c.get("/api/projects/P/state").get_json()
    assert state["project"]["version"] == 0
    assert [s["no"] for s in state["sheets"]] == ["A", "B"]
    expected_stats = Stats.from_issues(db.get_issues("P"))
    assert state["stats"] == {
        "missing_count": expected_stats.missing_count,
        "missing_area": expected_stats.missing_area,
        "overlap_count": expected_stats.overlap_count,
        "overlap_area": expected_stats.overlap_area,
        "problem_area": expected_stats.problem_area,
    }


def test_form_fallback_applies_exact_large_coordinate_patch(client):
    """The no-JS path keeps exact coordinates end to end too."""

    c, app = client
    db: Database = app.extensions["adjcheck_db"]
    base = 2**53 + 4242
    service.create_project(
        db,
        "FORMBIG",
        Rect(base - 5, 0, base + 6, 10),
        [
            BatchEntry("S1", Rect(base - 5, 0, base, 10)),
            BatchEntry("S3", Rect(base + 1, 0, base + 6, 10)),
        ],
    )
    resp = c.post(
        "/projects/FORMBIG/correct",
        data={
            "action": "apply",
            "version": "0",
            "batch": f"S2 {base} 0 {base + 1} 10",
        },
        follow_redirects=True,
    )
    assert "更正已写入" in resp.data.decode()
    state = c.get("/api/projects/FORMBIG/state").get_json()
    assert state["project"]["version"] == 1
    assert state["stats"]["problem_area"] == 0
    s2 = next(s for s in state["sheets"] if s["no"] == "S2")
    assert s2["rect"] == [base, 0, base + 1, 10]
    assert s2["rect"][2] - s2["rect"][0] == 1


def test_form_fallback_rejects_out_of_range_and_keeps_state(client):
    c, app = client
    db: Database = app.extensions["adjcheck_db"]
    service.create_project(
        db,
        "P",
        Rect(0, 0, 100, 100),
        [BatchEntry("A", Rect(0, 0, 50, 100)), BatchEntry("B", Rect(60, 0, 100, 100))],
    )
    resp = c.post(
        "/projects/P/correct",
        data={
            "action": "check",
            "version": "0",
            "batch": f"A 0 0 {COORD_MIN - 1} 100",
        },
        follow_redirects=True,
    )
    assert "超出有符号 64 位" in resp.data.decode()
    project, sheets, issues = db.get_state("P")
    assert project.version == 0
    assert [(s.no, s.rect.as_tuple()) for s in sheets] == [
        ("A", (0, 0, 50, 100)),
        ("B", (60, 0, 100, 100)),
    ]
    assert len(issues) == len(db.get_issues("P"))
