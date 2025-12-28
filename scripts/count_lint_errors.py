#!/usr/bin/env python3
"""Count linting errors from flake8 and isort output files."""

try:
    with open("/tmp/flake8_output.txt", "r") as f:
        flake8_count = sum(
            1
            for line in f
            if any(line.startswith(p) for p in ["mdb_engine", "tests", "scripts"])
        )
except OSError:
    flake8_count = 0

try:
    with open("/tmp/isort_output.txt", "r") as f:
        isort_count = sum(1 for line in f if "ERROR:" in line)
except OSError:
    isort_count = 0

total = flake8_count + isort_count

print("")
print("═" * 55)
print(f"Total linting errors: {total} (flake8: {flake8_count}, isort: {isort_count})")
print("═" * 55)
