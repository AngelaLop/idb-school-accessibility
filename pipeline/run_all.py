"""
Run the full CIMA pipeline in order.

Must be executed from the project root:
    uv run python pipeline/run_all.py
"""

import subprocess
import sys
import time
from pathlib import Path

STEPS = [
    ("01 — Build CIMA files",        "pipeline/01_build_cima.py"),
    ("02 — QC coordinates",          "pipeline/02_qc_coordinates.py"),
    ("03 — Coverage assessment",     "pipeline/03_coverage_assessment.py"),
]
# Steps 04+ are run on demand: 04_geocode_missing requires --countries,
# 05_base_k_12_clean joins against LAC_merged, 06_pop_grid downloads
# WorldPop rasters, 07_schools_context emits the derived layer.


def run_step(label, script, extra_args=None):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {script}")
    print(f"{'='*60}\n")

    cmd = [sys.executable, script]
    if extra_args:
        cmd.extend(extra_args)

    t0 = time.time()
    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent)
    elapsed = time.time() - t0

    status = "OK" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    print(f"\n  [{status}] {label} — {elapsed:.1f}s")
    return result.returncode == 0


def main():
    print("=" * 60)
    print("  CIMA Pipeline — Full Run")
    print("=" * 60)

    t_total = time.time()
    results = {}

    for label, script in STEPS:
        ok = run_step(label, script)
        results[label] = ok
        if not ok:
            print(f"\n  Pipeline stopped at: {label}")
            break

    elapsed = time.time() - t_total
    print(f"\n{'='*60}")
    print(f"  Pipeline finished in {elapsed:.0f}s")
    for label, ok in results.items():
        icon = "OK" if ok else "FAIL"
        print(f"    [{icon}] {label}")
    print(f"{'='*60}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
