# AI 自动分类候选人到目标岗位

## 背景

接待管理(Intake) 页 "目标岗位" 列对账号 11 显示 124/124 全部"未匹配"，因为所有 IntakeCandidate.job_id 字段为 NULL。

数据真相：
- 100 个 plugin 来源候选人 `job_intention="全栈工程师"` 完全等于唯一岗位 `Job(id=90002).title="全栈工程师"`
- 8 个 manual_upload 候选人写了其他 job_intention（如 "AI Agent 开发"）
- 16 个 manual_upload 候选人 job_intention 空
- 系统现有 `job_matcher.match_job_title` 仅做字符串相似度，且没接入任何流程

## 目标

让候选人自动分到正确的岗位上 — 优先精确字符串匹配（零成本），仅边界情况用 LLM 判断。

非目标：
- 不引入两段式 HR 审核（直接写 `candidate.job_id`，错了手动改）
- 不重写已有的 `match_job_title` 函数（先用，必要时再改）

## 方案

### 二段分类逻辑

`app/modules/im_intake/job_classifier.py`（新模块）：

```python
def classify_candidate_to_job(
    db: Session, candidate: IntakeCandidate, *, user_id: int
) -> tuple[int | None, str]:
    """返 (job_id, reason). 优先 exact match, 兜底 LLM."""
    jobs = active_approved_jobs_for_user(db, user_id)
    if not jobs:
        return None, "no_active_jobs"

    # 1. exact title match — 0 token, 瞬间
    intent = (candidate.job_intention or "").strip()
    if intent:
        for j in jobs:
            if (j.title or "").strip() == intent:
                return j.id, "exact_match"

    # 2. LLM 兜底
    return await _llm_classify(candidate, jobs)
```

LLM prompt 输入：
- 候选人：`name + job_intention + skills + work_experience[:200] + education`
- 候选 job 列表：`[{id, title, jd_summary}, ...]`（jd_summary 取 competency_model.hard_skills 名字拼接）

LLM 输出 JSON：`{"job_id": <int|null>, "confidence": "high"|"medium"|"low", "reason": "..."}`

LLM 调用：复用 `AIProvider(model=settings.ai_model_intake or settings.ai_model)` 走 OpenAI 兼容接口 + httpx。失败/超时返 `(None, "llm_error")`，不阻塞。

写入策略：直接 `candidate.job_id = result` + commit。无 ai_predicted_job_id 字段。

### 触发点

1. **自动**：`app/modules/resume/_ai_parse_core.py` 的 AI 解析完成后追加调用
2. **手动 backfill**：新增 endpoint `POST /api/im-intake/candidates/batch-classify`
   - 扫 `candidate.user_id == user_id AND candidate.job_id IS NULL` 全部候选人
   - 串行跑 classifier
   - 返回 `{task_id, total}`，前端用现有 `_RECOMPUTE_TASKS` 风格的进度查询接口轮询（或简化：单接口直接同步跑完）

### 前端

`frontend/src/views/Intake.vue`：

1. 顶部 toolbar 加按钮：
   ```html
   <el-button @click="onAiClassify" :loading="aiClassifying">
     🤖 AI 分类目标岗位（{{ unmatchedCount }} 个未分配）
   </el-button>
   ```
2. `unmatchedCount` = `items.filter(it => !it.job_id).length`
3. 点击调 `POST /im-intake/candidates/batch-classify` → 显示进度 → 完成后 `await loadList()` 刷新表格
4. "目标岗位"列保持原模板不变（job_title 由后端关联返）

## 数据流

```
候选人 PDF 上传/解析完成 (auto):
  _ai_parse_core.ai_parse_target() 跑完
  → job_classifier.classify_candidate_to_job(db, candidate, user_id)
    → 试 exact match → 命中即写 job_id
    → 否则调 LLM → 写 job_id 或保 None
  → db.commit()

HR 点 "AI 分类目标岗位" (manual backfill):
  POST /im-intake/candidates/batch-classify
  → 取 candidate.user_id == uid AND candidate.job_id IS NULL 全部
  → 串行 classify_candidate_to_job
  → 返 {total, exact_matched, llm_matched, no_match}
  → 前端刷新表格
```

## 错误处理

| 情况 | 处理 |
|---|---|
| `AIProvider.is_configured() == False` | exact match 仍跑；LLM 跳过，返 `(None, "llm_not_configured")` |
| LLM 调用超时或异常 | 单条返 `(None, "llm_error")`；批量继续 |
| LLM 返非法 JSON | 返 `(None, "llm_invalid_json")`，日志 warn |
| LLM 返 job_id 不属于 user | 视为 None，拒绝跨用户写入 |
| 多个 active job 都 exact match | 按 job.id ASC 取第一个，日志 warn |
| 用户没有任何 active+approved job | 返 None，前端按钮提示"请先发布岗位" |

## 不做的事

- **不加 ai_predicted_job_id 字段**：用户明确要求直接写 candidate.job_id
- **不引入审核工作流**：错了 HR 手动改 candidate.job_id
- **不向量分类**：用户选 LLM，向量留作未来优化
- **不改 plugin 路径**：plugin 候选人将走 exact match (job_intention='全栈工程师' === job.title) 直接命中，无需额外改造

## 测试计划

### 单元/集成测试

`tests/integration/test_job_classifier.py` (新建):

1. `test_exact_match_writes_job_id` — 候选人 job_intention == job.title → exact match → 写 job_id，零 LLM 调用
2. `test_exact_match_skipped_when_intent_empty` — job_intention 空 → 不走 exact，进 LLM
3. `test_llm_path_picks_best_job` — mock LLM 返 `{job_id: 90002, ...}` → 写 job_id=90002
4. `test_llm_rejects_cross_user_job` — mock LLM 返 user 没拥有的 job_id → 写 None
5. `test_batch_classify_endpoint` — 造 5 个 None 候选人，调 endpoint → 验证返计数 + 数据库 job_id 字段
6. `test_classify_no_active_jobs_returns_none` — user 没岗位 → 返 `(None, "no_active_jobs")`

### 手动验证

1. 起 dev → 进账号 11 → 接待管理 → 看到 "🤖 AI 分类目标岗位（124 个未分配）"
2. 点击 → 进度条跑完
3. 表格刷新 → "目标岗位"列至少 100+ 显示 "全栈工程师"
4. 切到 Jobs 详情 → 90002 → 匹配候选人 Tab → 仍能看到 26 人（与 job_id 无关，因为该 Tab 用 list_matched_for_job 不读 candidate.job_id）

## 实现顺序（TDD）

1. Test 1-6 红
2. `job_classifier.py` 实现 exact + LLM
3. Test 1-4 绿
4. Endpoint 实现 + Test 5 绿
5. 接 `_ai_parse_core` 自动触发
6. Frontend Intake.vue 加按钮 + 进度
7. 手动验证 4 项全过
