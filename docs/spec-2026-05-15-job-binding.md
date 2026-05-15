# Spec 2026-05-15 — 候选人岗位归属确定性绑定

> 状态: design
> 日期: 2026-05-15

## 背景

HR 同时招多个职位(例如 "产品经理" 和 "开发") 时,当前系统**回答不了"我给岗位 A 打了招呼的人有哪些"** 这个最基础的问题。原因是 F3 一键打招呼 → F4 收集信息 这条主流程上, `IntakeCandidate.job_id` / `Resume.job_id` **永远是 NULL**:

### 现状链路

1. **F3 路径** (`/api/recruit/evaluate_and_record`):
   - 请求体携带正确的 `job_id` (`recruit_bot/schemas.py:40`)。
   - `evaluate_and_record` 用 `job_id` 做三件事: 查 daily_cap、取岗位能力模型、F2 打分写 `MatchingResult(resume_id, job_id)`。
   - 然后调 `upsert_resume_by_boss_id(db, user_id, candidate)` —— **`job_id` 没传进去** (`recruit_bot/service.py:256`)。
   - `_ensure_candidate_for_f3` 的函数签名**不接收 `job_id` 参数**,创建出来的 `IntakeCandidate.job_id = NULL`。
   - `upsert_resume_by_boss_id` 创建 Resume 时也**不写 `job_id`**。

2. **F4 路径** (`/api/intake/candidates/register`):
   - `IntakeService.ensure_candidate` 只在**新建**候选人时尝试写 `job_id`,且只能用 `match_job_title` 做 bigram 字符相似度模糊匹配(阈值 0.7,典型 case "产品经理" vs "高级产品经理" 相似度 0.6 直接落空)。
   - 命中已存在行(F3 已经先建过)走 `else` 分支,**job_id 保持 NULL 不变**。

3. **promote_to_resume** (`im_intake/promote.py:90,121`) 复制 `candidate.job_id → resume.job_id` 的逻辑是对的,但源头 `candidate.job_id` 本身为 NULL,等于没复制。

### 失败模式

- **F2 打分失败 = 人间蒸发**: AI 批量筛选只认 `MatchingResult`。打招呼时若 F2 报错 / 岗位无能力模型 → 没有 `MatchingResult` → 该候选人不属于任何岗位池。
- **跨岗位污染**: `MatchingResult` 是 `(resume_id, job_id)` 多对多。若哪天对岗位 B 跑 "全员打分",岗位 A 的候选人也会出现在岗位 B 的 `_eligible_candidate_query` 结果里。
- **无法做"我给 A 岗打招呼的人"查询**: 这个最基础的诉求,系统目前答不了——它能答的是"对 A 岗打过分的人",在 F2 失败、跨岗位打分等情况下并不等价。

## 目标

1. **F3 路径透传**: `evaluate_and_record(job_id=A)` 必须把 `job_id` 写进新建的 `IntakeCandidate.job_id` 和 `Resume.job_id`,**确定性,非猜测**。
2. **F4 路径回填**: `ensure_candidate` 命中已存在行**且** `c.job_id IS NULL` 时,允许用 `match_job_title` 兜底回填; **非 NULL 时不覆盖** (first-write wins)。
3. **历史 NULL 回填**: 迁移 0029 扫 `IntakeCandidate.job_id IS NULL`,从 `MatchingResult` 反推回填,同步到 `Resume.job_id`。
4. **同 boss_id 二次 greet 不同 job 时**: candidate `job_id` 保持第一次的(first-write wins),写一条 `f3_job_rebind_attempt` 审计行记 cross-job 事实,便于事后查。

## 非目标

- **不改读取路径**: `list_matched_for_job` / 匹配页 / 简历库的查询行为本轮**不动**。本轮只确保**写入**可靠。读取侧改造(例如匹配页默认按 `job_id` 过滤)另开 spec。
- **不改 `match_job_title` 算法**: 阈值 0.7 保留。回填策略不依赖更智能的字符匹配。
- **不引入"候选人-多岗位"绑定表**: 多岗位关系已经通过 `MatchingResult` 表达,本轮的 `job_id` 是 "**primary/首次绑定岗位**" 语义。
- **不动 `core/` 目录**: 项目硬约束。所有改动都在 `app/modules/recruit_bot/` 、 `app/modules/im_intake/` 、 `migrations/versions/`。
- **不改前端**: `IntakeCandidate` 的 `job_id` 字段、`?job_id=` 过滤器、Intake 页面按岗位筛已经存在,字段一被填上就 Just Work。

## 数据模型

无新表、无新列。已有字段:

| 字段 | 位置 | 现状 | 本轮 |
|---|---|---|---|
| `IntakeCandidate.job_id` | `im_intake/candidate_model.py:16` | FK `jobs.id ON DELETE SET NULL`, nullable | **改为在 F3/F4 写入路径上确定性填值** |
| `Resume.job_id` | `resume/models.py:51` | Integer, nullable, 无 FK | **同上;promote 已能从 candidate 复制,只需源头不 NULL** |

## 写入路径改动

### 1. `_ensure_candidate_for_f3` (recruit_bot/service.py)

签名加 `job_id: int | None = None`,新建分支写入 `job_id`,upsert 分支若 `c.job_id` 为 NULL 且传入非 None 则回填(first-write wins 的另一面: 第一次写入永远胜出, NULL → 实值 不算覆盖)。

```python
def _ensure_candidate_for_f3(
    db, *, user_id, boss_id, name,
    education="", work_years=0, intended_job="", skills_csv="",
    latest_work_brief="", raw_text="",
    job_id: int | None = None,   # 新加
) -> IntakeCandidate:
    c = ... query existing ...
    if c is None:
        c = IntakeCandidate(..., job_id=job_id, ...)
        ...
    else:
        # upsert 分支: 字段空才覆盖
        ...
        if job_id and not c.job_id:   # 新加: NULL → 实值
            c.job_id = job_id
        elif job_id and c.job_id and c.job_id != job_id:
            # 二次 greet 不同岗位 —— first-write wins + 审计
            _audit_cross_job_attempt(c, attempted_job_id=job_id)
        db.commit()
    return c
```

### 2. `upsert_resume_by_boss_id` (recruit_bot/service.py)

签名加 `job_id`,透传给 `_ensure_candidate_for_f3`,新建 Resume 时写入,upsert 分支若 Resume.job_id 为 NULL 则回填。

### 3. `evaluate_and_record` (recruit_bot/service.py)

调用 `upsert_resume_by_boss_id` 时传入它已经有的 `job_id` 参数。

### 4. `IntakeService.ensure_candidate` (im_intake/service.py:52)

命中已存在行时**新增回填**:

```python
if c is not None:
    if name and not c.name:
        c.name = name
    # 新加: 已存在行 job_id 为 NULL 时,允许 fuzzy match 回填
    if c.job_id is None and job_intention:
        jobs = self.db.query(Job).filter_by(user_id=self.user_id).all()
        matched = match_job_title(job_intention, [...], threshold=0.7)
        if matched:
            c.job_id = matched
    self.db.commit()
```

## 迁移 0029

**目的**: 把历史 `IntakeCandidate.job_id IS NULL` 行回填到 MatchingResult 反推的 job_id。

**算法**:

```
for cand in IntakeCandidate where job_id IS NULL and promoted_resume_id IS NOT NULL:
    matches = MatchingResult where resume_id = cand.promoted_resume_id
    if len(matches) == 0:
        skip  # 真的没线索, 留 NULL
    elif len(matches) == 1:
        cand.job_id = matches[0].job_id
    else:
        # 多 job 打过分 —— 选 hard_gate_passed=1 且 total_score 最高的
        passed = [m for m in matches if m.hard_gate_passed == 1]
        winner = max(passed or matches, key=lambda m: m.total_score)
        cand.job_id = winner.job_id

    # 同步到 Resume
    resume = Resume where id = cand.promoted_resume_id
    if resume.job_id IS NULL:
        resume.job_id = cand.job_id

    # 审计
    audit f_stage='f4_backfill' action='migration_0029' entity_id=cand.id ...
```

**为什么不在写入路径上回填**: 写入路径是热路径(每次 greet 都跑),不应该额外查 MatchingResult。一次性迁移更合适。新进数据走 §写入路径 1-4 保证不再产生 NULL。

**downgrade**: no-op(回填型迁移)。

## first-write wins 决策依据

同一候选人在 Boss 上可能被同一 HR 招呼到不同岗位。三种处理:

| 策略 | 优点 | 缺点 |
|---|---|---|
| **first-write wins** ✅ | 候选人 "primary" 归属稳定,后续操作可预期 | 二次 greet 信息只在审计行里,日常查询看不到 |
| last-write wins | 反映最新意图 | 候选人会在不同岗位池间漂移,排面时不稳定 |
| 多对多绑定表 | 完整保留 | 新增表 + schema + 读取改造,本轮范围爆炸;且 MatchingResult 已经能表达 |

选 first-write + 审计兜底。如果需要回答 "这个人都被招呼过哪些岗位",查 `MatchingResult.job_id` (打分留痕) 或 `audit_events` 里 `f_stage='f3_job_rebind_attempt'` 的行。

## 测试计划 (TDD)

| Task | 红测断言 | 实现 |
|---|---|---|
| T1 | F3 路径 evaluate_and_record(job_id=A) 后 candidate.job_id == A 且 resume.job_id == A | `_ensure_candidate_for_f3` + `upsert_resume_by_boss_id` 加 job_id 参数 |
| T2.1 | F4 ensure_candidate 命中已存在且 job_id 非 NULL 时,新一次调用不覆盖 | ensure_candidate else 分支不动 job_id |
| T2.2 | F4 ensure_candidate 命中已存在且 job_id 为 NULL 时,可用 fuzzy match 回填 | else 分支新增回填逻辑 |
| T3 | 迁移 0029 跑完后,有 MatchingResult 的历史 NULL candidate 被填上正确 job_id | 写 migration 0029 |
| T4 | 同 boss_id 二次 greet 不同 job_id 时 candidate.job_id 不变 + 审计行产生 | _audit_cross_job_attempt |

## 范围 & 不在范围内

**范围内**:
- `app/modules/recruit_bot/service.py` (写入)
- `app/modules/im_intake/service.py` (回填) — 注意此文件在 `core/` 之外
- `migrations/versions/0029_backfill_intake_job_id.py`
- 上述对应的测试

**不在范围内**:
- 任何 `core/*` 文件 (项目硬约束)
- `list_matched_for_job` 读取行为
- 前端 Vue 文件
- `match_job_title` 算法
- 任何新增 schema / API endpoint
