# AI-Only 筛选通道实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 能力模型变更后自动全量重算 matching_results；AI 智能筛选 Tab 自检 stale 并自动刷新打分。

**Architecture:** 后端把 `approve_competency` 的弱 T2 触发替换成 `/api/matching/recompute` 的等效强重算（purge + 全量）；前端 AiScreeningPanel 进入时拉 `listByJob` 判 stale，stale>0 自动调用 `recomputeJob`，带可见进度。保留旧 T2 函数和"五维能力筛选" Tab 不动。

**Tech Stack:** FastAPI + SQLAlchemy（后端）、Vue 3 + Element Plus（前端）、pytest（测试）。

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `app/modules/screening/router.py` | 修改 `approve_competency` 后台调用；新增 `_recompute_with_purge_for_competency_change` 函数 | 能力模型通过 → 全量重算 |
| `frontend/src/components/AiScreeningPanel.vue` | 新增 stale 检测 + 自动 recompute + 手动刷新按钮 + 进度可见 | AI Tab 兜底刷新 |
| `tests/integration/test_competency_approve_full_recompute.py` | 新建 | 集成测试：approve 后全量打分 + purge 行为 |

---

## Task 1: 写后端集成测试（红）

**Files:**
- Create: `tests/integration/test_competency_approve_full_recompute.py`

- [ ] **Step 1: 写第一个失败测试** — 验证 approve_competency 后所有 matching_results 都用新 competency_hash

```python
# tests/integration/test_competency_approve_full_recompute.py
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

from app.modules.matching.hashing import compute_competency_hash
from app.modules.matching.models import MatchingResult
from app.modules.matching.triggers import on_competency_approved
from app.modules.resume.models import Resume
from app.modules.screening.models import Job
from app.modules.im_intake.candidate_model import IntakeCandidate
from app.modules.im_intake.models import IntakeSlot


def _make_complete_candidate(db, user_id, name, skills, education="本科"):
    """造一个四项齐全 + promoted 的 candidate-resume 对。"""
    r = Resume(
        name=name, phone="", skills=skills, work_years=3,
        education=education, ai_parsed="yes", source="manual",
        seniority="中级", user_id=user_id,
    )
    db.add(r); db.flush()
    c = IntakeCandidate(
        user_id=user_id, name=name, phone="", education=education,
        skills=skills, source="manual", pdf_path=f"/tmp/{name}.pdf",
        intake_status="complete", promoted_resume_id=r.id,
        status="passed",
    )
    db.add(c); db.flush()
    for k in ("arrival_date", "free_slots", "intern_duration"):
        db.add(IntakeSlot(candidate_id=c.id, slot_key=k, value="filled"))
    db.commit()
    return c, r


@pytest.mark.asyncio
async def test_full_recompute_refreshes_all_hashes(db_session, monkeypatch):
    """approve_competency 后所有 matching_results 都使用新 competency_hash."""
    from app.modules.screening import router as screening_router
    from app.modules.screening.competency_service import apply_competency_to_job

    uid = 999
    # 旧能力模型
    cm_old = {
        "hard_skills": [{"name": "Python", "weight": 9, "must_have": True, "canonical_id": 1}],
        "experience": {"years_min": 0},
        "education": {"min_level": "本科"},
        "job_level": "中级",
    }
    job = Job(
        title="后端", user_id=uid, is_active=True, required_skills="",
        competency_model=cm_old, competency_model_status="approved",
        education_min="本科",
    )
    db_session.add(job); db_session.commit()

    # 造 3 个 promoted candidate
    cands = []
    for i, sk in enumerate(["Python", "Java", "Go"]):
        c, r = _make_complete_candidate(db_session, uid, f"u{i}", sk)
        cands.append((c, r))

    # 先用旧模型打分（模拟历史 T2 已跑过）
    with patch("app.modules.matching.service.enhance_evidence_with_llm",
               new=AsyncMock(side_effect=lambda ev, *a, **kw: ev)):
        await on_competency_approved(db_session, job.id)

    rows_before = db_session.query(MatchingResult).filter_by(job_id=job.id).all()
    assert len(rows_before) == 3
    old_hash = compute_competency_hash(cm_old)
    assert all(r.competency_hash == old_hash for r in rows_before)

    # 改能力模型 — Python 不再 must_have
    cm_new = {
        "hard_skills": [{"name": "Python", "weight": 9, "must_have": False, "canonical_id": 1}],
        "experience": {"years_min": 0},
        "education": {"min_level": "本科"},
        "job_level": "中级",
    }
    apply_competency_to_job(job.id, cm_new)
    new_hash = compute_competency_hash(cm_new)

    # 跑新触发函数
    with patch("app.modules.matching.service.enhance_evidence_with_llm",
               new=AsyncMock(side_effect=lambda ev, *a, **kw: ev)):
        await screening_router._recompute_with_purge_for_competency_change(
            job.id, uid,
        )

    rows_after = db_session.query(MatchingResult).filter_by(job_id=job.id).all()
    assert len(rows_after) == 3
    assert all(r.competency_hash == new_hash for r in rows_after), \
        f"stale hash 未刷新: {[r.competency_hash for r in rows_after]}"
```

- [ ] **Step 2: 跑测试确认红**

Run: `pytest tests/integration/test_competency_approve_full_recompute.py::test_full_recompute_refreshes_all_hashes -v`
Expected: FAIL — `AttributeError: module 'app.modules.screening.router' has no attribute '_recompute_with_purge_for_competency_change'`

- [ ] **Step 3: 不要在此步实现，留到 Task 2**

继续 Task 2。

---

## Task 2: 实现后端 `_recompute_with_purge_for_competency_change`（绿）

**Files:**
- Modify: `app/modules/screening/router.py:510-520`（在 `_t2_trigger_with_fresh_session` 旁边新增；不修改原函数）
- Modify: `app/modules/screening/router.py:576`（替换 approve_competency 的 background_tasks.add_task 调用）

- [ ] **Step 1: 新增 `_recompute_with_purge_for_competency_change` 函数**

在 `app/modules/screening/router.py` 的 `_t2_trigger_with_fresh_session` 函数定义之后（约 521 行），新增：

```python
async def _recompute_with_purge_for_competency_change(
    job_id: int, user_id: int
) -> None:
    """能力模型变更后全量重算 matching_results.

    相比旧 T2 trigger (on_competency_approved), 本函数:
      1. 先 purge job 下不在硬筛通过集合内的旧行 (避免脏数据残留)
      2. 对硬筛通过的全量简历强制重算 (跑到一半被中断时, 下次进 AI Tab 可由 stale 检测拉起兜底)

    实现复用 /api/matching/recompute 的核心逻辑, 不引入新接口.
    """
    from app.database import SessionLocal
    from app.modules.matching.hard_filter import hard_filter_resume_ids
    from app.modules.matching.router import _purge_outside_hard_filter
    from app.modules.matching.service import (
        _new_task, recompute_job_with_fresh_session,
    )

    db = SessionLocal()
    try:
        allowed = hard_filter_resume_ids(db, user_id, job_id)
        _purge_outside_hard_filter(db, job_id, allowed)
        db.commit()
    finally:
        db.close()

    task_id = _new_task(len(allowed))
    await recompute_job_with_fresh_session(
        job_id, task_id, user_id, pre_filter_resume_ids=allowed,
    )
```

- [ ] **Step 2: 替换 approve_competency 的后台调用**

定位 `app/modules/screening/router.py:576`:

```python
    # F2 T2 trigger: score recent resumes against newly approved job
    background_tasks.add_task(_t2_trigger_with_fresh_session, job_id)
```

替换为:

```python
    # F2 强触发: 能力模型变更后全量重算 (清掉旧行避免 stale 残留).
    # 旧的 _t2_trigger_with_fresh_session 保留不删 (其他调用点和测试仍需要).
    background_tasks.add_task(
        _recompute_with_purge_for_competency_change, job_id, user_id,
    )
```

- [ ] **Step 3: 跑 Task 1 的测试确认绿**

Run: `pytest tests/integration/test_competency_approve_full_recompute.py::test_full_recompute_refreshes_all_hashes -v`
Expected: PASS

- [ ] **Step 4: 跑相关回归**

Run: `pytest tests/integration/test_f2_trigger_competency_approve.py tests/integration/test_f2_stale_detection.py tests/integration/test_f2_hard_gate_edges.py -v`
Expected: ALL PASS（旧 T2 测试仍存活因函数未删）

- [ ] **Step 5: 提交**

```bash
git add app/modules/screening/router.py tests/integration/test_competency_approve_full_recompute.py
git commit -m "feat(matching): approve_competency triggers full purge+recompute (replaces T2)"
```

---

## Task 3: 后端测试 — purge 行为

**Files:**
- Modify: `tests/integration/test_competency_approve_full_recompute.py`

- [ ] **Step 1: 添加 purge 验证测试**

在 Task 1 测试文件末尾追加：

```python
@pytest.mark.asyncio
async def test_full_recompute_purges_orphan_rows(db_session, monkeypatch):
    """matching_results 中不在硬筛通过集合内的行被 purge 删除."""
    from app.modules.screening import router as screening_router
    from app.modules.matching.hashing import compute_competency_hash

    uid = 998
    cm = {
        "hard_skills": [],
        "experience": {"years_min": 0},
        "education": {"min_level": "本科"},
        "job_level": "中级",
    }
    job = Job(
        title="后端", user_id=uid, is_active=True, required_skills="",
        competency_model=cm, competency_model_status="approved",
        education_min="本科",
    )
    db_session.add(job); db_session.commit()

    # 一个硬筛通过的
    c1, r1 = _make_complete_candidate(db_session, uid, "alive", "Python")
    # 一个"孤儿"行：直接造 matching_result, 但对应 candidate 已 abandoned
    c2, r2 = _make_complete_candidate(db_session, uid, "orphan", "Python")
    c2.intake_status = "abandoned"
    db_session.commit()

    # 给两个 resume 都打分 (模拟历史数据)
    db_session.add(MatchingResult(
        resume_id=r1.id, job_id=job.id, total_score=80.0,
        skill_score=100.0, experience_score=80.0, seniority_score=80.0,
        education_score=100.0, industry_score=80.0,
        hard_gate_passed=1, missing_must_haves="[]", evidence="{}",
        tags='["高匹配"]',
        competency_hash="OLD", weights_hash="OLD",
        scored_at=datetime.now(timezone.utc),
    ))
    db_session.add(MatchingResult(
        resume_id=r2.id, job_id=job.id, total_score=80.0,
        skill_score=100.0, experience_score=80.0, seniority_score=80.0,
        education_score=100.0, industry_score=80.0,
        hard_gate_passed=1, missing_must_haves="[]", evidence="{}",
        tags='["高匹配"]',
        competency_hash="OLD", weights_hash="OLD",
        scored_at=datetime.now(timezone.utc),
    ))
    db_session.commit()
    assert db_session.query(MatchingResult).filter_by(job_id=job.id).count() == 2

    # 跑新触发函数 — 应该 purge orphan
    with patch("app.modules.matching.service.enhance_evidence_with_llm",
               new=AsyncMock(side_effect=lambda ev, *a, **kw: ev)):
        await screening_router._recompute_with_purge_for_competency_change(
            job.id, uid,
        )

    rows = db_session.query(MatchingResult).filter_by(job_id=job.id).all()
    assert len(rows) == 1, f"orphan 未被 purge: rows={[(r.resume_id, r.competency_hash) for r in rows]}"
    assert rows[0].resume_id == r1.id
    new_hash = compute_competency_hash(cm)
    assert rows[0].competency_hash == new_hash
```

- [ ] **Step 2: 跑测试**

Run: `pytest tests/integration/test_competency_approve_full_recompute.py -v`
Expected: 两个测试都 PASS

- [ ] **Step 3: 提交**

```bash
git add tests/integration/test_competency_approve_full_recompute.py
git commit -m "test(matching): cover purge orphan rows in competency-approve recompute"
```

---

## Task 4: 前端 — stale 检测 + 自动重算（含进度可见）

**Files:**
- Modify: `frontend/src/components/AiScreeningPanel.vue`

- [ ] **Step 1: 在 `<script setup>` 顶部 import + state**

定位现有 import 行（约 line 126）：

```js
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { aiScreeningApi, decisionApi } from '../api'
import ItemsTable from './AiScreeningItemsTable.vue'
```

改为：

```js
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { aiScreeningApi, decisionApi, matchingApi } from '../api'
import ItemsTable from './AiScreeningItemsTable.vue'
```

在 `eligibleCount` 等 ref 定义之后（约 line 145），新增：

```js
// 重算状态 (能力模型变更后的兜底)
const recomputing = ref(false)
const autoRecomputeReason = ref('')
const recomputeProgress = ref({ total: 0, completed: 0, failed: 0 })
```

- [ ] **Step 2: 新增 `triggerRecompute` 函数**

在 `loadPreview` 函数之后（约 line 183）新增：

```js
async function triggerRecompute({ silent = false } = {}) {
  recomputing.value = true
  recomputeProgress.value = { total: 0, completed: 0, failed: 0 }
  try {
    const r = await matchingApi.recomputeJob(props.jobId)
    const task_id = r.task_id
    recomputeProgress.value.total = r.total || 0
    // poll 进度
    while (true) {
      await new Promise(res => setTimeout(res, 1500))
      try {
        const s = await matchingApi.recomputeStatus(task_id)
        recomputeProgress.value = {
          total: s.total,
          completed: s.completed,
          failed: s.failed,
        }
        if (!s.running) break
      } catch (e) {
        console.warn('recompute status poll failed', e)
        break
      }
    }
    if (!silent) {
      ElMessage.success(
        `刷新打分完成: ${recomputeProgress.value.completed} / ${recomputeProgress.value.total}` +
        (recomputeProgress.value.failed ? `, 失败 ${recomputeProgress.value.failed}` : '')
      )
    }
    await loadPreview()
  } catch (e) {
    if (!silent) ElMessage.error('刷新打分失败: ' + _friendlyErrorMsg(e, '刷新打分失败'))
    console.warn('triggerRecompute failed', e)
  } finally {
    recomputing.value = false
    autoRecomputeReason.value = ''
  }
}
```

- [ ] **Step 3: 新增 `checkAndAutoRecompute`**

在 `triggerRecompute` 之后新增：

```js
async function checkAndAutoRecompute() {
  if (status.value !== 'idle') return
  try {
    const r = await matchingApi.listByJob(props.jobId, { page: 1, page_size: 100 })
    const staleCount = (r.items || []).filter(it => it.stale).length
    if (staleCount > 0) {
      autoRecomputeReason.value = `检测到 ${staleCount} 份分数基于旧能力模型，正在自动刷新…`
      await triggerRecompute({ silent: true })
    }
  } catch (e) {
    console.warn('stale check failed', e)
  }
}
```

- [ ] **Step 4: 在 `onMounted` 里挂载 stale 自检**

定位现有 `onMounted`（约 line 366）：

```js
onMounted(() => {
  loadCurrent()
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }
})
```

改为：

```js
onMounted(async () => {
  await loadCurrent()
  await checkAndAutoRecompute()
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }
})
```

- [ ] **Step 5: 模板 — 顶部加 alert + 刷新按钮**

定位 idle 块（template，约 line 4-46）。在 idle 块最顶部、现有 alert 之前新增：

```html
    <!-- 重算状态：能力模型变更或 stale 检测后的可见反馈 -->
    <el-alert
      v-if="recomputing"
      type="warning" :closable="false" show-icon
      style="margin-bottom: 12px;"
    >
      <template #title>
        {{ autoRecomputeReason || '正在重新打分…' }}
        ({{ recomputeProgress.completed }} / {{ recomputeProgress.total }}{{ recomputeProgress.failed ? `，失败 ${recomputeProgress.failed}` : '' }})
      </template>
    </el-alert>
```

在 idle 块 form 之后（约 line 39 的 `</el-form>` 之后）新增手动刷新按钮：

```html
      <div style="margin-top: 12px; text-align: right;">
        <el-button
          type="default"
          size="small"
          @click="triggerRecompute({ silent: false })"
          :disabled="recomputing"
          :loading="recomputing"
        >
          🔄 刷新打分
        </el-button>
        <span style="margin-left: 8px; color: #909399; font-size: 12px;">
          能力模型改过或简历库变动后用
        </span>
      </div>
```

- [ ] **Step 6: 模板 — 重算时屏蔽"开始 AI 筛选"按钮**

定位（约 line 32）:

```html
          <el-button
            type="primary"
            :disabled="eligibleCount === 0"
            @click="onStart"
            :loading="starting"
          >
            开始 AI 筛选
          </el-button>
```

`:disabled` 加上 recomputing：

```html
          <el-button
            type="primary"
            :disabled="eligibleCount === 0 || recomputing"
            @click="onStart"
            :loading="starting"
          >
            开始 AI 筛选
          </el-button>
```

- [ ] **Step 7: 编译验证**

Run: `pnpm typecheck` (或 frontend 目录下 `npm run typecheck`)
Expected: 无新增 type 错误

- [ ] **Step 8: 起 dev 手动验证**

Run: 后端 `python -m app.main`、前端 `cd frontend && pnpm dev`

手动验证项：
1. 浏览器进账号 11 → 岗位 90002 → AI 智能筛选 Tab
2. 应自动出现 "检测到 N 份分数基于旧能力模型，正在自动刷新…" 黄色 alert + 进度
3. 跑完后 alert 消失，eligibleCount 变成 21+（取决于 must-have 状态）
4. 手动点 "🔄 刷新打分" → 进度可见 → 完成后 success 提示

- [ ] **Step 9: 提交**

```bash
git add frontend/src/components/AiScreeningPanel.vue
git commit -m "feat(ai-screening): auto-recompute stale matching_results + manual refresh button"
```

---

## Task 5: 完整验收 + pnpm test + typecheck

**Files:** 无（验证步骤）

- [ ] **Step 1: 跑全部受影响的后端测试**

Run:
```bash
pytest tests/integration/test_competency_approve_full_recompute.py \
       tests/integration/test_f2_trigger_competency_approve.py \
       tests/integration/test_f2_stale_detection.py \
       tests/integration/test_f2_hard_gate_edges.py \
       tests/integration/test_ai_screening.py \
       tests/modules/ai_screening/ \
       -v
```
Expected: ALL PASS

- [ ] **Step 2: 跑 typecheck**

Run: `pnpm typecheck`
Expected: 无错误

- [ ] **Step 3: 跑 pnpm test (前端单测)**

Run: `pnpm test`
Expected: 无回归

- [ ] **Step 4: 端到端手动验证**

按 spec "验收路径" 走一遍：
1. 改岗位 90002 的能力模型（比如把 weight 改一下）→ 点通过
2. 等 1-3 分钟（看后端日志确认 `recompute_job_done` audit event）
3. 进 AI 智能筛选 Tab → 直接看到正确 eligibleCount（不应触发自动 recompute，因为已是 fresh）
4. 反向测试：直接 SQL 把部分行 `competency_hash` 改成 'STALE' → 重进 AI Tab → 应触发自动 recompute → 跑完后 hash 全部回正

- [ ] **Step 5: 提交（如有遗漏修复）+ 收尾**

如果手动验证发现遗漏，修完单独提交。否则跳过。

---

## Self-Review

**Spec coverage:**
- ✅ 后端 approve 即全量重算 → Task 2
- ✅ purge 旧行避免 stale 残留 → Task 2 + Task 3 测试
- ✅ 保留 `_t2_trigger_with_fresh_session` / `on_competency_approved` / 五维 Tab → Task 2 Step 1 明确不删
- ✅ AI 面板 stale 自动检测 → Task 4 Step 3
- ✅ AI 面板手动刷新按钮 → Task 4 Step 5
- ✅ 进度可见 → Task 4 Step 5（alert + 计数）
- ✅ 错误处理 → Task 4 Step 2（try/catch + console.warn + ElMessage.error）

**Placeholder scan:** 无 TBD/TODO/"similar to"，全部代码块完整。

**Type consistency:**
- `_recompute_with_purge_for_competency_change(job_id, user_id)` — Task 2 Step 1 定义、Task 2 Step 2 调用、Task 1 测试调用 — 签名一致 ✓
- `triggerRecompute({silent})` — Task 4 Step 2 定义，Task 4 Step 3 和 Step 5 调用 — 一致 ✓
- `matchingApi.listByJob(jobId, {page, page_size})` 已在 `frontend/src/api/index.js:161` 存在 — 签名一致 ✓
