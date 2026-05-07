# 2026-05-07 暂停 — F-interview-eval 续接说明

> 用户因故关机，明天再开。这份文件用于明天打开会话后快速恢复上下文。
> 镜像：`~/.gstack/projects/liboze2026-AgenticHR/checkpoints/20260507-2030-f-interview-eval-pause.md`

## 当前位置

13-task plan 的执行刚到 **Task 0** implementer dispatch，被中断。

- ✅ Brainstorm 完成（含两轮市场调研）
- ✅ Spec 写完并 commit `f14544f` — `docs/superpowers/specs/2026-05-07-ai-interview-eval-design.md`
- ✅ Plan 写完并 commit `c7eddaa` — `docs/superpowers/plans/2026-05-07-ai-interview-eval-plan.md`
- ⏸️ **Task 0 Step 1 已成、Step 2-7 未做**：
  - 已写：`app/modules/interview_eval/__init__.py`、`tests/modules/interview_eval/__init__.py`（untracked）
  - 未做：`app/config.py` 加 5 字段 / `requirements.txt` 加 SDK / `.env.example` 加 5 行 / pytest 零回归确认 / commit
- ❌ Task 1-13 未启动

## 13 项已锁定决策（产品 + 架构）

| # | 项 | 选择 |
|---|---|---|
| D1 | 形态 | 事后 Interview Intelligence |
| D2 | MVP 输出 | 转录+说话人 / Scorecard / 录用建议+优劣/追问 / 片段时间戳 |
| D3 | mp4 接入 | 腾讯会议**免费版** + 复用 Playwright 多账号池 |
| D4 | 转录 | **腾讯云 ASR**（1 元/小时） — 否决了本地 FunASR（部署门槛太高） |
| D5 | Rubric 来源 | F1 `jobs.competency_model.assessment_dimensions`（已落地） |
| D6 | 触发 | 半自动 — HR 点 [分析面试] |
| D7 | 可见性 | AI 面评 Tab + 候选人聚合 + 飞书推送 HR/面试官 |
| D8 | 范围 | 只做 AI 面评 Tab，人类面评顺延 |
| D9 | 保留 | mp4+转录稿 180 天，scorecard 永久 |
| D10 | 合规 | F1 `audit_events` WORM 表 |
| D11 | 模块 | 新建 `app/modules/interview_eval/` |
| D12 | HITL | 不做（标"AI 草稿"） |
| D13 | 异步 | ai_screening worker 模式 |

## 13 个 implementation task（在 TodoWrite 里 task #7-#20）

| TodoId | Task | 状态 |
|---|---|---|
| #7 | T0 依赖+config+骨架 | pending（Step 1 已成） |
| #8 | T1 ORM + Pydantic | pending |
| #9 | T2 Alembic 0027 | pending |
| #10 | T3 service 5 校验门 | pending |
| #11 | T4 Worker 状态机 | pending |
| #12 | T5 Playwright 下 mp4 | pending |
| #13 | T6 腾讯云 ASR | pending |
| #14 | T7 LLM Prompt | pending |
| #15 | T8 飞书 + audit | pending |
| #16 | T9 Retention | pending |
| #17 | T10 Router | pending |
| #18 | T11 前端 Tab | pending |
| #19 | T12 候选人聚合 | pending |
| #20 | T13 E2E + 覆盖率 | pending |

## 明天的接续动作（建议）

1. 读 spec + plan（路径见上）+ 这份文件
2. `git status` 确认 untracked 还是那两个 `__init__.py`，没有新飘移
3. 派 Task 0 implementer，**prompt 要明确告诉它 Step 1 已成、跳到 Step 2 开始**
4. Implementer 完成后走 spec reviewer + code quality reviewer
5. 一路推到 Task 13；末端 invoke `superpowers:finishing-a-development-branch` 决定 PR 策略

## 关键约束（重复以防遗忘）

- **CLAUDE.md** 硬规：中文 commit + `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`；`core/` 不动；完成前跑 pytest
- **Plan Task 5** Playwright selector 是骨架，由 maintainer 抓真实页面 DOM 后填实
- **Plan Task 7/8** 中 `chat_complete` / `AuditEvent` 字段名以真实代码为准，subagent 自己 grep 校核
- 用户偏好：跑完实际命令贴真实输出再说"完成"；不接受"close enough"

## Git 状态快照

- 分支：`worktree-chaos-qa-2026-5-7`
- HEAD：`c7eddaa docs(interview_eval): F-interview-eval 实现计划 (13 tasks)`
- worktree 比 `origin/main` 领先 10 个 commit
- Untracked：`app/modules/interview_eval/__init__.py`、`tests/modules/interview_eval/__init__.py`
- 工作树其它处干净
