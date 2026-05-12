"""聚合 pytest 结果 + verifier 结果 → HTML。"""
import json
from pathlib import Path

import jinja2

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def generate_report(artifacts_dir: Path, round_no: int, budget_report: dict):
    pytest_json = artifacts_dir / "pytest_report.json"
    if not pytest_json.exists():
        print(f"no pytest_report.json at {pytest_json}")
        return
    data = json.loads(pytest_json.read_text(encoding="utf-8"))
    results = []
    for t in data.get("tests", []):
        nodeid = t["nodeid"]
        results.append({
            "id": nodeid,
            "status": t["outcome"],
            "reason": (t.get("call", {}).get("longrepr") or "")[:300],
            "screenshot": _maybe_screenshot(artifacts_dir, nodeid),
        })
    summary = data.get("summary", {})
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    html = env.get_template("report.html.j2").render(
        round_no=round_no,
        total=summary.get("total", 0),
        passed=summary.get("passed", 0),
        failed=summary.get("failed", 0),
        skipped=summary.get("skipped", 0),
        budget=json.dumps(budget_report, indent=2, ensure_ascii=False),
        results=results,
    )
    out = artifacts_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    print(f"report -> {out}")


def _maybe_screenshot(artifacts_dir: Path, nodeid: str) -> str:
    fn_name = nodeid.split("::")[-1]
    cand = list((artifacts_dir / "screenshots").glob(f"*{fn_name}*.png"))
    if cand:
        return f"screenshots/{cand[0].name}"
    return ""
