# F-interview-eval Heartbeat 自愈机制 — 设计文档

> 日期: 2026-05-11
> 触发: chaos round 11 上线后演示发现，跨进程重启场景下未完成任务永久僵尸化
> 范围: 仅 F-interview-eval 模块；与 chaos round 11 修复正交

## 背景与问题

`InterviewEvalJob` 当前生命周期：

```
pending → downloading → transcribing → scoring → done
                                              ↘ failed / cancelled
```

非终态行 (`pending|downloading|transcribing|scoring`) 由 daemon thread worker 推进。
进程重启（uvicorn 重启 / OS kill / OOM）后：
- worker 线程消失
- DB 行仍是非终态
- 校验 4 拦截"已有进行中任务"
- HR 永久无法重新触发分析

BUG-IE-005 修了同进程内 spawn 失败兜底，未覆盖跨进程残留。

## 设计目标

1. **自愈**：服务重启后能自动识别并标记僵尸任务为 `failed`，附 `error_msg` 说明原因
2. **可观察**：HR 能在 Dialog 里看到 "上次任务因服务中断被取消，请重跑"
3. **可配置**：心跳间隔、判死阈值、扫描频率全部 settings 可调
4. **不破坏现状**：不动 BUG-IE-001..015 的修复；测试基线不退化

## 设计选择

### 字段：`InterviewEvalJob.last_heartbeat: DateTime | None`

- 加在 job 表上，nullable（历史行默认 NULL，第一次扫描即视为陈旧）
- 避免新增 table 增加 join 成本
- Alembic 0028 迁移

### Worker 心跳

worker.py 在每次 `_set_status(...)` 时同步写 `last_heartbeat = utcnow()`。
LLM 评分阶段最长（5-30 秒），_score_with_llm 调用前后各 bump 一次，避免 scoring 中段被误判。

### Reconcile 模块

`app/modules/interview_eval/reconcile.py`:

```python
def sweep_stale_jobs(threshold_seconds: int) -> int:
    """扫所有非终态 job, last_heartbeat 早于 threshold 的 → failed.
    pending 状态 (尚未 spawn) + 旧无 heartbeat 也视为陈旧.
    返回 sweep 数量.
    """
```

### 接入点

1. **App startup**: `app/main.py` startup 钩子调用一次，恢复服务重启场景
2. **周期 cron**: 复用 retention 已有 APScheduler 风格 (`asyncio.sleep` loop)，每 `interview_eval_reconcile_period_seconds` 调一次
   - 兜底场景：worker thread 自身在 process 内死亡（罕见但发生过：未捕获异常 / OOM 子线程）

### 配置项

| Setting | 默认 | 说明 |
|---|---|---|
| `interview_eval_heartbeat_interval_seconds` | 30 | worker 心跳期望间隔（仅文档值，worker 通过 `_set_status` 触发） |
| `interview_eval_stale_threshold_seconds` | 180 | 超过此值无心跳→判死（6 倍心跳容忍） |
| `interview_eval_reconcile_period_seconds` | 300 | 周期扫描间隔 |

## TDD 测试清单

### `tests/modules/interview_eval/test_reconcile.py`
- `test_sweep_marks_stale_pending_as_failed` — pending + last_heartbeat 过期 → failed
- `test_sweep_marks_stale_scoring_as_failed` — scoring + last_heartbeat 过期 → failed
- `test_sweep_skips_fresh_job` — 心跳新鲜的非终态任务 → 不动
- `test_sweep_skips_terminal_done_cancelled` — done/cancelled/failed → 不动
- `test_sweep_null_heartbeat_treated_as_stale` — last_heartbeat IS NULL → 失败（历史残留）
- `test_sweep_sets_error_msg` — failed 行带 "服务中断" 提示
- `test_sweep_returns_count` — 返回扫到的数量

### `tests/modules/interview_eval/test_worker_heartbeat.py`
- `test_set_status_updates_heartbeat` — _set_status 顺带更新 last_heartbeat
- `test_score_phase_bumps_heartbeat_before_and_after_llm` — scoring 阶段前后各 bump 一次

### `tests/modules/interview_eval/test_migration_0028.py`
- `test_upgrade_adds_last_heartbeat_column`
- `test_downgrade_drops_last_heartbeat_column`

## 实施顺序

1. 写测试（红） ← TDD
2. settings + models + migration（让 reconcile / worker 测试能 import）
3. 实现 reconcile.py（让 reconcile 测试绿）
4. 改 worker.py 加心跳（让 worker 测试绿）
5. 接入 app/main.py startup + scheduler
6. 全套 pytest 跑通 + 重启 demo 服务端到端验证

## 不在范围

- 不重写为外部 queue（Celery / RQ）— 当前规模不需要
- 不做 worker 重启 / 恢复中断（直接 failed，让 HR 重跑）— 简单且足够
- 不改 chaos round 11 的任何修复
