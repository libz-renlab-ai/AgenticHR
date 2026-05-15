# F3 学历门槛筛选改造设计文档

- 日期：2026-05-15
- 涉及模块：`app/modules/recruit_bot`、`edge_extension`、`app/modules/matching` 的 MatchingResult 写入路径
- 状态：Draft（用户已口头授权全自动推进）

## 1. 背景与目标

当前 F3「主动打招呼」决策走 `MatchingService.score_pair()` 五维加权打分（skill / experience / seniority / education / industry），阈值在 `Job.greet_threshold`。该流水线对每个候选人都要：

- 命中 `Job.competency_model` 必须 `status='approved'` 的前置；
- 调 LLM 做证据增强；
- 五维加权聚合，硬门槛缺失打折。

业务上 HR 希望简化为：**仅看学历是否达标**（学历等级 + 名校标签），其他维度全部不考虑。门槛由 HR 在浏览器扩展面板自行设置，无需后端配置岗位胜任力。

### 目标
1. F3 决策仅由「最低学历 + 名校标签集合」两条规则决定。
2. HR 在扩展面板配置门槛，存 `localStorage`，每次评估请求带在请求体。
3. 后端取消 `competency_model_status='approved'` 前置，保留 `daily_cap` 与历史去重。
4. 仍写 `MatchingResult` 一行，用于既有列表/审计 UI 复用；evidence 记录命中的学校与学历。
5. F2 `MatchingService.score_pair()` 代码不删（screening 模块仍调用），但 F3 路径不再调用它。

### 非目标
- 不改 F2 screening 列表的排序/筛选逻辑。
- 不引入用户级或岗位级学历偏好持久化（按当前需求只有扩展端 localStorage）。
- 不改抓取层（`ScrapedCandidate.school` / `school_tier_tags` / `education` 已是充分输入）。

## 2. 架构概览

```
┌──────────────────────── Edge Extension ────────────────────────┐
│ popup.html (设置面板)                                          │
│   ├─ 最低学历 select: [大专,本科,硕士,博士]                    │
│   └─ 名校标签 checkboxes: [985, 211, 双一流, QS_TOP_100]       │
│        ↓ 写 localStorage: HR_EDUCATION_FILTER                  │
│                                                                │
│ content.js · autoGreetRecommend()                              │
│   读 localStorage → 构造 education_filter →                    │
│   POST /api/recruit/evaluate_and_record                        │
│     body: {job_id, candidate, education_filter}                │
└──────────────────────────┬─────────────────────────────────────┘
                           ▼
┌────────────────────── Backend (FastAPI) ───────────────────────┐
│ recruit_bot/router.py                                          │
│   schema: RecruitEvaluateRequest 增加 education_filter 字段    │
│                                                                │
│ recruit_bot/service.py · evaluate_and_record()                 │
│   1. daily_cap 检查 (保留)                                     │
│   2. job 归属校验 (保留)                                       │
│   3. upsert resume / candidate (保留)                          │
│   4. 已 greeted 跳过 (保留)                                    │
│   5. ★ check_education_threshold(resume, education_filter) ★   │
│         ↓                                                      │
│   6. 阈值判定: pass → should_greet, fail → rejected_low_edu    │
│   7. 写 MatchingResult: education_score=100/0, 其余维 0.0      │
│   8. 审计日志 (保留)                                           │
│                                                                │
│ recruit_bot/education_check.py (新)                            │
│   纯函数 check_education_threshold(resume, filter) -> dict     │
│   返回 {passed, education_level_pass, prestigious_pass,        │
│         matched_tier, reason}                                  │
└────────────────────────────────────────────────────────────────┘
```

## 3. 数据流与接口契约

### 3.1 扩展 → 后端

```typescript
// edge_extension localStorage shape
HR_EDUCATION_FILTER = {
  min_level: "本科",                          // 大专 | 本科 | 硕士 | 博士
  prestigious_tags: ["985", "211", "双一流"], // 子集，可为 []
  require_prestigious: false                  // false: 只要学历达标; true: 学历达标 AND 命中任一名校
}
```

```python
# RecruitEvaluateRequest 修改
class EducationFilter(BaseModel):
    min_level: Literal["大专", "本科", "硕士", "博士"]
    prestigious_tags: list[Literal["985", "211", "双一流", "QS_TOP_100"]] = []
    require_prestigious: bool = False

class RecruitEvaluateRequest(BaseModel):
    job_id: int
    candidate: ScrapedCandidate
    education_filter: EducationFilter          # 必填，删旧 strategy 字段
```

### 3.2 学历门槛判定算法（`education_check.py`）

```python
_EDU_ORD = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}
_TIER_RE = {
    "985":   re.compile(r"985"),
    "211":   re.compile(r"211"),
    "双一流": re.compile(r"双一流"),
    "QS_TOP_100": re.compile(r"QS(?:[\s_]?TOP)?[\s_]?100|世界排名前100"),
}

def check_education_threshold(
    candidate_education: str,        # 例: "硕士"
    school_tier_tags: list[str],     # 例: ["211院校", "双一流"]
    filter_: EducationFilter,
) -> EducationCheckResult:
    r = _EDU_ORD.get(candidate_education.strip(), 0)
    m = _EDU_ORD[filter_.min_level]
    level_pass = r >= m

    matched_tiers = []
    if filter_.prestigious_tags:
        for tag in filter_.prestigious_tags:
            pattern = _TIER_RE[tag]
            if any(pattern.search(t) for t in school_tier_tags):
                matched_tiers.append(tag)
    prestigious_pass = bool(matched_tiers) if filter_.require_prestigious else True

    return EducationCheckResult(
        passed = level_pass and prestigious_pass,
        level_pass = level_pass,
        prestigious_pass = prestigious_pass,
        matched_tiers = matched_tiers,
        reason = _format_reason(candidate_education, filter_, level_pass, matched_tiers),
    )
```

**关键语义**：

| `require_prestigious` | `prestigious_tags` | 名校维度判定 |
|-----------------------|--------------------|--------------|
| False | 任意 | 视为 pass（仅记录命中标签作为 evidence） |
| True  | 空   | **请求层 422**，禁止"必须名校但没勾任何标签" |
| True  | 非空 | 必须命中至少一个 tag 才 pass（OR 语义） |

学历缺失（`candidate.education == ""` 且 `_EDU_ORD` 查表为 0）：视为 0 级，永远不达本科及以上门槛 → 走 `rejected_low_education`，不抛错。

### 3.3 决策枚举调整

```python
# 改造前后对照
RecruitDecision.decision: Literal[
    "should_greet",
    "skipped_already_greeted",
    "rejected_low_education",   # ← 新增（替代 rejected_low_score）
    "blocked_daily_cap",
    # 删除: rejected_low_score, error_no_competency, error_scoring
]
```

`rejected_low_education` 替代 `rejected_low_score`。审计日志 `action` 字段同步切换。**不保留旧值兜底**——按项目 schemas.Literal 单源原则。`reason` 字段携带可读说明，例如 `"学历:本科<硕士"` 或 `"学历:硕士✓; 名校未命中(需985/211)"`。

### 3.4 MatchingResult 写入

| 字段 | 取值 |
|------|------|
| `total_score` | 100.0 (passed) / 0.0 (failed) |
| `education_score` | 100.0 (level_pass) / 0.0 (failed) |
| `skill_score` / `experience_score` / `seniority_score` / `industry_score` | **0.0**（columns are NOT NULL，必须填默认值，不写 NULL） |
| `hard_gate_passed` | 1 (passed) / 0 (failed) |
| `missing_must_haves` | `"[]"` 固定 |
| `evidence` | `{"education_only": {"candidate_level": "硕士", "required_level": "本科", "level_pass": true, "matched_tiers": ["211","双一流"], "required_tags": ["985","211"], "school": "XXX大学"}}` |
| `tags` | `'["education_only"]'` |
| `competency_hash` / `weights_hash` | 固定常量 `"education_only"`（不依赖 competency_model，避免 NOT NULL 违反） |
| `scored_at` | now() |

如该 `(resume_id, job_id)` 已存在 MatchingResult（来自历史 F2 打分），按 `uq_mr_resume_job` 走 upsert 覆盖；保留 `job_action` 字段不动（HR 既往手动决策不抹除）。

## 4. 扩展前端

### 4.1 popup.html 设置卡片

在现有「招聘助手」面板加一张"筛选门槛"卡片：

```
┌────── 学历筛选门槛 ──────┐
│ 最低学历: [本科 ▼]      │
│ 名校标签: ☑985 ☑211     │
│           ☐双一流 ☐QS   │
│ ☐ 必须命中名校           │
│           [保存]         │
└──────────────────────────┘
```

写入 `chrome.storage.local`（`HR_EDUCATION_FILTER` key），跨 popup/content 共享。

### 4.2 content.js 改动

`autoGreetRecommend()` 启动时读：

```javascript
const filter = await chrome.storage.local.get('HR_EDUCATION_FILTER');
const educationFilter = filter.HR_EDUCATION_FILTER || {
  min_level: "本科", prestigious_tags: [], require_prestigious: false
};
```

POST body 替换 `strategy: 'school_only'` 为：

```javascript
body: JSON.stringify({
  job_id: jobId,
  candidate: scraped,
  education_filter: educationFilter,
}),
```

启动前若 `education_filter.require_prestigious && prestigious_tags.length === 0`，UI 抛红 toast 并阻止启动循环（避免每张卡片都 422）。

## 5. 状态机变更

`Resume` / `IntakeCandidate` 状态字段语义不变：

- pass → `resume.status='passed'`, `resume.greet_status='pending_greet'`
- fail → `resume.status='rejected'`, `resume.reject_reason='education_only: <reason>'`

新 reject_reason 形态以 `education_only:` 前缀开头便于审计区分历史 F2 拒因。

## 6. 错误与边界

| 场景 | 行为 |
|------|------|
| `education_filter` 缺失 | router 层 422 |
| `min_level` 非合法值 | Pydantic Literal 自动 422 |
| `require_prestigious=True` 且 `prestigious_tags=[]` | router 层 422 + 前端预校验 |
| 候选人 `education==""` | 视为 0 级，走 rejected_low_education |
| 候选人 `school_tier_tags==[]` 且 `require_prestigious=True` | rejected_low_education（matched_tiers=[]） |
| `competency_model` 不存在 | 不再报错，正常继续（不再消费 cm） |
| MatchingResult 已存在 | upsert，覆盖五维分与 evidence，不动 job_action |
| daily_cap 已用尽 | 一切之前先返 blocked_daily_cap（不变） |
| 已 greeted | skipped_already_greeted（不变） |

## 7. 审计事件

```python
log_event(
    f_stage="F3_evaluate",
    action="should_greet" | "rejected_low_education" | "blocked_daily_cap" | "skipped_already_greeted",
    entity_type="resume" | "job",
    entity_id=...,
    input_payload={
        "boss_id": ..., "education_filter": filter_.model_dump(),
        "candidate_education": ..., "school_tier_tags": ...,
        "matched_tiers": ..., "level_pass": ...
    },
    reviewer_id=user_id,
)
```

action 枚举与 `RecruitDecision.decision` 保持 1:1。

## 8. 测试策略（TDD）

按"先写测试再写实现"。三层金字塔：

### 8.1 单元测试 `tests/recruit_bot/test_education_check.py`
- 学历等级：`大专 vs 本科` / `硕士 vs 本科` / 空字符串 / 未识别值 / 大小写带空格
- 名校命中：`["211院校"]` 对 tags `["211"]` 命中 / 对 `["985"]` 不命中 / `["双一流"]` regex 匹配 / `QS_TOP_100` 多种写法
- 三组开关：`require_prestigious` True/False × tags []/非空 6 个组合
- reason 字符串包含输入诊断信息

### 8.2 service 集成 `tests/recruit_bot/test_evaluate_and_record_education_only.py`
- happy path：本科+211 候选人 + filter(本科, [], False) → should_greet，落 MatchingResult
- 学历不达标 → rejected_low_education + reject_reason 前缀 "education_only:"
- 必须名校但未命中 → rejected_low_education
- daily_cap 用尽优先返回 blocked_daily_cap，不进学历判定
- 已 greeted 跳过，不进学历判定，不重写 MatchingResult
- competency_model 缺失下仍能正常评估（曾经报 error_no_competency 不再出现）
- 重复评估 (resume_id, job_id) 同对 → MatchingResult upsert 覆盖
- 历史 F2 已有 MatchingResult + 已设 `job_action='passed'` → 覆盖五维但保留 job_action

### 8.3 扩展端 e2e（手动 + Playwright 抽样）
- popup 修改设置 → localStorage 持久化 → 重启扩展仍生效
- content.js 在评估请求里把 education_filter 真的带进去（network tab 抓包断言）
- `require_prestigious=True` 但 `tags=[]` → UI 阻断启动

### 8.4 并发与回归
- 双扩展同时跑 (N=2) 评估同一候选人不同岗位 → 各自 MatchingResult 独立无冲突
- 旧的 strategy='school_only' 请求 → router 应 422（字段已删），edge_extension 不再发送

## 9. 迁移与回滚

- 无数据库迁移（不改表结构）。
- 历史 MatchingResult 行不动，新评估按 upsert 语义覆盖；`tags` 列出现 `education_only` 即可判定为新路径产物。
- 回滚路径：恢复 `service.py` git 历史，扩展端把 body 切回 `strategy: 'school_only'`。
- 不保留旧 `strategy` 字段兼容层——按"硬回退而非打补丁"项目偏好。

## 10. 风险与待定

- **抓取学历字段精度**：现 `ScrapedCandidate.education` 是 normalize 后字符串，但 normEdu 仅识别少数常见词。海外硕博学位（MBA、PhD 缩写）可能漏；本设计先用 `_EDU_ORD` 表查不到即 0，**当前接受**该误差，后续若误拒率高再扩词表。
- **名校 tags 时效**：`school_tier_tags` 直接来自 Boss DOM，依赖平台维护；若 Boss 改版需独立排查抓取层，不在本设计范围。
- **QS_TOP_100 词表**：暂不接入实体学校白名单，仅按 tag 字面匹配；产品确认是否够用。
- **MatchingResult 列 NOT NULL**：未来若改为 NULL，可把"其余维 0.0"切回 NULL；本次不动 schema。

## 11. 涉及文件清单

新增：
- `app/modules/recruit_bot/education_check.py`
- `tests/recruit_bot/test_education_check.py`
- `tests/recruit_bot/test_evaluate_and_record_education_only.py`

修改：
- `app/modules/recruit_bot/schemas.py`（删 strategy、增 EducationFilter、改 RecruitDecision Literal）
- `app/modules/recruit_bot/service.py`（删 F2 调用 + competency 前置 + school_only 分支；接入 education_check）
- `app/modules/recruit_bot/router.py`（无逻辑改，跟随 schema）
- `edge_extension/popup.html`（增设置卡片）
- `edge_extension/popup.js`（chrome.storage.local 读写）
- `edge_extension/content.js`（autoGreetRecommend 改 body）

不动：
- `app/modules/matching/*`（F2 score_pair 保留，screening 仍用）
- `core/competency/*`
- `Resume` / `IntakeCandidate` / `Job` 模型
