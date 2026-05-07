# AI 辅助面试评估（Interview Intelligence MVP） — 设计文档

**Status**: Draft
**Date**: 2026-05-07
**Phase**: F-interview-eval（M3 后续）
**Parent**: [docs/superpowers/specs/2026-04-17-m3-autonomous-recruitment-design.md](./2026-04-17-m3-autonomous-recruitment-design.md)
**Depends on**: F1 能力模型（[2026-04-20-f1-competency-model-design.md](./2026-04-20-f1-competency-model-design.md)）已落地

---

## 1. 背景与范围

### 1.1 目标

把"HR 在腾讯会议上做完真人面试"这件事的产物（录像 mp4 + 转录稿）→ 经云 ASR + LLM → 输出**结构化 AI 面评**（scorecard + 录用建议 + 优势/风险/追问点 + 关键片段时间戳跳转），辅助 HR 决策。

形态选型基于 2026-05-07 市场调研（详见 commit message + 调研对话记录）：
- **国内 HR SaaS 走"数字人面试"路线**（北森/海纳/牛客/智联） → 不评估真人面试
- **国外 BrightHire/Metaview 走"interview intelligence"** → 真人面试录制+转录+rubric 打分；都不支持腾讯会议
- **腾讯会议生态留白** → AgenticHR 此 MVP 占位

### 1.2 明确不做（Non-goals）

| 项 | 归属 |
|---|---|
| 人类面试官面评表单 / 提交流程 | 独立 spec（README roadmap 待办项），与本 spec 解耦 |
| 实时面试官辅助提示（面试中提示追问点） | 未来探索（依赖实时音频流，腾会免费版拿不到） |
| 数字人异步 AI 面试官（候选人独立面试间） | 战略不做（市场已极拥挤） |
| 视频微表情 / 语音情绪打分 | **永不做**——EU AI Act 2025/2 已禁，HireVue 2021 已下线 |
| ATS/简历库回写录用决策 | M5+ 多租户化后再做 |
| AI 面评 HITL 强制审核 | MVP 不做，AI 评价直接展示 + 标注"AI 草稿，仅供参考" |

### 1.3 完工定义（Done 标准）

1. `app/modules/interview_eval/` 模块建立，单测覆盖率 **≥ 85%**
2. 新表 `interview_eval_jobs` / `interview_eval_scorecards` 存在；Alembic migration 可往返
3. 复用 `audit_events` (F1)：start / download_recording / asr_call / llm_call / publish / cancel / retention_purge 七类事件全留痕
4. 复用 `meeting/account_pool.py` 多账号 Playwright profile，能从腾讯会议**免费版** web 端下载 mp4
5. 腾讯云 ASR 录音文件识别接通，能把 mp4 → `[{start_ms, end_ms, speaker, text}]`
6. LLM 评分按 `jobs.competency_model.assessment_dimensions` 一对一打分，输出 schema 校验通过
7. 前端 `Interviews.vue` 新增"AI 面评"Tab：状态条 + 评分卡 + 证据跳转 + 录像播放器
8. 前端 `Resumes.vue` 候选人详情页聚合该候选多场面试 AI 评价
9. 飞书卡片推送给 HR + 面试官（沿用 `interviewers.feishu_user_id`）
10. `retention.py` cron 每日清理 180 天到期任务的 mp4 + 转录稿
11. E2E smoke 通过：建岗位 → competency_model approved → 安排面试 → mock 录像→ 点 [分析面试] → 看到 scorecard
12. F1 / F2 / F3 等既有功能**零回归**（pytest 全套、原有用例不挂）

### 1.4 前置依赖

- F1 已落地：`jobs.competency_model_status='approved'` 才能跑 AI 面评（无能力模型 → 没有 rubric → 拒绝创建任务）
- 腾讯会议账号至少有一个已在 `account_pool` 扫码登录、有 mp4 录像权限（免费版 1GB 配额内）
- `.env` 配置 `TENCENT_CLOUD_SECRET_ID` / `TENCENT_CLOUD_SECRET_KEY`（新增）

---

## 2. 决策摘要

全部在 2026-05-07 brainstorm 拍板（含两轮市场调研）：

| # | 决策项 | 选择 | 理由 |
|---|---|---|---|
| D1 | 形态 | **事后 Interview Intelligence**（BrightHire 路线） | 国内空白；国外验证；与 AgenticHR ATS 一体 |
| D2 | MVP 输出范围 | 转录稿+说话人 / Scorecard / 录用建议+优劣/追问 / 关键片段+时间戳 | 用户选全部 4 项 |
| D3 | mp4 数据接入 | 腾讯会议**免费版** + 复用现有 Playwright 多账号池 | 用户无企业版；现有基建可扩展 |
| D4 | 转录链路 | **腾讯云 ASR 录音文件识别**（1 元/小时，自带说话人分离） | 本地 FunASR 模型 2-5GB+30-60s 加载，部署门槛高；腾讯云 ASR 与腾会同生态、无新增数据出境 |
| D5 | Rubric 来源 | 复用 `jobs.competency_model.assessment_dimensions`（F1 已落地） | F1 spec §1.1 明文声明此字段是 F2-F8（含面试评估）唯一依据 |
| D6 | 触发时机 | **半自动**：HR 在面试详情页点 [分析面试] | 避免对所有面试无脑跑；HR 有判断权（闲聊/测试面试可跳） |
| D7 | 可见性 | 面试详情页 AI 面评 Tab + 候选人详情页聚合 + 飞书推送 HR/面试官 | 全部 4 项用户选 |
| D8 | MVP 范围裁剪 | **只做 AI 面评 Tab，人类面评表单顺延** | 当前 `interviews` 表只有 `notes` Text，人类面评未建；解耦做 |
| D9 | 数据保留 | mp4 + 转录稿都留 **180 天**，到期 cron 清；scorecard 永久 | 用户选 6 个月；PIPL 留存最小化 |
| D10 | 合规审计 | 复用 `audit_events` WORM 表（F1） | F1 已建好，PIPL §24 红线复用 |
| D11 | 模块归属 | **新建 `app/modules/interview_eval/`**（与 ai_screening 平级） | 业务边界清晰；不污染 scheduling/ai_evaluation |
| D12 | HITL | **不做 HITL**（AI 评价直接展示 + 标注"AI 草稿，请人工审阅") | MVP 范围；HITL 由独立"人类面评系统" spec 接管 |
| D13 | 异步执行模式 | 复用 `ai_screening/worker.py` 的 thread + cancel handle 模式 | 项目内一致；cancel 机制已成熟 |

---

## 3. 数据模型

### 3.1 `interview_eval_jobs` 表（新建）

```sql
CREATE TABLE interview_eval_jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  interview_id    INTEGER NOT NULL REFERENCES interviews(id),
  user_id         INTEGER NOT NULL,                         -- 多用户隔离
  status          TEXT NOT NULL DEFAULT 'pending',
  -- pending → downloading → transcribing → scoring → done | failed | cancelled
  recording_path  TEXT DEFAULT '',                          -- data/recordings/{job_id}.mp4
  recording_size  INTEGER DEFAULT 0,
  duration_sec    INTEGER DEFAULT 0,
  meeting_account TEXT DEFAULT '',                          -- 哪个腾会账号下到的
  asr_request_id  TEXT DEFAULT '',                          -- 腾讯云 ASR 任务 ID
  llm_model       TEXT DEFAULT '',
  prompt_version  TEXT DEFAULT '',
  error_msg       TEXT DEFAULT '',
  cancel_requested INTEGER DEFAULT 0,
  retention_until DATETIME,                                  -- created_at + 180 天
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ieval_jobs_interview ON interview_eval_jobs(interview_id);
CREATE INDEX idx_ieval_jobs_status    ON interview_eval_jobs(status);
CREATE INDEX idx_ieval_jobs_retention ON interview_eval_jobs(retention_until);
CREATE INDEX idx_ieval_jobs_user      ON interview_eval_jobs(user_id);
```

**状态机**：

```
   pending ──[worker pickup]──> downloading
                                      │
                              ┌───────┴───────┐
                              ▼               ▼
                        transcribing      failed (网络/鉴权)
                              │
                              ▼
                          scoring
                              │
                              ▼
                            done
                              │
                              ▼ [cancel_requested=1 任意阶段]
                          cancelled
```

### 3.2 `interview_eval_scorecards` 表（新建）

```sql
CREATE TABLE interview_eval_scorecards (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id              INTEGER NOT NULL REFERENCES interview_eval_jobs(id),
  interview_id        INTEGER NOT NULL,                      -- 冗余但加索引方便聚合
  transcript_path     TEXT NOT NULL,                          -- data/transcripts/{job_id}.json
  dimensions_json     JSON NOT NULL,
  -- 结构: [{name, score: 1-10, reasoning, evidence: [{start_ms, end_ms, speaker, text}]}]
  hire_recommendation TEXT NOT NULL,                          -- strong_hire | hire | hold | no_hire
  strengths           JSON NOT NULL,                           -- [str], ≤5
  risks               JSON NOT NULL,                           -- [str], ≤5
  followups           JSON NOT NULL,                           -- [str], ≤5
  llm_model           TEXT NOT NULL,
  prompt_version      TEXT NOT NULL,
  created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ieval_scorecards_job       ON interview_eval_scorecards(job_id);
CREATE INDEX idx_ieval_scorecards_interview ON interview_eval_scorecards(interview_id);
```

**保留策略**：scorecard 行**永久保留**（已无敏感原始信号，仅结构化结论 + 证据片段引用）；引用的 mp4/transcript 文件 180 天后被 retention cron 删，UI 上检测到引用文件丢失时显示"原始材料已清理"。

### 3.3 文件目录

```
data/
├── recordings/{job_id}.mp4            # 下载的会议录像（180 天清）
├── transcripts/{job_id}.json          # 结构化转录稿（180 天清）
└── audit/{event_id}.json              # 复用 F1 审计大 payload（保留 3 年）
```

### 3.4 Alembic Migration

`migrations/versions/{ts}_add_interview_eval.py`：
- `op.create_table("interview_eval_jobs", ...)`
- `op.create_table("interview_eval_scorecards", ...)`
- `op.create_index(...)` 全套
- `downgrade()` 干净 drop（不依赖任何外部数据）

---

## 4. 系统架构

### 4.1 模块布局

```
app/modules/interview_eval/
├── __init__.py
├── models.py                          # InterviewEvalJob / InterviewEvalScorecard
├── schemas.py                         # 请求/响应 Pydantic
├── router.py                          # FastAPI 路由
├── service.py                         # 任务编排
├── worker.py                          # 异步流水线 + cancel handle
├── prompts.py                         # LLM 评分 prompt 模板 + version
├── tencent_meeting_recording.py       # Playwright 下载 mp4（复用 account_pool）
├── tencent_asr.py                     # 腾讯云 ASR 客户端
└── retention.py                       # 180 天清理 cron
```

### 4.2 复用既有基建

| 复用 | 用途 |
|---|---|
| `app/modules/meeting/account_pool.py` + Playwright 持久化 profile | 下 mp4 不需要重新登录腾讯会议 |
| `jobs.competency_model.assessment_dimensions` (F1) | Scorecard 的 rubric 来源 |
| `app/modules/ai_screening/worker.py` 的 cancel handle 模式 | InterviewEval worker 取消机制照搬 |
| `app/adapters/ai_provider.py` (OpenAI 兼容) | LLM 评分调用 |
| `app/adapters/feishu.py` | 推送结果给 HR/面试官 |
| `audit_events` WORM 表 (F1) | 留痕：7 类事件全程审计 |
| `data/audit/` 大 payload 外置 | 转录稿/prompt/output 全文按需外置 |
| Alembic baseline | schema 变更走 Alembic |

### 4.3 新增外部依赖

- `tencentcloud-sdk-python` — 腾讯云 ASR SDK
- `.env` 新增：
  - `TENCENT_CLOUD_SECRET_ID`
  - `TENCENT_CLOUD_SECRET_KEY`
  - `INTERVIEW_EVAL_ENABLED=true|false`（顶级开关，默认 false，避免无凭证启动失败）

---

## 5. 工作流

### 5.1 触发

```
HR 在 Interviews.vue 面试详情页 [分析面试] 按钮
    ↓
POST /api/interview-eval/start
Body: {interview_id: int}
    ↓
service.create_job(interview_id, user_id):
  1. 校验 interview 存在 + user_id 匹配（多用户隔离）
  2. 校验 interview.job.competency_model_status == 'approved'（D5 前置）
     失败 → 400 "请先在 Jobs 页完成能力模型抽取"
  3. 校验 interview.meeting_id 非空 + interview.meeting_account 在 account_pool
     失败 → 400 "本次面试无腾讯会议记录或账号未登录"
  4. 校验同一 interview_id 没有进行中的 job（status in pending/downloading/transcribing/scoring）
     有 → 409 "已有进行中任务"
  5. INSERT interview_eval_jobs (status='pending', retention_until=now+180d)
  6. 后台 thread 跑 worker.run(job_id)
  7. audit_events 写一条 action='ieval_start'
  8. 返回 {job_id, status: 'pending'}
```

### 5.2 Worker 异步流水线

```python
# 伪码
def run(job_id):
    handle = register_handle(job_id)
    try:
        update_status(job_id, 'downloading')
        if check_cancel(job_id): return
        recording_path = tencent_meeting_recording.download(
            interview.meeting_id, interview.meeting_account, dest=f"data/recordings/{job_id}.mp4"
        )
        audit('download_recording', size=os.path.getsize(recording_path))

        update_status(job_id, 'transcribing')
        if check_cancel(job_id): return
        transcript = tencent_asr.transcribe(recording_path)
        # transcript: [{start_ms, end_ms, speaker_id, text}]
        speaker_map = identify_speakers(transcript, interview)
        # 启发式：第一个发言/发言占比少的 → interviewer；其余 → candidate
        # MVP 不强求 100% 准；UI 上可手动改 speaker
        for seg in transcript:
            seg['speaker'] = speaker_map[seg['speaker_id']]
        json.dump(transcript, open(f"data/transcripts/{job_id}.json", "w"), ensure_ascii=False)
        audit('asr_call', segments=len(transcript))

        update_status(job_id, 'scoring')
        if check_cancel(job_id): return
        prompt = prompts.build(interview, transcript)
        result = ai_provider.chat(prompt, expect_json=True)
        validate_schema(result)  # pydantic schema check
        INSERT interview_eval_scorecards(...)
        audit('llm_call', model=settings.ai_model, prompt_version=prompts.PROMPT_VERSION)

        update_status(job_id, 'done')
        feishu_push(interview, scorecard)
        audit('publish')
    except Exception as e:
        update_status(job_id, 'failed', error_msg=str(e))
        audit(f'failed_at_{current_step}', error=str(e))
    finally:
        unregister_handle(job_id)
```

### 5.3 取消 / 重跑

- **取消**：`POST /api/interview-eval/{job_id}/cancel` → `cancel_requested=1`；worker 在每步开头检查；正在跑的子操作（如 ASR 调用）尽量 graceful 退出，最差等当前步完成
- **重跑**：`POST /api/interview-eval/start` 带相同 `interview_id` → 创建**新 job**（旧 job 保留），新 job 复用 mp4 文件（如未被清）→ 跳过 download 直接进 transcribe（节省一次下载）

---

## 6. LLM Prompt 设计

### 6.1 Prompt 模板

```python
# prompts.py
PROMPT_VERSION = "interview_eval_v1"

SYSTEM = """你是一位资深招聘面试评估专家。基于面试转录稿，按给定的考察维度对候选人评分。

硬性要求：
1. 所有打分必须基于转录稿中的真实证据，禁止编造、禁止推测候选人未说过的内容
2. 输出严格符合 JSON Schema，禁止额外文字
3. 每个维度至少 1 个证据片段（含 start_ms/end_ms/speaker/text）
4. 转录稿可能有 ASR 误识别，遇到明显错字推断原意，但不改 speaker 归属
5. 禁止评估候选人的口音/语速/外貌/情绪——仅评估表达内容
"""

USER_TEMPLATE = """
# 候选人
姓名：{candidate_name}
学历：{candidate_education}
工作经验：{candidate_years} 年
当前技能：{candidate_skills}

# 岗位
职位：{job_title}
考察维度（请按这些维度逐一打分，与下方 dimensions 数组 1-1 对应）：
{assessment_dimensions_json}

# 面试转录稿（说话人 + 时间戳）
{transcript_text}

# 输出格式（严格 JSON）
{{
  "dimensions": [
    {{
      "name": "维度名称（必须与上方 assessment_dimensions[i].name 完全一致）",
      "score": 1-10 整数,
      "reasoning": "≤200 字打分理由",
      "evidence": [
        {{"start_ms": 整数, "end_ms": 整数, "speaker": "interviewer|candidate", "text": "原话"}}
      ]
    }}
  ],
  "hire_recommendation": "strong_hire|hire|hold|no_hire",
  "strengths": ["≤5 条核心优势"],
  "risks": ["≤5 条风险/疑虑"],
  "followups": ["≤5 条建议追问点"]
}}
"""
```

### 6.2 输出校验

`schemas.py` 定义 `ScorecardOutput` Pydantic 模型，worker 拿到 LLM 输出后：
1. `json.loads()` 失败 → 重试 3 次（指数退避）
2. Pydantic validate 失败 → 重试 3 次
3. `dimensions` 数量与 `assessment_dimensions` 不一致 → 重试 3 次
4. 全失败 → status=failed，error_msg 标注 "LLM 输出格式异常 N 次"

---

## 7. 前端 UI

### 7.1 面试详情页（`frontend/src/views/Interviews.vue`）新 Tab "AI 面评"

**Tab 头**：状态条
- pending：灰色"等待开始" + [取消]
- downloading：蓝色 + 进度提示"下载录像中..." + [取消]
- transcribing：蓝色"转录中..." + [取消]
- scoring：蓝色"AI 评分中..." + [取消]
- done：绿色"已完成"
- failed：红色 error_msg + [重跑]
- cancelled：灰色"已取消" + [重跑]
- 未触发：[分析面试] 按钮

**主体（done 时）**：
- **顶部录用建议徽章**（`<el-tag>`）：strong_hire 绿 / hire 蓝 / hold 黄 / no_hire 红 + 总分（维度分均值）
- **维度评分卡列表**：每个维度一卡
  - 卡头：维度名 + 分数（如 7.5/10，分数条可视化）
  - 卡体：reasoning 文字
  - 证据片段：1-3 个 chip，hover 显示 text，click 设置 `<video>.currentTime = start_ms/1000`
- **优势 / 风险 / 追问点**：三栏列表（`<el-tag>` 着色）
- **完整转录稿**：折叠面板，按说话人渲染气泡（interviewer 蓝 / candidate 绿），每段 click 跳录像
- **录像播放器**：右侧 `<video src="/api/interview-eval/{job_id}/recording" controls>`，文件 180 天后清理时显示"原始录像已清理"
- **底部备注**："此评价为 AI 草稿，仅供参考，最终决定权在 HR/面试官"

### 7.2 候选人详情页（`Resumes.vue`）聚合视图

候选人简历详情 tab 区新增 "面试 AI 评价"：
- 该候选所有有 scorecard 的 interview 列表
- 每行：日期 / 岗位 / 面试官 / 录用建议徽章 / 总分 / [查看详情]（跳到对应 Interview Tab）

### 7.3 API 路由

```
POST   /api/interview-eval/start                  # 启动任务
GET    /api/interview-eval/{job_id}                # 查任务状态
GET    /api/interview-eval/{job_id}/scorecard      # 取 scorecard JSON
GET    /api/interview-eval/{job_id}/transcript     # 取转录稿 JSON
GET    /api/interview-eval/{job_id}/recording      # 取 mp4 文件流
POST   /api/interview-eval/{job_id}/cancel         # 取消
GET    /api/interview-eval/by-interview/{iid}      # 取该 interview 最新 job
GET    /api/interview-eval/by-resume/{rid}         # 候选人聚合
```

---

## 8. 飞书推送

`audit_events.publish` 触发后，对该 interview 的 HR + interviewer（取 `interviewers.feishu_user_id`）各发一张飞书卡片：

```
┌────────────────────────────────────┐
│ 🤖 AI 面评已生成                    │
│                                    │
│ 候选人：张三                        │
│ 岗位：高级后端工程师                 │
│ 面试官：李四                         │
│                                    │
│ 录用建议：[hire]                    │
│ 总分：7.4 / 10                      │
│                                    │
│ [查看完整 AI 面评 →]                │
└────────────────────────────────────┘
```

按钮跳转到 `{AGENTICHR_URL}/interviews?id={interview_id}&tab=ai-eval`。复用 `feishu.send_card`，无新基建。

推送失败不影响任务 status——单独记 audit_events 一条 `feishu_push_failed`。

---

## 9. 错误处理 / 兜底

| 步骤 | 失败场景 | 处理 |
|---|---|---|
| `service.create_job` | competency_model 未 approved | 400 + UI 提示去 Jobs 页完成 |
| 同上 | meeting_id/meeting_account 缺失 | 400 + UI 提示无录像 |
| 同上 | 同 interview 已有进行中 job | 409 |
| `download_recording` | 录像未在腾会生成（会议刚结束） | failed + error_msg "录像可能尚未生成，请几分钟后重试" |
| 同上 | Playwright session 过期 | failed + error_msg "腾讯会议账号 {label} 需重新扫码登录" |
| 同上 | mp4 不存在（云录制配额已满） | failed + error_msg "云录制空间已满，请清理后重试" |
| `tencent_asr.transcribe` | 鉴权错 | failed + error_msg "腾讯云 ASR 凭证无效"（提示 .env 配置） |
| 同上 | 配额超限 | failed + error_msg "腾讯云 ASR 配额超限" |
| 同上 | 网络/超时 | 重试 3 次（指数退避 1s/3s/9s），全失败后 failed |
| `ai_provider.chat` | JSON 解析失败 / Schema 校验失败 | 重试 3 次后 failed |
| 同上 | LLM API 网络 | 同 ASR 重试策略 |
| `feishu_push` | 飞书 API 失败 | 不影响主任务 status；audit 留痕 + UI 上的小红点提示推送失败 |

---

## 10. 数据保留 / PIPL 合规

### 10.1 保留期

| 数据 | 保留期 | 清理方式 |
|---|---|---|
| `interview_eval_jobs` 行 | 与 mp4 同步 180 天后软删（设 `deleted_at`） | retention cron |
| `interview_eval_scorecards` 行 | 永久（已脱敏的结构化结论） | 不清 |
| `data/recordings/*.mp4` | 180 天 | retention cron 物理删 |
| `data/transcripts/*.json` | 180 天 | retention cron 物理删 |
| `audit_events` 行 | 3 年（F1 既定） | F1 既有 cron |
| `data/audit/*.json` | 3 年 | F1 既有 cron |

### 10.2 retention.py

```python
# 每日 03:00 跑（与现有 cron 调度集成）
def purge_expired():
    now = datetime.now(timezone.utc)
    expired = session.query(InterviewEvalJob).filter(
        InterviewEvalJob.retention_until < now
    ).all()
    for job in expired:
        if job.recording_path and os.path.exists(job.recording_path):
            os.remove(job.recording_path)
        transcript_path = f"data/transcripts/{job.id}.json"
        if os.path.exists(transcript_path):
            os.remove(transcript_path)
        audit('retention_purge', entity_id=job.id, files_removed=2)
        # 软删 job 行：清空 recording_path/transcript path 引用，UI 上 scorecard 仍可见但提示"原始材料已清理"
        job.recording_path = ''
        job.deleted_at = now
    session.commit()
```

### 10.3 PIPL 合规要点

- **告知与同意**：候选人面试同意书在 AgenticHR 当前流程外（HR 与候选人沟通时口头/书面）—— MVP 不强行加同意页，但在 README/帮助文档中提供告知模板
- **最小必要**：仅采集面试音频、不存视频画面以外的生物特征；不做情绪/微表情打分（D2 / D12 已锁）
- **数据出境**：腾讯云 ASR 在国内节点；不出境
- **审计可查**：`audit_events` 7 类事件全留痕，可向监管出具完整数据流
- **删除请求**：候选人请求删除其面试数据 → HR 在该候选详情页点 "彻底删除" → 删 mp4/transcript/scorecard，audit 留 `deletion_request` 记

---

## 11. 测试策略

### 11.1 单测

| 文件 | 覆盖 |
|---|---|
| `test_tencent_meeting_recording.py` | mock Playwright；登录态/未登录/mp4 不存在/网络错 |
| `test_tencent_asr.py` | mock SDK；正常返回/鉴权错/配额超限/超时；说话人映射启发式 |
| `test_worker.py` | 状态机各路径（pending→done / pending→failed at each step / cancelled at each step） |
| `test_service.py` | create_job 校验链（competency_model 未 approved / meeting 缺失 / 重复任务） |
| `test_prompts.py` | prompt 渲染、PROMPT_VERSION 不变性、token 估算 |
| `test_schemas.py` | ScorecardOutput Pydantic 校验；dimensions 数量校验 |
| `test_retention.py` | 到期清理、未到期不清理、文件不存在不报错 |

### 11.2 集成测

- `test_worker_integration.py` — mock 三个外部 IO（Playwright/ASR/LLM），跑完整 worker
- `test_router.py` — FastAPI TestClient，全 7 个端点 + 多用户隔离
- `test_alembic_roundtrip.py` — upgrade / downgrade 干净往返

### 11.3 E2E smoke

`tests/e2e/test_interview_eval_smoke.py`：
1. 注册用户 → 建岗位 → 粘 JD → competency_model 走 F1 流程到 approved
2. 创建 interviewer + 安排 interview（meeting_id/meeting_account 假数据 mock）
3. 在 fixtures 里准备一个 mp4 + transcript JSON（mock 文件），让 download/asr 直接读 fixture
4. POST /api/interview-eval/start
5. 轮询 status 到 done
6. 校验 scorecard 写入 + audit_events 7 类事件齐 + feishu_push mock 被调

### 11.4 覆盖率要求

- 单测覆盖率 **≥ 85%**（沿用 F1 标准）
- 关键路径（worker 状态机、retention、create_job 校验）100% 行覆盖

---

## 12. 范围外（明确不在本 spec）

- **人类面试官面评表单 / 提交流程** — README roadmap 项，独立 spec，与本 spec 无依赖关系
- **AI 面评 HITL 强制审核流程** — 由"人类面评系统" spec 接管
- **多家云 ASR provider adapter 抽象** — MVP 仅腾讯云；阿里云作为 future 选项
- **微表情 / 语音情绪识别** — 永不做（合规 + 战略）
- **实时面试官辅助提示** — 未来探索
- **批量重跑 / Prompt 版本回归测试** — Prompt v2 出现时再做

---

## 13. 风险与未决项

| 风险 | 缓解 |
|---|---|
| 腾讯会议免费版 1GB 录制配额满 | UI 提示 + retention 加速清理（缩到 60 天可配） |
| 腾讯云 ASR 中文质量在面试场景的实测准确率未知 | 落地后跑 2-3 场真实面试做 benchmark；不达标考虑切阿里云 |
| LLM 输出 JSON 格式不稳 | 重试 3 次 + Pydantic 校验；Prompt v1 收集 failure case 后 v2 优化 |
| 说话人启发式映射错 | UI 上提供"手动改 speaker"操作；MVP 不阻塞 |
| Playwright 自动化反爬触发腾会风控 | 复用现有 account_pool 的 stealth 配置；如风控加严，回退到"手动上传 mp4" |
| F1 能力模型未落地完整 | 前置依赖；服务层 hard fail + 引导 HR 完成 |

---

## 14. 实现节奏建议

按 plan→test→impl 顺序，独立 spec → writing-plans → 子任务拆解：
- **P0**：Schema + migration + service.create_job 校验链
- **P1**：worker 状态机骨架（mock 三外部 IO）+ 单测
- **P2**：tencent_meeting_recording（Playwright 真接入）
- **P3**：tencent_asr（腾讯云 SDK 真接入）
- **P4**：prompts + ai_provider 接入 + ScorecardOutput 校验
- **P5**：feishu push + audit_events 全 7 类
- **P6**：retention cron + 删除流程
- **P7**：前端 AI 面评 Tab + 候选人聚合 + 录像播放器
- **P8**：E2E smoke + 真实面试灰度（≥2 场）

每个 P 走 plan→test→impl 独立 commit，可独立验证。

---

**作者**：Claude Opus 4.7 + AgenticHR maintainer
**Brainstorm 决策记录**：见对话历史（2026-05-07，含两轮市场调研）
