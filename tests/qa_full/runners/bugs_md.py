"""从 pytest_report.json 抽失败项 → BUGS-qa-round-N.md。"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent


def write_bugs_md(round_no: int):
    art = REPO_ROOT / "artifacts" / f"round-{round_no}"
    pytest_json = art / "pytest_report.json"
    if not pytest_json.exists():
        return
    data = json.loads(pytest_json.read_text(encoding="utf-8"))
    fails = [t for t in data.get("tests", []) if t["outcome"] == "failed"]
    out = REPO_ROOT / f"BUGS-qa-round-{round_no}.md"
    lines = [f"# BUGS QA Round {round_no}\n\n", f"失败数: {len(fails)}\n\n"]
    for t in fails:
        nodeid = t["nodeid"]
        repr_ = t.get("call", {}).get("longrepr", "")
        lines.append(f"## {nodeid}\n\n")
        lines.append(f"```\n{repr_}\n```\n\n")
    out.write_text("".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    return out


if __name__ == "__main__":
    import sys
    write_bugs_md(int(sys.argv[1]))
