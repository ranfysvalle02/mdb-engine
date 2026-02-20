#!/usr/bin/env python3
"""
Update coverage and test-count badges in README.md after a test-coverage run.

Reads coverage.json (produced by --cov-report=json:coverage.json) and the
pytest result line from the last run to produce shields.io badge URLs.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COV_JSON = ROOT / "coverage.json"
README = ROOT / "README.md"

BADGE_COV = "![Coverage]"
BADGE_TESTS = "![Tests]"


def _color(pct: float) -> str:
    if pct >= 90:
        return "brightgreen"
    if pct >= 80:
        return "green"
    if pct >= 70:
        return "yellow"
    return "red"


def _shields_url(label: str, value: str, color: str) -> str:
    safe_value = value.replace("-", "--").replace(" ", "_").replace("%", "%25")
    return f"https://img.shields.io/badge/{label}-{safe_value}-{color}"


def main() -> None:
    if not COV_JSON.exists():
        print("No coverage.json found — skipping badge update.")
        return

    data = json.loads(COV_JSON.read_text())
    pct = data.get("totals", {}).get("percent_covered", 0)
    pct_display = f"{pct:.0f}%"
    color = _color(pct)

    stmts = data.get("totals", {}).get("num_statements", 0)
    missed = data.get("totals", {}).get("missing_lines", 0)
    covered = stmts - missed

    cov_url = _shields_url("coverage", pct_display, color)
    tests_url = _shields_url("statements", f"{covered}%2F{stmts}", color)

    readme_text = README.read_text()

    cov_badge = f"[![Coverage]({cov_url})]()"
    tests_badge = f"[![Tests]({tests_url})]()"

    badge_re_cov = re.compile(r"\[!\[Coverage\]\([^)]*\)\]\([^)]*\)")
    badge_re_tests = re.compile(r"\[!\[Tests\]\([^)]*\)\]\([^)]*\)")

    if badge_re_cov.search(readme_text):
        readme_text = badge_re_cov.sub(cov_badge, readme_text)
    else:
        # Insert after the last existing badge line in the header block
        readme_text = _insert_after_last_badge(readme_text, cov_badge)

    if badge_re_tests.search(readme_text):
        readme_text = badge_re_tests.sub(tests_badge, readme_text)
    else:
        readme_text = _insert_after_last_badge(readme_text, tests_badge)

    README.write_text(readme_text)
    print(f"Updated README.md — coverage: {pct_display}, statements: {covered}/{stmts}")


def _insert_after_last_badge(text: str, new_badge: str) -> str:
    """Insert a new badge line after the last existing badge in the header."""
    lines = text.split("\n")
    last_badge_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("[!["):
            last_badge_idx = i
    if last_badge_idx >= 0:
        lines.insert(last_badge_idx + 1, new_badge)
    else:
        lines.insert(2, new_badge)
    return "\n".join(lines)


if __name__ == "__main__":
    main()
