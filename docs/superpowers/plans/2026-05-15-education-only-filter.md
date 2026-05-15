# F3 学历门槛筛选改造 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 F3 主动打招呼的筛选从五维 F2 打分换成「学历等级 + 名校标签」单维度门槛；门槛由扩展面板配置经 localStorage 持久化、随每次请求带在 body；后端仍写 MatchingResult 一行以兼容 screening UI。

**Architecture:** 后端新建纯函数 `education_check.py` 替换 `MatchingService.score_pair()` 调用；`schemas.py` 把 `strategy: str | None` 换成必填 `EducationFilter`，并改 `RecruitDecision.decision` Literal；`service.evaluate_and_record()` 删 competency 前置 + F2 打分 + `school_only` 分支，新增 MatchingResult upsert（education_score=100/0，其余维 0.0）；扩展端 popup 加设置卡片写 `chrome.storage.local`，content.js 启动循环时读取并放入请求体。

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic v2；pytest + pytest-asyncio；Chrome Extension MV3 (popup.html/popup.js/content.js)；pnpm (frontend)。

**Spec:** `docs/superpowers/specs/2026-05-15-education-only-filter-design.md`

---

## File Structure

**Create:**
- `app/modules/recruit_bot/education_check.py` — 纯函数 + 数据类
- `tests/modules/recruit_bot/test_education_check.py` — 单元测试

**Modify:**
- `app/modules/recruit_bot/schemas.py` — `EducationFilter` / `RecruitDecision` / `RecruitEvaluateRequest`
- `app/modules/recruit_bot/service.py` — 重写 `evaluate_and_record()`，写 MatchingResult
- `app/modules/recruit_bot/router.py` — 跟随 schema（基本无逻辑改）
- `tests/modules/recruit_bot/test_evaluate_and_record.py` — 改写既有测试以匹配新签名
- `tests/modules/recruit_bot/test_schemas.py` — schema 验证测试
- `tests/modules/recruit_bot/test_router_evaluate.py` — router 集成测试
- `tests/modules/recruit_bot/test_integration.py` — 端到端集成测试
- `edge_extension/popup.html` — 学历门槛卡片
- `edge_extension/popup.js` — `chrome.storage.local` 读写 + 校验
- `edge_extension/content.js:1075` 起的 POST body 段 + 决策枚举处理段（1100/1110/1114）

**Unchanged:**
- `app/modules/matching/*`（F2 screening 仍用）
- `core/competency/*`
- Resume / IntakeCandidate / Job / MatchingResult 模型与迁移

---

## Task 1: education_check 纯函数 (TDD)

**Files:**
- Create: `app/modules/recruit_bot/education_check.py`
- Test: `tests/modules/recruit_bot/test_education_check.py`

- [ ] **Step 1.1: 写失败测试**

`tests/modules/recruit_bot/test_education_check.py`:

```python
"""education_check 纯函数 — 学历等级 + 名校标签判定."""
import pytest
from app.modules.recruit_bot.education_check import (
    check_education_threshold, EducationFilter, EducationCheckResult,
)


def _f(min_level="本科", tags=None, require=False):
    return EducationFilter(
        min_level=min_level,
        prestigious_tags=tags or [],
        require_prestigious=require,
    )


class TestLevelOrdering:
    def test_higher_level_passes(self):
        r = check_education_threshold("硕士", [], _f("本科"))
        assert r.passed and r.level_pass

    def test_equal_level_passes(self):
        r = check_education_threshold("本科", [], _f("本科"))
        assert r.passed and r.level_pass

    def test_lower_level_fails(self):
        r = check_education_threshold("大专", [], _f("本科"))
        assert not r.passed and not r.level_pass

    def test_empty_education_fails(self):
        r = check_education_threshold("", [], _f("本科"))
        assert not r.passed and not r.level_pass

    def test_unknown_education_treated_as_zero(self):
        r = check_education_threshold("中专", [], _f("本科"))
        assert not r.passed

    def test_whitespace_trimmed(self):
        r = check_education_threshold("  硕士  ", [], _f("本科"))
        assert r.passed


class TestPrestigiousMatching:
    def test_require_false_always_passes_tier(self):
        r = check_education_threshold("本科", [], _f("本科", [], False))
        assert r.passed and r.prestigious_pass
        assert r.matched_tiers == []

    def test_require_false_records_matched_tiers_for_evidence(self):
        r = check_education_threshold("本科", ["211院校"], _f("本科", ["211"], False))
        assert r.passed
        assert r.matched_tiers == ["211"]

    def test_require_true_with_match_passes(self):
        r = check_education_threshold(
            "本科", ["985院校"], _f("本科", ["985", "211"], True)
        )
        assert r.passed and r.prestigious_pass
        assert "985" in r.matched_tiers

    def test_require_true_no_match_fails(self):
        r = check_education_threshold(
            "本科", ["普通本科"], _f("本科", ["985"], True)
        )
        assert not r.passed and not r.prestigious_pass

    def test_or_semantics_across_multiple_tags(self):
        r = check_education_threshold(
            "本科", ["双一流院校"], _f("本科", ["985", "211", "双一流"], True)
        )
        assert r.passed
        assert "双一流" in r.matched_tiers

    def test_qs_top_100_pattern_variants(self):
        for tag in ["QS_TOP_100", "QS TOP 100", "QS100", "世界排名前100"]:
            r = check_education_threshold(
                "硕士", [tag], _f("硕士", ["QS_TOP_100"], True)
            )
            assert r.passed, f"failed for {tag}"


class TestCombinedSemantics:
    def test_level_pass_prestigious_fail(self):
        r = check_education_threshold(
            "硕士", ["普通本科"], _f("本科", ["985"], True)
        )
        assert not r.passed
        assert r.level_pass and not r.prestigious_pass

    def test_level_fail_prestigious_pass(self):
        r = check_education_threshold(
            "大专", ["985院校"], _f("本科", ["985"], True)
        )
        assert not r.passed
        assert not r.level_pass and r.prestigious_pass

    def test_reason_contains_diagnostic_info(self):
        r = check_education_threshold("大专", [], _f("本科"))
        assert "大专" in r.reason and "本科" in r.reason
```

- [ ] **Step 1.2: 运行测试确认失败**

Run: `pytest tests/modules/recruit_bot/test_education_check.py -v`
Expected: `ModuleNotFoundError: No module named 'app.modules.recruit_bot.education_check'`

- [ ] **Step 1.3: 写最小实现**

`app/modules/recruit_bot/education_check.py`:

```python
"""F3 学历门槛筛选 — 学历等级 + 名校标签的纯函数判定."""
import re
from typing import Literal
from pydantic import BaseModel, Field

_EDU_ORD: dict[str, int] = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}

PrestigiousTag = Literal["985", "211", "双一流", "QS_TOP_100"]

_TIER_RE: dict[str, re.Pattern[str]] = {
    "985": re.compile(r"985"),
    "211": re.compile(r"211"),
    "双一流": re.compile(r"双一流"),
    "QS_TOP_100": re.compile(
        r"QS[\s_]?(?:TOP[\s_]?)?100|世界排名前\s*100", re.IGNORECASE
    ),
}


class EducationFilter(BaseModel):
    """HR 在扩展面板配置的学历门槛."""
    min_level: Literal["大专", "本科", "硕士", "博士"]
    prestigious_tags: list[PrestigiousTag] = Field(default_factory=list)
    require_prestigious: bool = False


class EducationCheckResult(BaseModel):
    passed: bool
    level_pass: bool
    prestigious_pass: bool
    matched_tiers: list[str]
    reason: str


def check_education_threshold(
    candidate_education: str,
    school_tier_tags: list[str],
    filter_: EducationFilter,
) -> EducationCheckResult:
    """对一名候选人判定是否满足学历 + 名校门槛."""
    r = _EDU_ORD.get((candidate_education or "").strip(), 0)
    m = _EDU_ORD[filter_.min_level]
    level_pass = r >= m

    matched: list[str] = []
    for tag in filter_.prestigious_tags:
        pattern = _TIER_RE[tag]
        if any(pattern.search(t or "") for t in school_tier_tags):
            matched.append(tag)
    prestigious_pass = bool(matched) if filter_.require_prestigious else True

    passed = level_pass and prestigious_pass
    reason = _format_reason(
        candidate_education or "", filter_, level_pass, matched
    )
    return EducationCheckResult(
        passed=passed,
        level_pass=level_pass,
        prestigious_pass=prestigious_pass,
        matched_tiers=matched,
        reason=reason,
    )


def _format_reason(
    cand_edu: str, f: EducationFilter, level_pass: bool, matched: list[str]
) -> str:
    parts: list[str] = []
    parts.append(
        f"学历:{cand_edu or '空'}"
        + ("≥" if level_pass else "<")
        + f.min_level
    )
    if f.require_prestigious:
        if matched:
            parts.append(f"名校命中:{','.join(matched)}")
        else:
            parts.append(f"名校未命中(需{','.join(f.prestigious_tags) or '?'})")
    elif matched:
        parts.append(f"名校命中:{','.join(matched)}(参考)")
    return "; ".join(parts)
```

- [ ] **Step 1.4: 运行测试确认通过**

Run: `pytest tests/modules/recruit_bot/test_education_check.py -v`
Expected: 全部 PASS（约 16 个用例）

- [ ] **Step 1.5: Commit**

```bash
git add app/modules/recruit_bot/education_check.py tests/modules/recruit_bot/test_education_check.py
git commit -m "feat(recruit_bot): add education_check pure function for F3 filter

学历等级 + 名校标签的纯函数判定，含 require_prestigious 开关与 QS_TOP_100 多种写法识别。"
```

---

## Task 2: 更新 schemas — EducationFilter / Decision Literal / Request

**Files:**
- Modify: `app/modules/recruit_bot/schemas.py`
- Test: `tests/modules/recruit_bot/test_schemas.py`

- [ ] **Step 2.1: 写失败测试（新增）**

在 `tests/modules/recruit_bot/test_schemas.py` 末尾追加：

```python
class TestRecruitEvaluateRequestEducationFilter:
    def _candidate(self):
        from app.modules.recruit_bot.schemas import ScrapedCandidate
        return ScrapedCandidate(name="A", boss_id="b1")

    def test_education_filter_required(self):
        import pytest
        from pydantic import ValidationError
        from app.modules.recruit_bot.schemas import RecruitEvaluateRequest
        with pytest.raises(ValidationError):
            RecruitEvaluateRequest(job_id=1, candidate=self._candidate())

    def test_min_level_enum_constrained(self):
        import pytest
        from pydantic import ValidationError
        from app.modules.recruit_bot.schemas import RecruitEvaluateRequest
        with pytest.raises(ValidationError):
            RecruitEvaluateRequest(
                job_id=1, candidate=self._candidate(),
                education_filter={"min_level": "中专"},
            )

    def test_prestigious_tag_enum_constrained(self):
        import pytest
        from pydantic import ValidationError
        from app.modules.recruit_bot.schemas import RecruitEvaluateRequest
        with pytest.raises(ValidationError):
            RecruitEvaluateRequest(
                job_id=1, candidate=self._candidate(),
                education_filter={
                    "min_level": "本科",
                    "prestigious_tags": ["c9"],
                },
            )

    def test_require_prestigious_with_empty_tags_rejected(self):
        import pytest
        from pydantic import ValidationError
        from app.modules.recruit_bot.schemas import RecruitEvaluateRequest
        with pytest.raises(ValidationError):
            RecruitEvaluateRequest(
                job_id=1, candidate=self._candidate(),
                education_filter={
                    "min_level": "本科",
                    "prestigious_tags": [],
                    "require_prestigious": True,
                },
            )

    def test_valid_payload(self):
        from app.modules.recruit_bot.schemas import RecruitEvaluateRequest
        req = RecruitEvaluateRequest(
            job_id=1, candidate=self._candidate(),
            education_filter={
                "min_level": "本科",
                "prestigious_tags": ["985", "211"],
                "require_prestigious": False,
            },
        )
        assert req.education_filter.min_level == "本科"


class TestRecruitDecisionLiteral:
    def test_new_decisions_allowed(self):
        from app.modules.recruit_bot.schemas import RecruitDecision
        for d in ("should_greet", "skipped_already_greeted",
                  "rejected_low_education", "blocked_daily_cap"):
            RecruitDecision(decision=d)

    def test_old_decisions_rejected(self):
        import pytest
        from pydantic import ValidationError
        from app.modules.recruit_bot.schemas import RecruitDecision
        for d in ("rejected_low_score", "error_no_competency", "error_scoring"):
            with pytest.raises(ValidationError):
                RecruitDecision(decision=d)
```

- [ ] **Step 2.2: 运行测试确认失败**

Run: `pytest tests/modules/recruit_bot/test_schemas.py::TestRecruitEvaluateRequestEducationFilter -v`
Expected: ImportError 或 ValidationError 缺失

- [ ] **Step 2.3: 改写 schemas.py**

替换 `app/modules/recruit_bot/schemas.py` 中 `RecruitEvaluateRequest` 与 `RecruitDecision` 两个类：

```python
"""F3 recruit_bot 请求 / 响应 Pydantic schemas."""
from typing import Literal
from pydantic import BaseModel, Field, model_validator

from app.modules.recruit_bot.education_check import EducationFilter

DAILY_CAP_MAX = 10000


class ScrapedCandidate(BaseModel):
    # ...（保持原文，不变）...
    name: str = Field(..., min_length=1, max_length=100)
    boss_id: str = Field(..., min_length=1, max_length=100)
    age: int | None = None
    education: str = ""
    grad_year: int | None = None
    work_years: int = 0
    school: str = ""
    major: str = ""
    intended_job: str = ""
    skill_tags: list[str] = Field(default_factory=list)
    school_tier_tags: list[str] = Field(default_factory=list)
    ranking_tags: list[str] = Field(default_factory=list)
    expected_salary: str = ""
    active_status: str = ""
    recommendation_reason: str = ""
    latest_work_brief: str = ""
    raw_text: str = ""
    boss_current_job_title: str = ""


class RecruitEvaluateRequest(BaseModel):
    """F3 评估请求体。education_filter 必填；删旧 strategy 字段。"""
    job_id: int
    candidate: ScrapedCandidate
    education_filter: EducationFilter

    @model_validator(mode="after")
    def _require_tags_when_required(self) -> "RecruitEvaluateRequest":
        ef = self.education_filter
        if ef.require_prestigious and not ef.prestigious_tags:
            raise ValueError(
                "require_prestigious=True 时 prestigious_tags 不可为空"
            )
        return self


class RecruitDecision(BaseModel):
    decision: Literal[
        "should_greet",
        "skipped_already_greeted",
        "rejected_low_education",
        "blocked_daily_cap",
    ]
    resume_id: int | None = None
    score: int | None = None
    threshold: int | None = None
    reason: str = ""


class GreetRecordRequest(BaseModel):
    resume_id: int
    success: bool
    error_msg: str = ""


class UsageInfo(BaseModel):
    used: int
    cap: int
    remaining: int


class DailyCapUpdateRequest(BaseModel):
    cap: int = Field(..., ge=0, le=DAILY_CAP_MAX)
```

- [ ] **Step 2.4: 运行测试确认通过**

Run: `pytest tests/modules/recruit_bot/test_schemas.py -v`
Expected: 新测试 PASS；旧 `strategy` 相关测试若存在则会 FAIL — 留到 Task 4 一起处理

- [ ] **Step 2.5: Commit**

```bash
git add app/modules/recruit_bot/schemas.py tests/modules/recruit_bot/test_schemas.py
git commit -m "feat(recruit_bot): replace strategy field with EducationFilter

RecruitEvaluateRequest.strategy → education_filter (required);
RecruitDecision.decision 删 rejected_low_score/error_no_competency/error_scoring,
新增 rejected_low_education。"
```

---

## Task 3: 重写 service.evaluate_and_record + MatchingResult upsert

**Files:**
- Modify: `app/modules/recruit_bot/service.py:227-373`（替换 evaluate_and_record 函数体）
- Test: `tests/modules/recruit_bot/test_evaluate_and_record.py`（Task 4 处理）

- [ ] **Step 3.1: 写 MatchingResult upsert 辅助测试（先单测）**

在 `tests/modules/recruit_bot/test_evaluate_and_record.py` 顶部追加一个新测试组（旧测试不动；Task 4 集中替换）：

```python
class TestEducationOnlyMatchingResultWrite:
    def _filter(self, **kw):
        return {
            "min_level": kw.get("min_level", "本科"),
            "prestigious_tags": kw.get("tags", []),
            "require_prestigious": kw.get("require", False),
        }

    @pytest.mark.asyncio
    async def test_should_greet_writes_matching_result(self, db):
        import json
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.recruit_bot.education_check import EducationFilter
        from app.modules.matching.models import MatchingResult
        _mk_user(db)
        job = _mk_job(db, threshold=60, with_competency=False)
        c = _mk_candidate(education="硕士")
        ef = EducationFilter(**self._filter(min_level="本科"))
        dec = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c, education_filter=ef,
        )
        assert dec.decision == "should_greet"
        row = db.query(MatchingResult).filter_by(
            resume_id=dec.resume_id, job_id=job.id
        ).first()
        assert row is not None
        assert row.total_score == 100.0
        assert row.education_score == 100.0
        assert row.skill_score == 0.0
        assert row.experience_score == 0.0
        assert row.seniority_score == 0.0
        assert row.industry_score == 0.0
        assert row.hard_gate_passed == 1
        assert "education_only" in json.loads(row.tags)
        ev = json.loads(row.evidence)
        assert "education_only" in ev
        assert ev["education_only"]["candidate_level"] == "硕士"

    @pytest.mark.asyncio
    async def test_rejected_low_education_writes_zero_score(self, db):
        import json
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.recruit_bot.education_check import EducationFilter
        from app.modules.matching.models import MatchingResult
        from app.modules.resume.models import Resume
        _mk_user(db)
        job = _mk_job(db, with_competency=False)
        c = _mk_candidate(education="大专")
        ef = EducationFilter(min_level="本科")
        dec = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c, education_filter=ef,
        )
        assert dec.decision == "rejected_low_education"
        row = db.query(MatchingResult).filter_by(
            resume_id=dec.resume_id, job_id=job.id
        ).first()
        assert row.total_score == 0.0 and row.education_score == 0.0
        assert row.hard_gate_passed == 0
        r = db.query(Resume).filter_by(id=dec.resume_id).first()
        assert r.reject_reason.startswith("education_only:")

    @pytest.mark.asyncio
    async def test_no_competency_no_error(self, db):
        """没 competency_model 也能正常筛."""
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.recruit_bot.education_check import EducationFilter
        _mk_user(db)
        job = _mk_job(db, with_competency=False)
        c = _mk_candidate(education="硕士")
        ef = EducationFilter(min_level="本科")
        dec = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c, education_filter=ef,
        )
        assert dec.decision == "should_greet"

    @pytest.mark.asyncio
    async def test_matching_result_upsert_overwrites(self, db):
        from app.modules.recruit_bot.service import evaluate_and_record
        from app.modules.recruit_bot.education_check import EducationFilter
        from app.modules.matching.models import MatchingResult
        _mk_user(db)
        job = _mk_job(db, with_competency=False)
        c = _mk_candidate(education="本科")
        # 先评一次 → pass
        dec1 = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="本科"),
        )
        assert dec1.decision == "should_greet"
        before = db.query(MatchingResult).filter_by(
            resume_id=dec1.resume_id, job_id=job.id
        ).count()
        # 再评一次 (改门槛) → reject;  must reset greet_status so 第二次不被 skipped_already_greeted 短路
        from app.modules.resume.models import Resume
        r = db.query(Resume).filter_by(id=dec1.resume_id).first()
        r.greet_status = "none"
        db.commit()
        dec2 = await evaluate_and_record(
            db, user_id=1, job_id=job.id, candidate=c,
            education_filter=EducationFilter(min_level="硕士"),
        )
        assert dec2.decision == "rejected_low_education"
        after = db.query(MatchingResult).filter_by(
            resume_id=dec1.resume_id, job_id=job.id
        ).count()
        assert before == after == 1
        row = db.query(MatchingResult).filter_by(
            resume_id=dec1.resume_id, job_id=job.id
        ).first()
        assert row.total_score == 0.0
```

- [ ] **Step 3.2: 运行测试确认失败**

Run: `pytest tests/modules/recruit_bot/test_evaluate_and_record.py::TestEducationOnlyMatchingResultWrite -v`
Expected: 函数签名不匹配 / 决策值缺失

- [ ] **Step 3.3: 改写 `evaluate_and_record` 函数体**

替换 `app/modules/recruit_bot/service.py` 中 `evaluate_and_record` 完整函数（行 227-373）为：

```python
async def evaluate_and_record(
    db: Session, user_id: int, job_id: int,
    candidate: "ScrapedCandidate",
    education_filter: "EducationFilter",
) -> RecruitDecision:
    """F3 决策: daily_cap → upsert → 已 greeted skip → education_check → MatchingResult upsert."""
    import json
    from datetime import datetime, timezone
    from app.modules.matching.models import MatchingResult
    from app.modules.recruit_bot.education_check import check_education_threshold

    # 1. daily_cap
    usage = get_daily_usage(db, user_id)
    if usage.remaining <= 0:
        log_event(
            f_stage="F3_evaluate", action="blocked_daily_cap",
            entity_type="job", entity_id=job_id,
            input_payload={"boss_id": candidate.boss_id, "usage": usage.model_dump()},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="blocked_daily_cap",
            reason=f"今日已打 {usage.used}/{usage.cap}",
        )

    # 2. job 归属（不再要求 competency_model）
    job = (
        db.query(Job)
        .filter(Job.id == job_id, Job.user_id == user_id)
        .first()
    )
    if not job:
        raise ValueError(f"job {job_id} not found for user {user_id}")

    # 3. upsert resume
    resume = upsert_resume_by_boss_id(db, user_id=user_id, candidate=candidate)

    # 4. 已 greeted 跳过
    if resume.greet_status == "greeted":
        log_event(
            f_stage="F3_evaluate", action="skipped_already_greeted",
            entity_type="resume", entity_id=resume.id,
            input_payload={"boss_id": candidate.boss_id},
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="skipped_already_greeted",
            resume_id=resume.id,
            reason="历史已打过招呼",
        )

    # 5. 学历门槛判定
    check = check_education_threshold(
        candidate.education or "",
        candidate.school_tier_tags or [],
        education_filter,
    )

    # 6. MatchingResult upsert（无论 pass/fail 都写一行，复用既有 UI）
    now = datetime.now(timezone.utc)
    total = 100.0 if check.passed else 0.0
    edu_sc = 100.0 if check.level_pass else 0.0
    tags_list = ["education_only"]
    evidence_obj = {
        "education_only": {
            "candidate_level": candidate.education or "",
            "candidate_school": candidate.school or "",
            "school_tier_tags": list(candidate.school_tier_tags or []),
            "required_level": education_filter.min_level,
            "required_tags": list(education_filter.prestigious_tags),
            "require_prestigious": education_filter.require_prestigious,
            "level_pass": check.level_pass,
            "prestigious_pass": check.prestigious_pass,
            "matched_tiers": check.matched_tiers,
        }
    }
    existing_mr = db.query(MatchingResult).filter_by(
        resume_id=resume.id, job_id=job.id
    ).first()
    if existing_mr:
        existing_mr.total_score = total
        existing_mr.skill_score = 0.0
        existing_mr.experience_score = 0.0
        existing_mr.seniority_score = 0.0
        existing_mr.education_score = edu_sc
        existing_mr.industry_score = 0.0
        existing_mr.hard_gate_passed = 1 if check.passed else 0
        existing_mr.missing_must_haves = "[]"
        existing_mr.evidence = json.dumps(evidence_obj, ensure_ascii=False)
        existing_mr.tags = json.dumps(tags_list, ensure_ascii=False)
        existing_mr.competency_hash = "education_only"
        existing_mr.weights_hash = "education_only"
        existing_mr.scored_at = now
    else:
        db.add(MatchingResult(
            resume_id=resume.id, job_id=job.id,
            total_score=total,
            skill_score=0.0, experience_score=0.0,
            seniority_score=0.0, education_score=edu_sc, industry_score=0.0,
            hard_gate_passed=1 if check.passed else 0,
            missing_must_haves="[]",
            evidence=json.dumps(evidence_obj, ensure_ascii=False),
            tags=json.dumps(tags_list, ensure_ascii=False),
            competency_hash="education_only", weights_hash="education_only",
            scored_at=now,
        ))

    # 7. 阈值判定 + 更新 resume
    if check.passed:
        resume.status = "passed"
        resume.greet_status = "pending_greet"
        db.commit()
        log_event(
            f_stage="F3_evaluate", action="should_greet",
            entity_type="resume", entity_id=resume.id,
            input_payload={
                "boss_id": candidate.boss_id,
                "education_filter": education_filter.model_dump(),
                "candidate_education": candidate.education,
                "school_tier_tags": list(candidate.school_tier_tags or []),
                "matched_tiers": check.matched_tiers,
            },
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="should_greet",
            resume_id=resume.id,
            reason=check.reason,
        )
    else:
        resume.status = "rejected"
        resume.reject_reason = f"education_only: {check.reason}"
        db.commit()
        log_event(
            f_stage="F3_evaluate", action="rejected_low_education",
            entity_type="resume", entity_id=resume.id,
            input_payload={
                "boss_id": candidate.boss_id,
                "education_filter": education_filter.model_dump(),
                "candidate_education": candidate.education,
                "school_tier_tags": list(candidate.school_tier_tags or []),
                "level_pass": check.level_pass,
                "prestigious_pass": check.prestigious_pass,
            },
            reviewer_id=user_id,
        )
        return RecruitDecision(
            decision="rejected_low_education",
            resume_id=resume.id,
            reason=check.reason,
        )
```

同时在 `service.py` 顶部 import 块删除：
- `from app.modules.matching.service import MatchingService`（行 199）— 若 import 触发延迟加载副作用，保留也无伤；可移除以减少依赖。

并在文件末尾用 `TYPE_CHECKING` 加入：

```python
if TYPE_CHECKING:
    from app.modules.recruit_bot.education_check import EducationFilter  # noqa
```

> 注意：`service.py` 现有的 `from app.modules.recruit_bot.schemas import RecruitDecision, UsageInfo` 已存在；EducationFilter 类型 annotation 使用字符串 forward ref。

- [ ] **Step 3.4: 运行测试确认新测试通过**

Run: `pytest tests/modules/recruit_bot/test_evaluate_and_record.py::TestEducationOnlyMatchingResultWrite -v`
Expected: 4 个新测试 PASS

旧测试组 `test_evaluate_should_greet_high_score` 等会 FAIL — Task 4 修。

- [ ] **Step 3.5: Commit**

```bash
git add app/modules/recruit_bot/service.py tests/modules/recruit_bot/test_evaluate_and_record.py
git commit -m "feat(recruit_bot): F3 evaluate switches to education-only filter

evaluate_and_record 删 F2 score_pair 调用与 competency_model 前置；
按 EducationFilter 调 check_education_threshold；
写 MatchingResult 一行 (education_score=100/0, 其余维 0.0, tags=['education_only'])。"
```

---

## Task 4: 修旧测试（router / integration / 旧 evaluate 用例）

**Files:**
- Modify: `tests/modules/recruit_bot/test_evaluate_and_record.py`（替换旧测试组）
- Modify: `tests/modules/recruit_bot/test_router_evaluate.py`
- Modify: `tests/modules/recruit_bot/test_integration.py`

- [ ] **Step 4.1: 看旧测试现状**

Run: `pytest tests/modules/recruit_bot/ -v --no-header 2>&1 | head -80`
Expected: 列出哪些旧测试因签名/枚举变化而失败（预计 6-12 个）

- [ ] **Step 4.2: 改写 test_evaluate_and_record.py 旧用例**

打开 `tests/modules/recruit_bot/test_evaluate_and_record.py`：

**保留**：`_mk_job` / `_mk_candidate` / `_mk_user` 辅助 + `TestEducationOnlyMatchingResultWrite` 整组。

**改写** `test_evaluate_should_greet_high_score` → 用 `education_filter=EducationFilter(min_level="本科")`，删 `with_competency=True`，断言 `decision == "should_greet"`。

**改写** `test_evaluate_rejected_low_score` → 重命名 `test_evaluate_rejected_low_education`，候选人 `education="大专"`，filter `min_level="本科"`，断言 `decision == "rejected_low_education"`，`reject_reason.startswith("education_only:")`。

**改写** `test_evaluate_skipped_already_greeted` → 传 `education_filter` 参数，断言不变。

**改写** `test_evaluate_blocked_daily_cap` → 同上传 filter。

**删除**：任何依赖 `strategy='school_only'` 或 `error_no_competency` / `error_scoring` 的测试，逻辑已不存在。

示例（核心新签名调用）：

```python
@pytest.mark.asyncio
async def test_evaluate_should_greet_education_pass(db):
    from app.modules.recruit_bot.service import evaluate_and_record
    from app.modules.recruit_bot.education_check import EducationFilter
    _mk_user(db)
    job = _mk_job(db, with_competency=False)
    c = _mk_candidate(education="硕士")
    dec = await evaluate_and_record(
        db, user_id=1, job_id=job.id, candidate=c,
        education_filter=EducationFilter(min_level="本科"),
    )
    assert dec.decision == "should_greet"
    assert dec.resume_id is not None
```

- [ ] **Step 4.3: 改写 test_router_evaluate.py**

把所有 POST `/api/recruit/evaluate_and_record` 的 body 从 `{"job_id":..., "candidate":..., "strategy":"school_only"}` 改成包含 `education_filter`。

示例：

```python
def test_router_evaluate_education_pass(client, db):
    _mk_user(db); job = _mk_job(db, with_competency=False)
    payload = {
        "job_id": job.id,
        "candidate": {"name": "A", "boss_id": "b1", "education": "硕士"},
        "education_filter": {
            "min_level": "本科", "prestigious_tags": [], "require_prestigious": False
        },
    }
    r = client.post("/api/recruit/evaluate_and_record", json=payload)
    assert r.status_code == 200
    assert r.json()["decision"] == "should_greet"


def test_router_evaluate_missing_education_filter_422(client, db):
    _mk_user(db); job = _mk_job(db, with_competency=False)
    r = client.post("/api/recruit/evaluate_and_record", json={
        "job_id": job.id,
        "candidate": {"name": "A", "boss_id": "b1"},
    })
    assert r.status_code == 422


def test_router_evaluate_require_prestigious_no_tags_422(client, db):
    _mk_user(db); job = _mk_job(db, with_competency=False)
    r = client.post("/api/recruit/evaluate_and_record", json={
        "job_id": job.id,
        "candidate": {"name": "A", "boss_id": "b1", "education": "本科"},
        "education_filter": {
            "min_level": "本科", "prestigious_tags": [], "require_prestigious": True,
        },
    })
    assert r.status_code == 422
```

- [ ] **Step 4.4: 改写 test_integration.py**

定位所有 `strategy=` / `rejected_low_score` / `error_no_competency` 引用：

Run: `grep -nE "strategy=|rejected_low_score|error_no_competency|error_scoring" tests/modules/recruit_bot/test_integration.py`

逐个改：
- `strategy=...` → 删，并加 `education_filter=EducationFilter(...)`。
- 决策枚举名替换。

- [ ] **Step 4.5: 运行全模块测试**

Run: `pytest tests/modules/recruit_bot/ -v`
Expected: 全部 PASS

- [ ] **Step 4.6: Commit**

```bash
git add tests/modules/recruit_bot/
git commit -m "test(recruit_bot): align tests with education-only F3 filter

旧 strategy='school_only' / rejected_low_score / error_no_competency 等用例改写为
education_filter + rejected_low_education。"
```

---

## Task 5: 扩展端 popup.html 学历门槛卡片

**Files:**
- Modify: `edge_extension/popup.html`

- [ ] **Step 5.1: 找好插入位置**

Run: `grep -n "<body" edge_extension/popup.html`
Run: `wc -l edge_extension/popup.html`

定位主内容区（topbar 之后、第一张业务卡片之前）。

- [ ] **Step 5.2: 在主内容区插入学历门槛卡片**

在 popup.html 主内容区开头插入：

```html
<section class="card" id="edu-filter-card">
  <h3 class="card-title">学历门槛 (F3 筛选)</h3>
  <div class="row">
    <label>最低学历</label>
    <select id="edu-min-level">
      <option value="大专">大专</option>
      <option value="本科" selected>本科</option>
      <option value="硕士">硕士</option>
      <option value="博士">博士</option>
    </select>
  </div>
  <div class="row">
    <label>名校标签</label>
    <div id="edu-tags">
      <label><input type="checkbox" value="985" /> 985</label>
      <label><input type="checkbox" value="211" /> 211</label>
      <label><input type="checkbox" value="双一流" /> 双一流</label>
      <label><input type="checkbox" value="QS_TOP_100" /> QS_TOP_100</label>
    </div>
  </div>
  <div class="row">
    <label><input type="checkbox" id="edu-require" /> 必须命中名校</label>
  </div>
  <div class="row">
    <button id="edu-save" class="btn-primary">保存门槛</button>
    <span id="edu-save-hint" class="hint"></span>
  </div>
</section>
```

复用现有 `.card / .row / .btn-primary / .hint` 样式类（已在 `<style>` 块定义）。若类不存在，复用最近邻的卡片样式即可，不引入新 CSS。

- [ ] **Step 5.3: Commit**

```bash
git add edge_extension/popup.html
git commit -m "feat(extension): add F3 education filter card to popup

最低学历下拉 + 名校标签 checkbox + 必须命中名校开关 + 保存按钮。"
```

---

## Task 6: popup.js — chrome.storage.local 读写与校验

**Files:**
- Modify: `edge_extension/popup.js`

- [ ] **Step 6.1: 末尾追加 IIFE**

在 `edge_extension/popup.js` 末尾追加：

```javascript
// === F3 学历门槛卡片 ===========================================
(function initEducationFilter() {
  const STORAGE_KEY = 'HR_EDUCATION_FILTER';
  const DEFAULT_FILTER = {
    min_level: '本科',
    prestigious_tags: [],
    require_prestigious: false,
  };
  const $level = document.getElementById('edu-min-level');
  const $tags = document.querySelectorAll('#edu-tags input[type=checkbox]');
  const $require = document.getElementById('edu-require');
  const $save = document.getElementById('edu-save');
  const $hint = document.getElementById('edu-save-hint');
  if (!$level || !$save) return;

  function applyToUI(f) {
    $level.value = f.min_level || '本科';
    $tags.forEach(cb => { cb.checked = (f.prestigious_tags || []).includes(cb.value); });
    $require.checked = !!f.require_prestigious;
  }

  function readFromUI() {
    return {
      min_level: $level.value,
      prestigious_tags: Array.from($tags).filter(c => c.checked).map(c => c.value),
      require_prestigious: !!$require.checked,
    };
  }

  chrome.storage.local.get([STORAGE_KEY], (res) => {
    applyToUI(res[STORAGE_KEY] || DEFAULT_FILTER);
  });

  $save.addEventListener('click', () => {
    const f = readFromUI();
    if (f.require_prestigious && f.prestigious_tags.length === 0) {
      $hint.textContent = '勾选了"必须名校"必须至少选 1 个名校标签';
      $hint.style.color = '#ff4d4f';
      return;
    }
    chrome.storage.local.set({ [STORAGE_KEY]: f }, () => {
      $hint.textContent = '已保存 ' + new Date().toLocaleTimeString();
      $hint.style.color = '#00b38a';
    });
  });
})();
```

- [ ] **Step 6.2: 手动验证（Chrome）**

Run（人工）：装载扩展 → 打开 popup → 改设置 → 点保存 → 重开 popup → 状态保持。
亦可在 popup 开发者工具控制台输入 `chrome.storage.local.get('HR_EDUCATION_FILTER', console.log)` 验证。

- [ ] **Step 6.3: Commit**

```bash
git add edge_extension/popup.js
git commit -m "feat(extension): persist education filter to chrome.storage.local

读取/保存 HR_EDUCATION_FILTER；阻止 require_prestigious=true 但 tags=[] 的非法保存。"
```

---

## Task 7: content.js — 读 filter, 改 POST body, 改决策处理

**Files:**
- Modify: `edge_extension/content.js`（约 1067-1117 区间）

- [ ] **Step 7.1: 在 `autoGreetRecommend` 函数顶（循环开始之前）读 filter**

定位（Run: `grep -n "autoGreetRecommend\|processedBossIds" edge_extension/content.js | head -10`）。在第一次进入循环前插入：

```javascript
// F3 学历门槛筛选: 从 popup 设置读取
const educationFilter = await new Promise((resolve) => {
  try {
    chrome.storage.local.get(['HR_EDUCATION_FILTER'], (res) => {
      resolve(res.HR_EDUCATION_FILTER || {
        min_level: '本科', prestigious_tags: [], require_prestigious: false,
      });
    });
  } catch (e) {
    resolve({ min_level: '本科', prestigious_tags: [], require_prestigious: false });
  }
});
if (educationFilter.require_prestigious && educationFilter.prestigious_tags.length === 0) {
  log('未配置名校标签但启用了"必须名校"，停止');
  _setRunning(false);
  return { success: false, message: '请在扩展弹窗里完善学历门槛设置', summary: stats, log: LOG };
}
log(`学历门槛: ${educationFilter.min_level}${educationFilter.require_prestigious ? ' + 必须名校' : ''}${educationFilter.prestigious_tags.length ? ' [' + educationFilter.prestigious_tags.join(',') + ']' : ''}`);
```

- [ ] **Step 7.2: 替换 POST body**

定位 `content.js:1075` 附近，把：

```javascript
body: JSON.stringify({ job_id: jobId, candidate: scraped, strategy: 'school_only' }),
```

替换为：

```javascript
body: JSON.stringify({ job_id: jobId, candidate: scraped, education_filter: educationFilter }),
```

- [ ] **Step 7.3: 替换决策枚举处理**

定位行 1100、1110、1114 附近的 decision 分支：

```javascript
if (decision.decision === 'error_no_competency') { ... }
} else if (decision.decision === 'rejected_low_score') { ... }
} else if (decision.decision === 'error_scoring') { ... }
```

把 `error_no_competency` 整块 if 删除（再不会出现）；把 `rejected_low_score` 改成 `rejected_low_education`，log 文案改成：

```javascript
} else if (decision.decision === 'rejected_low_education') {
  stats.rejected++;
  log(`学历未达标: ${decision.reason}, 跳过`);
  _setStats(stats);
}
```

`error_scoring` 整块 if 删除（同样不再出现）。

- [ ] **Step 7.4: 手动验证（Chrome）**

人工：登录扩展 → 设置 min_level=博士 → F3 推荐页跑一轮 → 大部分卡片应被记 `rejected`，日志显示「学历未达标」。
network tab 抓包 `evaluate_and_record` 请求体应含 `education_filter`。

- [ ] **Step 7.5: Commit**

```bash
git add edge_extension/content.js
git commit -m "feat(extension): content.js sends education_filter to F3 evaluate

启动循环前读 chrome.storage.local；POST body 用 education_filter 替换 strategy；
决策处理切到 rejected_low_education；删 error_no_competency / error_scoring 分支。"
```

---

## Task 8: 全量验证 + 项目检查

**Files:** 无（仅运行命令）

- [ ] **Step 8.1: 后端单元 + 集成测试**

Run: `pytest tests/modules/recruit_bot/ -v`
Expected: 全部 PASS（含 Task 1 / Task 3 / Task 4 改写后的全部用例）

- [ ] **Step 8.2: 全后端 quicktest**

Run: `pytest tests/ -x --ignore=tests/qa_full --ignore=tests/chaos -q`
Expected: 全部 PASS（关注 matching / screening / ai_screening 是否被波及；如有失败，多数是 import / signature 问题，按报错修）

- [ ] **Step 8.3: 前端 / 类型检查**

Run: `pnpm typecheck`
Expected: 0 errors（扩展端是纯 JS 不参与 ts，typecheck 主要覆盖 frontend/）

Run: `pnpm test`
Expected: 全部 PASS

- [ ] **Step 8.4: 工作目录干净度检查**

Run: `git status --short`
Expected: working tree clean（除可能的 `frontend/pnpm-lock.yaml` 等已 untracked 文件）

- [ ] **Step 8.5: Smoke：装扩展走一次真实流程**

人工：
1. 在 popup 设置门槛 `min_level=本科` + 勾 `985`，不勾「必须名校」。
2. 打开 Boss 推荐页，点开始。
3. 观察：N 个候选人评估后日志显示 should_greet / rejected_low_education；勾「必须名校」后再跑，rejected 比例应升高。
4. 后端 audit_logs 表里 `action='rejected_low_education'` 行有 evidence JSON 含 `education_filter` / `matched_tiers`。

- [ ] **Step 8.6: 总结提交（可选）**

无新文件改动则跳过；若 Task 8 期间额外修了边角，单独 commit。

---

## Self-Review

**Spec coverage 对照：**
- §2 架构 → Task 1-3 + 5-7 全部覆盖。
- §3.1 EducationFilter schema → Task 2。
- §3.2 判定算法 → Task 1。
- §3.3 决策枚举调整 → Task 2 + Task 7。
- §3.4 MatchingResult 写入 → Task 3。
- §4 扩展前端 → Task 5 + 6 + 7。
- §5 状态机变更 → Task 3（reject_reason 前缀）。
- §6 错误边界 → Task 2（schema 422）+ Task 3（competency 缺失不报错）+ Task 7（前端预校验）。
- §7 审计 → Task 3（log_event）。
- §8 测试策略 → Task 1（单元）+ Task 3（service 集成）+ Task 4（router/integration）+ Task 8（e2e smoke）。
- §9 迁移回滚 → 无 DB 迁移；Task 7 删旧分支符合"硬回退"。
- §11 文件清单与 plan File Structure 段一致。

**Placeholder scan：** 无 TBD/TODO/"add appropriate ...";所有代码块完整可执行。

**Type consistency：** `EducationFilter` 在 education_check.py 定义并由 schemas.py 重导出；`check_education_threshold(candidate_education, school_tier_tags, filter_)` 在 Task 1 / Task 3 调用签名一致；`RecruitDecision.decision` Literal 4 个值在 Task 2、Task 3、Task 7 始终一致。

OK，提交。
