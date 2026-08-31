"""
test_fixes.py — verifies the fix helpers without touching BigQuery or the API.
Run from repo root:  python test_fixes.py
"""

import sys
from datetime import date

from agent.common.handoff import (
    RunContext,
    current_analysis_month,
    parse_json_response,
    extract_text,
)


class _Block:
    def __init__(self, type, text=None):
        self.type = type
        self.text = text


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


def test_analysis_month_stable():
    m = current_analysis_month(date(2025, 1, 31))
    assert m == "2025-01-01", m
    # last day of month must still map to first of THAT month
    assert current_analysis_month(date(2025, 12, 31)) == "2025-12-01"
    print("  ✓ analysis_month maps to first-of-month")


def test_run_context_roundtrip():
    ctx = RunContext(run_id="r1", supplier_id="SUP001", grounding_skus=["ELC-0011"])
    d = ctx.to_dict()
    assert d["supplier_id"] == "SUP001"
    back = RunContext.from_state({"run_id": "r1", "supplier_id": "SUP001",
                                  "grounding_skus": ["ELC-0011"]})
    assert back.grounding_skus == ["ELC-0011"]
    print("  ✓ RunContext round-trips through state")


def test_extract_text_handles_non_text_first_block():
    # tool_use block first, text second — must not crash, must find the text
    resp = _Resp([_Block("tool_use"), _Block("text", "hello")])
    assert extract_text(resp) == "hello"
    # empty content list -> "" not IndexError
    assert extract_text(_Resp([])) == ""
    print("  ✓ extract_text tolerates non-text / empty content")


def test_parse_json_strips_fences():
    resp = _Resp([_Block("text", '```json\n{"a": 1}\n```')])
    assert parse_json_response(resp) == {"a": 1}
    # bad json with default -> returns default instead of raising
    bad = _Resp([_Block("text", "not json")])
    assert parse_json_response(bad, default={}) == {}
    print("  ✓ parse_json_response strips fences and honours default")


def test_grounding_guard():
    # import the guard from validate (after fixes applied)
    try:
        from agent.nodes.validate import validate_grounding
    except ImportError:
        print("  · validate_grounding not found — apply_fixes.py not run yet, skipping")
        return
    narrative = "SKU ELC-0011 is fine. But TOY-0040 shows problems."
    res = validate_grounding(narrative, grounding_skus=["ELC-0011"])
    flagged = [r["reported_value"] for r in res]
    assert flagged == ["TOY-0040"], flagged
    print("  ✓ grounding guard flags ungrounded SKU TOY-0040")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    print("Running fix verification tests\n")
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {t.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__} ERROR: {e}")
            failed += 1
    print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
    sys.exit(1 if failed else 0)
