"""Signed 64-bit coordinate bounds and arbitrary-precision area handling."""

from __future__ import annotations

import pytest

from adjcheck import service
from adjcheck.geometry import (
    COORD_MAX,
    COORD_MIN,
    Rect,
    Region,
    analyze_rects,
    area_outside,
    check_int64,
    in_int64_range,
)
from adjcheck.models import BatchEntry, Stats

BIG = 2**63
WIDTH = 2 * BIG - 1  # span between the two extreme int64 coordinates: 2^64-1


def test_int64_constants_and_predicate():
    assert COORD_MIN == -(2**63)
    assert COORD_MAX == 2**63 - 1
    assert in_int64_range(COORD_MIN) and in_int64_range(COORD_MAX)
    assert not in_int64_range(COORD_MIN - 1)
    assert not in_int64_range(COORD_MAX + 1)


def test_check_int64_rejects_bools_and_out_of_range():
    assert check_int64(COORD_MAX, "x1") == COORD_MAX
    for bad in (True, False, 1.0, "1", None):
        with pytest.raises(ValueError):
            check_int64(bad, "x0")  # type: ignore[arg-type]
    for bad in (COORD_MIN - 1, COORD_MAX + 1):
        with pytest.raises(ValueError, match="64 位整数米"):
            check_int64(bad, "x0")


def test_rect_accepts_extreme_int64_and_keeps_exact_size():
    rect = Rect(COORD_MIN, 0, COORD_MAX, 1)
    assert rect.width == WIDTH
    assert rect.area == WIDTH
    thin = Rect(COORD_MAX - 1, COORD_MIN, COORD_MAX, COORD_MIN + 1)
    assert thin.width == 1 and thin.height == 1 and thin.area == 1


def test_rect_rejects_out_of_range_and_bool_coordinates():
    with pytest.raises(ValueError, match="超出有符号 64 位"):
        Rect(COORD_MAX + 1, 0, 1, 1)
    with pytest.raises(ValueError, match="超出有符号 64 位"):
        Rect(0, 0, COORD_MIN - 1, 1)
    with pytest.raises(ValueError):
        Rect(True, 0, 1, 1)  # type: ignore[arg-type]


def test_one_metre_patch_kept_exact_at_large_coordinate():
    """The reported web bug: width 1 m must not round to 2 m above 2^53."""

    base = 2**53 + 1  # not exactly representable as a float
    rect = Rect(base, 0, base + 1, 10)
    assert rect.width == 1
    assert rect.area == 10
    # exact edge-to-edge tiling around the 1-metre patch: no gap, no overlap
    regions = analyze_rects(
        Rect(base - 10, 0, base + 11, 10),
        [
            ("S1", Rect(base - 10, 0, base, 10)),
            ("S2", rect),
            ("S3", Rect(base + 1, 0, base + 11, 10)),
        ],
    )
    assert regions == []
    # a one-unit misalignment would be detected as a real 10 m² missing strip
    gappy = analyze_rects(
        Rect(base - 10, 0, base + 11, 10),
        [
            ("S1", Rect(base - 10, 0, base, 10)),
            ("S2", Rect(base + 1, 0, base + 11, 10)),
        ],
    )
    assert len(gappy) == 1 and gappy[0].kind == "missing"
    assert gappy[0].rect == Rect(base, 0, base + 1, 10)
    assert gappy[0].area == 10


def test_areas_can_exceed_64_bits_and_stay_exact():
    extent = Rect(COORD_MIN, 0, COORD_MAX, 2)
    # one covered strip of height 1, leaving a missing strip of height 1
    sheets = [("S1", Rect(COORD_MIN, 0, COORD_MAX, 1))]
    regions = analyze_rects(extent, sheets)
    missing = [r for r in regions if r.kind == "missing"]
    assert len(missing) == 1
    assert missing[0].rect == Rect(COORD_MIN, 1, COORD_MAX, 2)
    huge_area = WIDTH
    assert huge_area > 2**63
    assert missing[0].area == huge_area
    stats = Stats.from_issues(regions)
    assert stats.missing_area == huge_area
    assert stats.problem_area == huge_area


def test_area_outside_handles_super_64bit_totals():
    huge_rect = Rect(COORD_MIN, 0, COORD_MAX, 2)
    # removing a single square metre must be reflected exactly
    cutter = Rect(COORD_MIN, 0, COORD_MIN + 1, 1)
    pieces_area = area_outside([huge_rect], [cutter])
    assert pieces_area == huge_rect.area - 1
    assert area_outside([cutter], [huge_rect]) == 0


def test_parse_extent_rejects_out_of_range_with_reason():
    with pytest.raises(service.ValidationError) as exc:
        service.parse_extent(str(COORD_MAX), "0", str(COORD_MAX + 1), "1")
    assert any("超出有符号 64 位" in r for r in exc.value.reasons)
    with pytest.raises(service.ValidationError) as exc:
        service.parse_extent(str(COORD_MIN - 1), "0", "1", "1")
    assert any("超出有符号 64 位" in r for r in exc.value.reasons)
    # non-integer input still fails cleanly
    with pytest.raises(service.ValidationError):
        service.parse_extent("1.5", "0", "1", "1")


def test_parse_sheet_lines_reject_out_of_range():
    with pytest.raises(service.ValidationError) as exc:
        service.parse_sheet_lines(
            f"A 0 0 {COORD_MAX + 1} 1", max_entries=10, context="图幅清单"
        )
    assert any("超出有符号 64 位" in r for r in exc.value.reasons)


def test_parse_batch_json_strict_integer_contract():
    good = [{"no": "A", "x0": COORD_MIN, "y0": 0, "x1": COORD_MAX, "y1": 1}]
    entries = service.parse_batch_json(good)
    assert entries[0].rect == Rect(COORD_MIN, 0, COORD_MAX, 1)

    def expect_error(payload):
        with pytest.raises(service.ValidationError) as exc:
            service.parse_batch_json(payload)
        return exc.value.reasons

    # floats, booleans, strings and missing values are all refused, never coerced
    reasons = expect_error([{"no": "A", "x0": 1.0, "y0": 0, "x1": 2, "y1": 1}])
    assert any("整数" in r for r in reasons)
    reasons = expect_error([{"no": "A", "x0": True, "y0": 0, "x1": 2, "y1": 1}])
    assert any("整数" in r for r in reasons)
    reasons = expect_error([{"no": "A", "x0": "1", "y0": "0", "x1": "2", "y1": "1"}])
    assert any("整数" in r for r in reasons)
    # out of range carries the bound explanation
    reasons = expect_error(
        [{"no": "A", "x0": 0, "y0": 0, "x1": COORD_MAX + 1, "y1": 1}]
    )
    assert any("超出有符号 64 位" in r for r in reasons)
    # Python-side parsing preserves the full integer even beyond 2^53
    entries = service.parse_batch_json(
        [{"no": "A", "x0": 2**53 + 1, "y0": 0, "x1": 2**53 + 2, "y1": 1}]
    )
    assert entries[0].rect.width == 1


def test_parse_version_rules():
    assert service.parse_version(None) is None
    assert service.parse_version("") is None
    assert service.parse_version(3) == 3
    assert service.parse_version("3") == 3  # HTML form fallback
    for bad in (1.5, True, "abc", [], {}):
        with pytest.raises(service.ValidationError):
            service.parse_version(bad)  # type: ignore[arg-type]


def test_extreme_correction_plan_uses_exact_big_areas(tmp_path):
    """An accepted correction at huge coordinates recomputes only its window."""

    from adjcheck.db import Database

    db = Database(str(tmp_path / "big.sqlite3"))
    db.init_schema()
    base = 2**53 + 1000
    extent = Rect(base - 100, 0, base + 100, 10)
    sheets = [
        ("A", Rect(base - 100, 0, base, 10)),
        ("B", Rect(base + 1, 0, base + 100, 10)),
    ]
    service.create_project(
        db, "BIG", extent, [BatchEntry(no, r) for no, r in sheets]
    )
    project, stored, issues = db.get_state("BIG")
    gap = [i for i in issues if i.kind == "missing"]
    assert len(gap) == 1 and gap[0].rect == Rect(base, 0, base + 1, 10)
    assert gap[0].rect.area == 10

    # fill the one-metre-wide gap exactly
    plan = service.build_plan(
        project,
        stored,
        issues,
        [BatchEntry("C", Rect(base, 0, base + 1, 10))],
        expected_version=0,
    )
    assert plan.accepted, plan.reasons
    assert plan.eliminated.missing_area == 10
    assert plan.after.problem_area == 0
    ok, message = service.commit_plan(db, "BIG", plan)
    assert ok, message
    _, _, after_issues = db.get_state("BIG")
    assert after_issues == []


def test_rejected_out_of_range_correction_changes_nothing(tmp_path):
    from adjcheck.db import Database

    db = Database(str(tmp_path / "big.sqlite3"))
    db.init_schema()
    service.create_project(
        db,
        "P",
        Rect(0, 0, 100, 100),
        [BatchEntry("A", Rect(0, 0, 50, 100)), BatchEntry("B", Rect(60, 0, 100, 100))],
    )
    project, sheets, issues = db.get_state("P")
    # the JSON boundary is where raw out-of-range values arrive; Rect itself
    # can never be constructed with an out-of-range coordinate.
    with pytest.raises(service.ValidationError) as exc:
        batch = service.parse_batch_json(
            [{"no": "A", "x0": 0, "y0": 0, "x1": COORD_MAX + 1, "y1": 100}]
        )
        service.build_plan(project, sheets, issues, batch, expected_version=0)
    assert any("超出有符号 64 位" in r for r in exc.value.reasons)
    # catalog, result and version are all untouched
    project2, sheets2, issues2 = db.get_state("P")
    assert project2.version == 0
    assert [(s.no, s.rect) for s in sheets2] == [(s.no, s.rect) for s in sheets]
    assert [i.id for i in issues2] == [i.id for i in issues]
    assert Stats.from_issues(issues2) == Stats.from_issues(issues)
