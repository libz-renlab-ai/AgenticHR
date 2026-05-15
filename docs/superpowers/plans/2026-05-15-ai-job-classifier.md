# AI 候选人岗位自动分类实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 把候选人自动分到 `IntakeCandidate.job_id` —— exact match 优先，LLM 兜底；同时给前端"接待管理"加批量分类按钮修复存量 124 个未匹配。

**Architecture:** 新模块 `app/modules/im_intake/job_classifier.py` 提供 `classify_candidate_to_job` 函数；新 endpoint `POST /api/im-intake/candidates/batch-classify` 做存量；`_ai_parse_core` 解析完后追加一次自动分类；Intake.vue 加按钮 + 进度。

**Tech Stack:** FastAPI + SQLAlchemy + httpx (OpenAI 兼容 LLM) + Vue 3。

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `app/modules/im_intake/job_classifier.py` | 新建 | 二段分类核心（exact + LLM） |
| `app/modules/im_intake/router.py` | 修改 | 新增 `/candidates/batch-classify` endpoint |
| `app/modules/resume/_ai_parse_core.py` | 修改 | 解析完成后调 `classify_candidate_to_job` |
| `tests/integration/test_job_classifier.py` | 新建 | 集成测试 6 个 |
| `frontend/src/views/Intake.vue` | 修改 | 顶部按钮 + 进度 + 计数 |
| `frontend/src/api/index.js` | 修改 | 加 `imIntakeApi.batchClassify` |

---

## Task 1: 后端分类核心 + 测试 1-4（exact + LLM）

**Files:**
- Create: `app/modules/im_intake/job_classifier.py`
- Create: `tests/integration/test_job_classifier.py`

- [ ] **Step 1: 写 classifier 模块骨架**

```python
# app/modules/im_intake/job_classifier.py
"""候选人到岗位的二段分类: exact match 优先, LLM 兜底."""
from __future__ import annotations
import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.adapters.ai_provider import AIProvider
from app.config import settings
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.screening.models import Job

logger = logging.getLogger(__name__)


def _active_approved_jobs(db: Session, user_id: int) -> list[Job]:
    return (
        db.query(Job)
        .filter(
            Job.user_id == user_id,
            Job.is_active == True,
            Job.competency_model_status == "approved",
        )
        .order_by(Job.id.asc())
        .all()
    )


def _exact_match(intent: str, jobs: list[Job]) -> Optional[Job]:
    intent = (intent or "").strip()
    if not intent:
        return None
    for j in jobs:
        if (j.title or "").strip() == intent:
            return j
    return None


def _build_llm_prompt(c: IntakeCandidate, jobs: list[Job]) -> str:
    job_lines = []
    for j in jobs:
        cm = j.competency_model or {}
        hard = cm.get("hard_skills") or []
        skill_names = ", ".join(s.get("name", "") for s in hard[:10] if s.get("name"))
        job_lines.append(f"- id={j.id} | 标题={j.title} | 核心技能={skill_names or '未定义'}")
    jobs_block = "\n".join(job_lines)

    return f"""你是 HR 助手。请把以下候选人分类到最匹配的岗位 id, 或返 null 表示无明显匹配。

候选人:
- 姓名: {c.name or ''}
- 求职意向: {c.job_intention or ''}
- 技能: {(c.skills or '')[:300]}
- 工作经验摘要: {(c.work_experience or '')[:200]}
- 学历: {c.education or ''}

候选岗位:
{jobs_block}

只返 JSON, 不要任何额外说明:
{{"job_id": <候选岗位 id 之一, 或 null>, "confidence": "high"|"medium"|"low", "reason": "<1 句话>"}}"""


async def _llm_classify(c: IntakeCandidate, jobs: list[Job]) -> tuple[Optional[int], str]:
    import httpx
    model = settings.ai_model_intake or settings.ai_model
    provider = AIProvider(model=model)
    if not provider.is_configured():
        return None, "llm_not_configured"

    prompt = _build_llm_prompt(c, jobs)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": provider.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        logger.warning("classifier llm invalid json: %s", e)
        return None, "llm_invalid_json"
    except Exception as e:
        logger.warning("classifier llm error: %s", e)
        return None, "llm_error"

    raw_jid = data.get("job_id")
    if raw_jid is None:
        return None, f"llm_no_match: {data.get('reason', '')}"

    try:
        jid = int(raw_jid)
    except (TypeError, ValueError):
        return None, "llm_invalid_job_id"

    if jid not in {j.id for j in jobs}:
        logger.warning("classifier llm returned cross-user job_id=%s, rejecting", jid)
        return None, "llm_cross_user_rejected"

    return jid, f"llm_{data.get('confidence', 'unknown')}: {data.get('reason', '')}"


async def classify_candidate_to_job(
    db: Session, candidate: IntakeCandidate, *, user_id: int
) -> tuple[Optional[int], str]:
    """返 (job_id_or_None, reason_code).

    流程:
      1. 取 user 名下 active + approved 岗位
      2. exact title match — 命中直接写
      3. LLM 兜底 — 失败/异常不阻塞, 返 (None, error_code)

    注: 调用方负责 db.commit(). 本函数只 set candidate.job_id 不 commit.
    """
    jobs = _active_approved_jobs(db, user_id)
    if not jobs:
        return None, "no_active_jobs"

    exact = _exact_match(candidate.job_intention or "", jobs)
    if exact is not None:
        candidate.job_id = exact.id
        return exact.id, "exact_match"

    jid, reason = await _llm_classify(candidate, jobs)
    if jid is not None:
        candidate.job_id = jid
    return jid, reason
```

- [ ] **Step 2: 写测试 1-4**

```python
# tests/integration/test_job_classifier.py
import json
import pytest
from unittest.mock import patch, AsyncMock

from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.job_classifier import classify_candidate_to_job
from app.modules.screening.models import Job


def _make_user(db, uid):
    from sqlalchemy import text
    db.execute(text(
        "INSERT OR IGNORE INTO users (id, username, password_hash, created_at) "
        "VALUES (:id, :u, 'x', datetime('now'))"
    ), {"id": uid, "u": f"u{uid}"})
    db.commit()


def _make_job(db, uid, jid, title, cm=None):
    j = Job(
        id=jid, user_id=uid, title=title, is_active=True,
        required_skills="", competency_model=cm or {"hard_skills": [], "experience": {"years_min": 0}},
        competency_model_status="approved",
    )
    db.add(j); db.commit()
    return j


def _make_cand(db, uid, name, intention="", skills="", we=""):
    c = IntakeCandidate(
        user_id=uid, boss_id=f"b_{name}", name=name,
        job_intention=intention, skills=skills, work_experience=we,
        intake_status="complete", status="passed",
    )
    db.add(c); db.commit()
    return c


@pytest.mark.asyncio
async def test_exact_match_writes_job_id(db_session):
    _make_user(db_session, 11)
    _make_job(db_session, 11, 7001, "全栈工程师")
    c = _make_cand(db_session, 11, "alice", intention="全栈工程师")

    jid, reason = await classify_candidate_to_job(db_session, c, user_id=11)
    db_session.commit()

    assert jid == 7001
    assert reason == "exact_match"
    assert c.job_id == 7001


@pytest.mark.asyncio
async def test_exact_match_skipped_when_intent_empty(db_session):
    _make_user(db_session, 12)
    _make_job(db_session, 12, 7002, "全栈工程师")
    c = _make_cand(db_session, 12, "bob", intention="")

    mock_llm = AsyncMock(return_value=(7002, "llm_high: 技能匹配"))
    with patch("app.modules.im_intake.job_classifier._llm_classify", mock_llm):
        jid, reason = await classify_candidate_to_job(db_session, c, user_id=12)
    assert mock_llm.called, "intent 为空必须进 LLM"
    assert jid == 7002


@pytest.mark.asyncio
async def test_llm_path_picks_best_job(db_session):
    _make_user(db_session, 13)
    _make_job(db_session, 13, 7003, "AI 工程师")
    c = _make_cand(db_session, 13, "carol", intention="AI Agent 开发", skills="LangChain")

    mock_resp = {"choices": [{"message": {"content": '{"job_id": 7003, "confidence": "high", "reason": "AI 方向"}'}}]}
    async def _post(*a, **kw):
        class R:
            def raise_for_status(self): pass
            def json(self_inner): return mock_resp
        return R()
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = _post
        with patch("app.modules.im_intake.job_classifier.AIProvider") as mock_p:
            mock_p.return_value.is_configured.return_value = True
            mock_p.return_value.base_url = "http://x"
            mock_p.return_value.api_key = "x"
            mock_p.return_value.model = "glm-4-flash"
            jid, reason = await classify_candidate_to_job(db_session, c, user_id=13)
            db_session.commit()
    assert jid == 7003
    assert "llm_high" in reason
    assert c.job_id == 7003


@pytest.mark.asyncio
async def test_llm_rejects_cross_user_job(db_session):
    _make_user(db_session, 14)
    _make_user(db_session, 15)
    _make_job(db_session, 14, 7004, "前端")
    _make_job(db_session, 15, 7005, "后端")  # 不属于 user 14
    c = _make_cand(db_session, 14, "dave", intention="后端开发")

    mock_resp = {"choices": [{"message": {"content": '{"job_id": 7005, "confidence": "high", "reason": "x"}'}}]}
    async def _post(*a, **kw):
        class R:
            def raise_for_status(self): pass
            def json(self_inner): return mock_resp
        return R()
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = _post
        with patch("app.modules.im_intake.job_classifier.AIProvider") as mock_p:
            mock_p.return_value.is_configured.return_value = True
            mock_p.return_value.base_url = "http://x"
            mock_p.return_value.api_key = "x"
            mock_p.return_value.model = "glm-4-flash"
            jid, reason = await classify_candidate_to_job(db_session, c, user_id=14)

    assert jid is None
    assert "cross_user" in reason
    assert c.job_id is None


@pytest.mark.asyncio
async def test_classify_no_active_jobs_returns_none(db_session):
    _make_user(db_session, 16)
    # 无 job
    c = _make_cand(db_session, 16, "eve")

    jid, reason = await classify_candidate_to_job(db_session, c, user_id=16)
    assert jid is None
    assert reason == "no_active_jobs"
```

- [ ] **Step 3: 跑测试**
Run: `pytest tests/integration/test_job_classifier.py -v -k "exact_match_writes or exact_match_skipped or llm_path_picks or llm_rejects_cross or no_active"`
Expected: 5 PASS

- [ ] **Step 4: 提交**
```bash
git add app/modules/im_intake/job_classifier.py tests/integration/test_job_classifier.py
git commit -m "feat(intake): job_classifier with exact match + LLM fallback"
```

---

## Task 2: Batch classify endpoint + 测试 5

**Files:**
- Modify: `app/modules/im_intake/router.py` (新增 endpoint)
- Modify: `tests/integration/test_job_classifier.py` (新增 test 5)

- [ ] **Step 1: 在 `app/modules/im_intake/router.py` 末尾追加 endpoint**

```python
@router.post("/candidates/batch-classify")
async def batch_classify_candidates(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """对当前 user 名下所有 job_id IS NULL 的候选人跑分类.

    串行执行 (LLM 调用相对慢, 但 124 个候选人量级可控). 返计数明细供前端展示.
    """
    from app.modules.im_intake.job_classifier import classify_candidate_to_job

    pending = (
        db.query(IntakeCandidate)
        .filter(IntakeCandidate.user_id == user_id, IntakeCandidate.job_id.is_(None))
        .all()
    )
    total = len(pending)
    exact_matched = 0
    llm_matched = 0
    no_match = 0
    errors = 0

    for c in pending:
        try:
            jid, reason = await classify_candidate_to_job(db, c, user_id=user_id)
            if jid is not None:
                if reason == "exact_match":
                    exact_matched += 1
                else:
                    llm_matched += 1
            else:
                no_match += 1
        except Exception as e:
            logger.warning("classify failed cid=%s: %s", c.id, e)
            errors += 1

    db.commit()
    return {
        "total": total,
        "exact_matched": exact_matched,
        "llm_matched": llm_matched,
        "no_match": no_match,
        "errors": errors,
    }
```

注意：`logger` 已在文件顶部 import；如未 import 则加 `import logging; logger = logging.getLogger(__name__)`。

- [ ] **Step 2: 写 test 5**

在 `tests/integration/test_job_classifier.py` 末尾追加：

```python
def test_batch_classify_endpoint(client_with_auth):
    """造 5 个 job_id=None 候选, 调 endpoint, 验证返计数 + DB job_id."""
    client, db, uid = client_with_auth  # fixture: see conftest
    from app.modules.im_intake.candidate_model import IntakeCandidate
    from app.modules.screening.models import Job

    j = Job(
        user_id=uid, title="全栈工程师", is_active=True, required_skills="",
        competency_model={"hard_skills": [], "experience": {"years_min": 0}},
        competency_model_status="approved",
    )
    db.add(j); db.commit()

    # 3 个 exact match, 2 个走 LLM
    for i in range(3):
        db.add(IntakeCandidate(
            user_id=uid, boss_id=f"bx{i}", name=f"exact{i}",
            job_intention="全栈工程师", intake_status="complete", status="passed",
        ))
    for i in range(2):
        db.add(IntakeCandidate(
            user_id=uid, boss_id=f"bl{i}", name=f"llm{i}",
            job_intention="其他", intake_status="complete", status="passed",
        ))
    db.commit()

    from unittest.mock import patch, AsyncMock
    mock_llm = AsyncMock(return_value=(j.id, "llm_high: 兜底"))
    with patch("app.modules.im_intake.job_classifier._llm_classify", mock_llm):
        r = client.post("/api/im-intake/candidates/batch-classify")

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert body["exact_matched"] == 3
    assert body["llm_matched"] == 2
    assert body["no_match"] == 0
    # 验证 DB
    cnt_with_job = db.query(IntakeCandidate).filter(
        IntakeCandidate.user_id == uid, IntakeCandidate.job_id == j.id,
    ).count()
    assert cnt_with_job == 5
```

注意：`client_with_auth` fixture 可能需要查 `tests/conftest.py` 看现有 pattern；若不存在则用现有 `test_im_intake_*.py` 里的 auth 方式（cookie/header）。

- [ ] **Step 3: 跑测试**
Run: `pytest tests/integration/test_job_classifier.py -v`
Expected: 6 PASS

- [ ] **Step 4: 提交**
```bash
git add app/modules/im_intake/router.py tests/integration/test_job_classifier.py
git commit -m "feat(intake): batch-classify endpoint for backfilling job_id"
```

---

## Task 3: 自动触发 — 接入 _ai_parse_core

**Files:**
- Modify: `app/modules/resume/_ai_parse_core.py`

- [ ] **Step 1: 看现有 _ai_parse_core 找接入点**

```bash
grep -n "def ai_parse_target\|db.commit\|candidate.ai_parsed" app/modules/resume/_ai_parse_core.py
```

找到 `ai_parse_target` 函数最后 commit 之前/之后的位置, 把分类调用插进去.

- [ ] **Step 2: 在 ai_parse_target 内部, 字段已写完且 candidate 关联还在 session 时追加**

伪代码（具体位置依文件结构）：

```python
# 在原本的 db.commit() 之前 或 之后:
try:
    # 仅对 IntakeCandidate 流走分类 (Resume 直进流可能没 candidate 对象)
    from app.modules.im_intake.candidate_model import IntakeCandidate
    cand = db.query(IntakeCandidate).filter_by(
        promoted_resume_id=resume.id
    ).first() if resume else None
    if cand and not cand.job_id:
        from app.modules.im_intake.job_classifier import classify_candidate_to_job
        await classify_candidate_to_job(db, cand, user_id=cand.user_id)
        db.commit()
except Exception as e:
    logger.warning("auto job classify failed: %s", e)
    # 不阻塞主流程
```

（精确插入位置由实施者读文件决定）

- [ ] **Step 3: 跑相关回归**
Run: `pytest tests/integration/test_ai_screening.py tests/integration/test_f2_lifecycle.py tests/modules/ -v`
Expected: ALL PASS

- [ ] **Step 4: 提交**
```bash
git add app/modules/resume/_ai_parse_core.py
git commit -m "feat(intake): auto-classify candidate to job after AI parse"
```

---

## Task 4: 前端 — Intake.vue 加按钮 + 进度

**Files:**
- Modify: `frontend/src/api/index.js`
- Modify: `frontend/src/views/Intake.vue`

- [ ] **Step 1: api/index.js 加 imIntakeApi.batchClassify**

定位 `imIntakeApi` 或类似 export, 末尾加：

```js
// 接待管理 — AI 分类
export const intakeClassifyApi = {
  batchClassify: () => api.post('/im-intake/candidates/batch-classify'),
}
```

（如已有 `imIntakeApi` namespace 则加进去）

- [ ] **Step 2: Intake.vue 表格上方加按钮 + 状态**

定位 `<el-table` 之前的 toolbar 区域, 加：

```html
<el-button
  type="primary"
  size="small"
  @click="onAiClassify"
  :loading="aiClassifying"
  :disabled="unmatchedCount === 0"
>
  🤖 AI 分类目标岗位({{ unmatchedCount }} 个未分配)
</el-button>
<el-alert
  v-if="lastClassifyResult"
  type="success" :closable="false" show-icon
  style="margin-top: 8px;"
>
  分类完成: 共 {{ lastClassifyResult.total }} 人 →
  精确匹配 {{ lastClassifyResult.exact_matched }} 人,
  AI 判断 {{ lastClassifyResult.llm_matched }} 人,
  无匹配 {{ lastClassifyResult.no_match }} 人{{
    lastClassifyResult.errors ? `, 失败 ${lastClassifyResult.errors} 人` : ''
  }}
</el-alert>
```

在 `<script setup>` 中加 state + computed + handler:

```js
import { intakeClassifyApi } from '../api'

const aiClassifying = ref(false)
const lastClassifyResult = ref(null)
const unmatchedCount = computed(() =>
  (items.value || []).filter(it => !it.job_id).length
)

async function onAiClassify() {
  aiClassifying.value = true
  lastClassifyResult.value = null
  try {
    const r = await intakeClassifyApi.batchClassify()
    lastClassifyResult.value = r
    ElMessage.success(`已完成: ${r.exact_matched + r.llm_matched}/${r.total} 个候选人已分配岗位`)
    await loadList()
  } catch (e) {
    ElMessage.error('AI 分类失败: ' + (e.response?.data?.detail || e.message || '请重试'))
  } finally {
    aiClassifying.value = false
  }
}
```

注意：`items` / `loadList` / `ElMessage` 已在文件中存在，仅追加新 state 和函数。

- [ ] **Step 3: 编译验证**
Run: `cd frontend && pnpm build`
Expected: build 成功无报错

- [ ] **Step 4: 提交**
```bash
git add frontend/src/api/index.js frontend/src/views/Intake.vue
git commit -m "feat(intake): AI classify button + progress in Intake page"
```

---

## Task 5: End-to-end 验证

- [ ] **Step 1: 跑全后端测试**
```bash
pytest tests/integration/test_job_classifier.py tests/integration/test_ai_screening.py tests/integration/test_f2_lifecycle.py tests/integration/test_candidate_id_resume_routes.py -v
```
Expected: ALL PASS

- [ ] **Step 2: 真实数据库 dry-run**

```python
# 临时脚本: scripts/_dry_run_classify_user11.py (不 commit)
import asyncio
from app.database import SessionLocal
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.job_classifier import classify_candidate_to_job

async def main():
    db = SessionLocal()
    try:
        cands = db.query(IntakeCandidate).filter(
            IntakeCandidate.user_id == 2, IntakeCandidate.job_id.is_(None)
        ).all()
        print(f"待分类: {len(cands)}")
        for c in cands[:5]:  # 先 5 个 dry run
            jid, reason = await classify_candidate_to_job(db, c, user_id=2)
            print(f"  cid={c.id} {c.name!r} → job={jid} reason={reason}")
        db.rollback()  # dry run, 不真写
    finally:
        db.close()

asyncio.run(main())
```

跑一遍看是否前 5 个全部 exact_match 命中 job 90002。

- [ ] **Step 3: 跑前端 build**
```bash
cd frontend && pnpm build
```
Expected: 成功

---

## Self-Review

**Spec coverage:**
- ✅ exact match 优先 → Task 1 Step 1 `_exact_match`
- ✅ LLM 兜底 → Task 1 Step 1 `_llm_classify`
- ✅ 直接写 candidate.job_id → Task 1 Step 1 `classify_candidate_to_job` 内部 set
- ✅ batch backfill endpoint → Task 2
- ✅ _ai_parse_core 自动触发 → Task 3
- ✅ 前端按钮 + 进度 → Task 4
- ✅ 跨用户拒绝写入 → Task 1 Step 1 `_llm_classify` 末尾校验 + Test 4
- ✅ 无 active job 返 None → Test 5

**Placeholder scan:** Task 3 Step 2 是伪代码（"精确插入位置由实施者读文件决定"）— 这是给 subagent 的合理 leeway，因为 _ai_parse_core 文件位置依实际函数结构。其他 Step 都有完整代码。

**Type consistency:**
- `classify_candidate_to_job` 签名一致 (Session, IntakeCandidate, *, user_id) → tuple[int|None, str]
- `batch-classify` 接口路径 `/im-intake/candidates/batch-classify` 在 endpoint + 测试 + 前端 api 都一致
- `intakeClassifyApi.batchClassify()` 在 Task 4 Step 1 定义, Step 2 调用
