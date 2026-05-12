"""主入口: 一键跑一轮。"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent
ARTIFACTS_BASE = REPO_ROOT / "artifacts"


def run_round(n: int, extra_args: list):
    art = ARTIFACTS_BASE / f"round-{n}"
    art.mkdir(parents=True, exist_ok=True)
    pytest_report = art / "pytest_report.json"

    cmd = [
        sys.executable, "-m", "pytest",
        str(REPO_ROOT / "tests" / "qa_full"),
        f"--round={n}",
        "--json-report",
        f"--json-report-file={pytest_report}",
        "-v",
    ] + extra_args
    print("RUN:", " ".join(cmd))
    rc = subprocess.call(cmd, cwd=str(REPO_ROOT))
    print(f"pytest exit code: {rc}")

    from tests.qa_full.runners.report import generate_report
    from tests.qa_full.runners.budget_guard import BudgetGuard
    from tests.qa_full.runners.bugs_md import write_bugs_md
    bg = BudgetGuard(art)
    generate_report(art, n, bg.report())
    write_bugs_md(n)
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    args, extra = ap.parse_known_args()
    sys.exit(run_round(args.round, extra))
