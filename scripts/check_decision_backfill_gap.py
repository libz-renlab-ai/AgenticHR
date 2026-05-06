"""spec 0429-D 收尾 P2-a — 检查 0024 迁移回填差额。

0024 迁移回填条件: matching_results.job_action 非空 AND
intake_candidates.promoted_resume_id = matching_results.resume_id 关联存在。

可能漏数据场景:
  - resume 存在但无对应 IntakeCandidate (老数据 promote 链路缺失)
  - candidate.promoted_resume_id 是 NULL (1:1 约束之前的历史数据)

本脚本只读 + dry-run 报数, 不改 DB。差额 > 0 时打印漏行 (resume_id, job_id, action, owner)。

用法:
    python scripts/check_decision_backfill_gap.py [path/to/test.db]
默认查项目根 ./test.db
"""
from __future__ import annotations

import sys
from pathlib import Path
from sqlalchemy import create_engine, text


def main(db_path: str = "test.db") -> int:
    db_file = Path(db_path)
    if not db_file.exists():
        print(f"[ERROR] DB not found: {db_file.resolve()}")
        return 2
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        total = conn.execute(text(
            "SELECT COUNT(*) FROM matching_results WHERE job_action IN ('passed','rejected')"
        )).scalar() or 0
        backfilled = conn.execute(text(
            "SELECT COUNT(*) FROM job_candidate_decisions"
        )).scalar() or 0
        orphans = conn.execute(text("""
            SELECT mr.id, mr.resume_id, mr.job_id, mr.job_action
            FROM matching_results mr
            WHERE mr.job_action IN ('passed','rejected')
              AND mr.resume_id NOT IN (
                  SELECT promoted_resume_id FROM intake_candidates
                  WHERE promoted_resume_id IS NOT NULL
              )
        """)).fetchall()

        print(f"matching_results 决策行数 (job_action != NULL): {total}")
        print(f"job_candidate_decisions 实际行数:                {backfilled}")
        print(f"无对应 IntakeCandidate.promoted_resume_id 孤儿数: {len(orphans)}")
        if orphans:
            print("\n孤儿明细 (max 50):")
            for r in orphans[:50]:
                print(f"  matching_result.id={r[0]} resume_id={r[1]} job_id={r[2]} action={r[3]}")
            if len(orphans) > 50:
                print(f"  ... +{len(orphans) - 50} more")
            print("\n推荐处置:")
            print("  - 若孤儿数 < 10: 联系 HR 在新匹配 Tab 重标")
            print("  - 若较多: 写补数迁移按 Resume.user_id 反推 candidate (匹配 phone/boss_id)")
            return 1
    print("\n[OK] 无回填差额")
    return 0


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "test.db"
    raise SystemExit(main(db))
