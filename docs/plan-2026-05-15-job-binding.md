# Plan 2026-05-15 — 候选人岗位归属确定性绑定 (TDD 实现计划)

> 配套设计文档: [`spec-2026-05-15-job-binding.md`](./spec-2026-05-15-job-binding.md)
> 日期: 2026-05-15

## 任务流(TDD 红→绿)

每个 task 都先写红测、再实现、确认绿,然后再开下一个 task。

---

### T1: F3 路径透传 job_id (红→绿→重构)

**红测** — `tests/modules/recruit_bot/test_f3_job_binding.py::TestF3JobBinding`:

- `test_evaluate_and_record_writes_job_id_to_candidate`
  断言: `evaluate_and_record(db, user_id=1, job_id=10, candidate=...)` 后,该 boss_id 的 IntakeCandidate.job_id == 10。
- `test_evaluate_and_record_writes_job_id_to_resume`
  断言: 同上,Resume.job_id == 10。
- `test_upsert_resume_by_boss_id_accepts_job_id_kwarg`
  断言: 直接调 `upsert_resume_by_boss_id(db, user_id=1, candidate=..., job_id=10)` 也能写入。
- `test_upsert_existing_candidate_backfills_null_job_id`
  setup: 先用 job_id=None 建一行 (走老路径模拟历史数据),再调 `evaluate_and_record(job_id=10)`,断言 candidate.job_id 被回填成 10。

**实现**:
1. `_ensure_candidate_for_f3` 加 `job_id: int | None = None` 关键字参数。
2. 新建分支: `IntakeCandidate(..., job_id=job_id, ...)`。
3. upsert 分支: `if job_id and not c.job_id: c.job_id = job_id`。
4. `upsert_resume_by_boss_id` 加 `job_id: int | None = None`,新建 Resume 时填,upsert 分支 NULL 时回填,透传给 `_ensure_candidate_for_f3`。
5. `evaluate_and_record` 调用处加 `job_id=job_id` 实参。

**重构**: 检查现有签名调用方,确保新参数关键字可选,不破坏现有调用点。

---

### T2: F4 ensure_candidate 回填 (红→绿)

**红测** — `tests/modules/im_intake/test_ensure_candidate_job_id.py`:

- `test_ensure_candidate_new_with_job_intention_fuzzy_matches`
  setup: 数据库里有 Job(title="产品经理"),调 `ensure_candidate(boss_id=..., job_intention="产品经理")`,断言 candidate.job_id 是该 job.id。(确保已有行为不破坏)
- `test_ensure_candidate_existing_with_job_id_not_overwritten`
  setup: 已有 candidate.job_id=10,调 `ensure_candidate(boss_id=同, job_intention="另一个岗位")`(数据库里另有 Job(title="另一个岗位")),断言 candidate.job_id 仍是 10。
- `test_ensure_candidate_existing_with_null_job_id_backfills`
  setup: 已有 candidate.job_id=None (模拟 F3 老路径建的行),数据库里有 Job(title="产品经理"),调 `ensure_candidate(boss_id=同, name=..., job_intention="产品经理")`,断言 candidate.job_id 被填上对应 job.id。
- `test_ensure_candidate_existing_null_no_fuzzy_match_stays_null`
  setup: candidate.job_id=None,job_intention 跟所有岗位相似度 <0.7,断言 candidate.job_id 仍是 None。

**实现**: `ensure_candidate` 的 `else` 分支(命中已存在行)新增:

```python
if c.job_id is None and job_intention:
    jobs = self.db.query(Job).filter_by(user_id=self.user_id).all()
    matched = match_job_title(job_intention, [{"id": j.id, "title": j.title} for j in jobs], threshold=0.7)
    if matched:
        c.job_id = matched
```

注意 `self.db.commit()` 已经在原代码 elif 分支里,保留。

---

### T3: 同 boss_id 二次 greet 不同岗位 — first-write wins + 审计 (红→绿)

**红测** — `tests/modules/recruit_bot/test_f3_job_binding.py::TestCrossJobRebind`:

- `test_second_greet_different_job_keeps_first_job_id`
  setup: `evaluate_and_record(job_id=10)` 建 candidate.job_id=10,再 `evaluate_and_record(job_id=20)` 同 boss_id,断言 candidate.job_id 仍是 10。
- `test_second_greet_different_job_writes_audit_event`
  断言上一步后,`audit_events` 里有 `f_stage='f3_job_rebind_attempt'` 且 `entity_id=candidate.id`、input_payload 含 `attempted_job_id=20` 的行。

**实现**: `_ensure_candidate_for_f3` upsert 分支补:

```python
if job_id and c.job_id and c.job_id != job_id:
    try:
        from app.core.audit.logger import log_event as _le
        _le(
            f_stage="f3_job_rebind_attempt", action="cross_job_greet",
            entity_type="intake_candidate", entity_id=c.id,
            input_payload={
                "boss_id": c.boss_id,
                "primary_job_id": c.job_id,
                "attempted_job_id": job_id,
            },
            reviewer_id=user_id,
        )
    except Exception:
        pass
```

---

### T4: 迁移 0029 历史 NULL 回填 (红→绿)

**红测** — `tests/modules/im_intake/test_migration_0029.py`:

- `test_migration_0029_backfills_single_match`
  setup: 用 Alembic command.upgrade(到 0028),手工塞:
    - Job(id=10, user_id=1, title="A")
    - IntakeCandidate(id=1, user_id=1, boss_id="b1", job_id=NULL, promoted_resume_id=100)
    - Resume(id=100, user_id=1, boss_id="b1", job_id=NULL)
    - MatchingResult(resume_id=100, job_id=10, hard_gate_passed=1, total_score=70)
  upgrade(0029),断言: candidate.job_id == 10, resume.job_id == 10。
- `test_migration_0029_backfills_multi_match_picks_best`
  setup: 同上,但有两个 MatchingResult: (job_id=10, hard_gate_passed=1, score=60) 和 (job_id=20, hard_gate_passed=1, score=80)。断言: candidate.job_id == 20。
- `test_migration_0029_prefers_passed_over_higher_unpassed`
  setup: MatchingResult(job_id=10, hard_gate_passed=1, score=50) 和 (job_id=20, hard_gate_passed=0, score=90)。断言: candidate.job_id == 10。
- `test_migration_0029_skips_when_no_matching_result`
  setup: candidate.job_id=NULL,没有任何 MatchingResult。断言: 仍是 NULL,不报错。
- `test_migration_0029_does_not_overwrite_existing_job_id`
  setup: candidate.job_id=5,有 MatchingResult(job_id=10)。断言: candidate.job_id 仍是 5。

**实现**: `migrations/versions/0029_backfill_intake_job_id.py`:

```python
revision = "0029"
down_revision = "0028"

def upgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, promoted_resume_id FROM intake_candidates "
        "WHERE job_id IS NULL AND promoted_resume_id IS NOT NULL"
    )).fetchall()
    for cand_id, resume_id in rows:
        matches = bind.execute(sa.text(
            "SELECT job_id, hard_gate_passed, total_score "
            "FROM matching_results WHERE resume_id = :rid"
        ), {"rid": resume_id}).fetchall()
        if not matches:
            continue
        passed = [m for m in matches if m[1] == 1]
        pool = passed or list(matches)
        winner = max(pool, key=lambda m: m[2] or 0)
        chosen_job_id = winner[0]
        bind.execute(sa.text(
            "UPDATE intake_candidates SET job_id = :j WHERE id = :c AND job_id IS NULL"
        ), {"j": chosen_job_id, "c": cand_id})
        bind.execute(sa.text(
            "UPDATE resumes SET job_id = :j WHERE id = :r AND job_id IS NULL"
        ), {"j": chosen_job_id, "r": resume_id})

def downgrade():
    pass  # 回填型迁移,无逆操作
```

---

### T5: 验收 (端到端)

1. `pytest tests/modules/recruit_bot/ -x` 全绿
2. `pytest tests/modules/im_intake/ -x` 全绿
3. `pytest tests/integration/test_f3_greet_via_candidate.py -x` 全绿
4. `pytest tests/ -x --ignore=tests/integration/test_f2_e2e_smoke.py` 全量,看是否有意外回归(已知 ai_screening 有 pre-existing 失败,可单独看)
5. 端到端 dry-run: 起一个临时 db, 模拟 F3 greet(job_id=10) → F4 register → 查 candidate.job_id 应为 10。

---

### T6: 提交序列

```
git checkout -b worktree-job-binding (或直接 main 累积 commit)
# T0
git add docs/spec-2026-05-15-job-binding.md docs/plan-2026-05-15-job-binding.md
git commit -m "docs(spec): 候选人岗位归属确定性绑定 — 设计 + TDD 计划"

# T1+T3 (实现合并提交,因为两者共享 _ensure_candidate_for_f3 改动)
git add tests/modules/recruit_bot/test_f3_job_binding.py app/modules/recruit_bot/service.py
git commit -m "feat(recruit_bot): F3 路径透传 job_id 到 candidate+resume + 二次 greet 审计"

# T2
git add tests/modules/im_intake/test_ensure_candidate_job_id.py app/modules/im_intake/service.py
git commit -m "feat(intake): ensure_candidate 命中已存在行时回填 NULL job_id"

# T4
git add tests/modules/im_intake/test_migration_0029.py migrations/versions/0029_backfill_intake_job_id.py
git commit -m "feat(migration): 0029 从 MatchingResult 反推回填历史 NULL job_id"
```

不主动 push、不开 PR(用户自定夺)。
