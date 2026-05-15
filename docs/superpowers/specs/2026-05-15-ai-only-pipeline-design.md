# AI-Only 筛选通道：能力模型变更后自动全量重算 + AI 面板可见状态

## 背景

当前用户使用流程：

1. 岗位"能力模型" Tab 编辑 must-have 等参数 → 点"通过"
2. 后端 `approve_competency` (`app/modules/screening/router.py:523`) 走 `_t2_trigger_with_fresh_session` → `on_competency_approved` (`app/modules/matching/triggers.py:55`) 对硬筛通过简历**逐条 sequential** 打分
3. 用户切到"AI 智能筛选" Tab 看候选池

**实战故障**（账号 11，岗位 90002）：

- T2 后台跑到第 14 个就停了（成因可能是 LLM 调用挂、后台任务被中断、进程重启）
- 剩下 12 份的 `matching_results` 仍使用旧 `competency_hash`、旧 `missing_must_haves=["Python"]`
- AI 智能筛选的 `_eligible_candidate_query` 读 `hard_gate_passed=1` → 用旧数据卡掉 5 个本该通过的候选人
- 用户在 AI 智能筛选 Tab 看不到"再次分析"按钮（按钮藏在隔壁"五维能力筛选" Tab）→ 无法自救

**用户决策**："五维能力筛选基本不用了，只用 AI 分析"。

## 目标

将"能力模型变更 → AI 智能筛选" 这条主路径打通，让用户不需要再绕去五维 Tab 触发重算，并对中断情况有兜底。

非目标：删除五维 Tab、删除旧 T2 trigger 函数（避免破坏其他调用点和测试）。

## 方案概述

**两层防护：**

| 层 | 触发时机 | 行为 |
|---|---|---|
| 后端"通过即全量重算" | 用户点能力模型"通过"按钮 | 替换 T2 弱触发为 `/api/matching/recompute` 等效的强重算（purge + 全量） |
| 前端 AI 面板"可见的兜底" | 用户进入 AI 智能筛选 Tab | 自动 stale 检测 → stale > 0 时自动重算 + 可见状态显示；同时提供手动"刷新打分"按钮 |

## 详细设计

### 后端改动

**文件**：`app/modules/screening/router.py`

**改动 1**：`approve_competency` 调用点替换（router.py:576）

```python
# Before
background_tasks.add_task(_t2_trigger_with_fresh_session, job_id)

# After
background_tasks.add_task(_recompute_with_purge_for_competency_change, job_id, user_id)
```

**改动 2**：新增辅助函数 `_recompute_with_purge_for_competency_change(job_id, user_id)`

实现复用 `/api/matching/recompute` 接口的核心逻辑（`router.py:322-339`）：

```python
async def _recompute_with_purge_for_competency_change(job_id: int, user_id: int) -> None:
    """能力模型变更后全量重算：先 purge 旧行，再对硬筛通过的简历全量打分。

    替代旧 T2 trigger 的 sequential upsert，避免中断后留半成品 stale 数据。
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
    # task_id 仅供 progress 查询；当前 approve 流不暴露 task_id 给前端
    # （前端通过 stale 检测识别状态）
    task_id = _new_task(len(allowed))
    await recompute_job_with_fresh_session(
        job_id, task_id, user_id, pre_filter_resume_ids=allowed,
    )
```

**改动 3**：保留 `_t2_trigger_with_fresh_session` 和 `on_competency_approved` 不删（向后兼容，可能被测试或其他 trigger 引用）。

### 前端改动

**文件**：`frontend/src/components/AiScreeningPanel.vue`

**改动 1**：进入面板时检测 stale + 自动重算

`onMounted` → `loadCurrent` 之后新增 `checkAndAutoRecompute()`：

```js
async function checkAndAutoRecompute() {
  // 仅 idle 状态执行 (running/done 不打扰)
  if (status.value !== 'idle') return
  try {
    const r = await matchingApi.listResults(props.jobId)
    const staleCount = (r.items || []).filter(it => it.stale).length
    if (staleCount > 0) {
      autoRecomputeReason.value = `检测到 ${staleCount} 份分数基于旧能力模型，正在自动刷新…`
      await triggerRecompute({ silent: false })
    }
  } catch (e) {
    console.warn('stale check failed', e)
  }
}
```

**改动 2**：添加手动"刷新打分"按钮（idle 状态顶部）

```html
<el-button
  v-if="status === 'idle'"
  type="default"
  size="small"
  @click="triggerRecompute({ silent: false })"
  :loading="recomputing"
>
  🔄 刷新打分
</el-button>
```

**改动 3**：进度可见 — 复用现有 alert 区域显示重算状态

```html
<el-alert
  v-if="recomputing"
  type="warning" :closable="false" show-icon
>
  {{ autoRecomputeReason || '正在重新打分…' }} ({{ recomputeProgress.completed }}/{{ recomputeProgress.total }})
</el-alert>
```

**改动 4**：`triggerRecompute({ silent })` 函数实现

```js
async function triggerRecompute({ silent = false }) {
  recomputing.value = true
  try {
    const { task_id, total } = await matchingApi.recomputeJob(props.jobId)
    recomputeProgress.value = { task_id, total, completed: 0, failed: 0 }
    // poll 进度
    while (true) {
      await new Promise(r => setTimeout(r, 1500))
      const s = await matchingApi.recomputeStatus(task_id)
      recomputeProgress.value = {
        task_id, total: s.total,
        completed: s.completed, failed: s.failed,
      }
      if (!s.running) break
    }
    if (!silent) {
      ElMessage.success(`重新打分完成: ${recomputeProgress.value.completed} / ${recomputeProgress.value.total}`)
    }
    await loadPreview()  // 刷新 eligibleCount
  } catch (e) {
    if (!silent) ElMessage.error('刷新打分失败: ' + _friendlyErrorMsg(e))
  } finally {
    recomputing.value = false
    autoRecomputeReason.value = ''
  }
}
```

### 状态机

```
进入 AI 智能筛选 Tab
  ↓
loadCurrent() 拿当前 screening_job 状态
  ↓
status === 'idle' ?
  ↓ yes
listResults() 统计 staleCount
  ↓
staleCount > 0 ?
  ├─ yes → triggerRecompute({silent:false})
  │         显示 alert: "检测到 N 份分数基于旧能力模型，正在自动刷新…"
  │         poll 进度直到完成
  │         loadPreview() 刷新 eligibleCount
  │         alert 消失
  └─ no  → 正常显示 eligibleCount，等用户决策

用户主动点 "🔄 刷新打分" → triggerRecompute({silent:false}) 同上流程
```

## 数据流

```
能力模型 Tab "通过"
  → POST /api/jobs/{job_id}/competency/approve
  → background_tasks: _recompute_with_purge_for_competency_change
      1. hard_filter_resume_ids → 26 个 resume_id
      2. _purge_outside_hard_filter → 删 26 集合外的旧行
      3. recompute_job_with_fresh_session(pre_filter=26)
          - 对每个 resume × job pair: score_pair() → upsert
  → 跑完后 matching_results 所有行用新 competency_hash

用户切到 AI 智能筛选 Tab
  → loadCurrent() → status='idle'
  → checkAndAutoRecompute() → listResults() → staleCount=0
  → 正常显示 eligibleCount (e.g., 21 人)
  → 用户配置 mode/threshold → 开始 AI 筛选

意外路径：approve 流程被中断（进程重启等）
  → 部分行仍用老 hash → staleCount > 0
  → 用户进 AI Tab → 自动检测 → 自动 recompute → 刷新到正确状态
```

## 错误处理

| 情况 | 处理 |
|---|---|
| `_recompute_with_purge_for_competency_change` 跑到一半挂了 | matching_results 行可能部分新部分老 → 用户进 AI Tab 时 stale 检测会再次拉起重算 |
| stale check API 调用失败 | console.warn 不阻塞，用户仍可看到 eligibleCount（可能是 stale 值）；用户可手动点"刷新打分"兜底 |
| recompute 失败（如 LLM API 挂） | 进度面板显示 failed 计数；ElMessage.error 通知；用户可重试 |
| 用户在自动重算时关闭 Tab | 后台任务继续跑（fresh session），下次打开会显示已完成状态 |

## 不做的事

- **不删 "五维能力筛选" Tab**：用户说"基本不用"，不是"删除"。删 Tab 会牵动 Jobs.vue 大量 UI 代码且无法回退。
- **不删旧 `on_competency_approved` 和 `_t2_trigger_with_fresh_session`**：测试和其他可能调用点保留兼容。
- **不改 `/api/matching/recompute` 接口**：直接复用现有，行为已验证。
- **不引入新接口**：后端 0 个新 endpoint；前端走现有 `matchingApi.recomputeJob` / `recomputeStatus` / `listResults`。

## 测试计划

### 单元/集成测试（pytest）

**新增** `tests/integration/test_competency_approve_triggers_full_recompute.py`

- T1: 26 个 promoted 简历，用户改 must-have，点 approve → 验证 matching_results 所有行 `competency_hash` 都是新值
- T2: 模拟 approve 后跑到一半挂了（旧行有的有的没） → 验证 listResults 返回的 stale 计数 > 0
- T3: `_purge_outside_hard_filter` 不在硬筛集合内的旧行被删

### E2E（playwright）

- 起 dev → 登录账号 11 → 进岗位 90002 能力模型 → 修改 must-have → 点通过
- 等 5 秒（足够 background 跑完）
- 切到 AI 智能筛选 Tab → 验证 eligibleCount 是预期值（不是 stale 老值）
- 手动制造 stale（直接改 DB 把部分行的 competency_hash 改成 'OLD'）→ 重新进 Tab → 验证 alert "检测到 N 份分数基于旧能力模型，正在自动刷新…" 出现，跑完后消失

### 手动验证

打开账号 11 现有数据：12 份老 hash + 14 份新 hash 的现状下，重启 dev → 进 AI 智能筛选 → 看自动刷新动画 → 完成后 eligibleCount 应为 21+

## 实现顺序（TDD）

1. 写 T1/T3 集成测试 → 红
2. 实现 `_recompute_with_purge_for_competency_change` + 接 approve → T1/T3 绿
3. 写 T2 stale 检测测试 → 红
4. 实现前端 stale 自动检测和重算 → 全部绿
5. 跑 `pnpm test` + `pnpm typecheck`
6. 手动 E2E 验证

## 风险

| 风险 | 缓解 |
|---|---|
| 全量重算把 26 份简历的 LLM 评估再跑一遍，token 成本翻倍 | 用户改一次能力模型只触发一次；evidence LLM 调用可缓存在 `matching_results.evidence` 字段（已 upsert）|
| `_purge_outside_hard_filter` 误删现有正确数据 | 它只删 "不在硬筛通过集合内" 的行，硬筛集合本身不变前误删风险低；已有 `tests/integration/test_f2_e2e_smoke.py` 覆盖 |
| 前端 stale 自动重算 + 用户同时跑 AI 筛选 | `triggerRecompute` 在 `status === 'idle'` 才跑；status 转 'running' 后不再触发 |
