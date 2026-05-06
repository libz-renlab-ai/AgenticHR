# Spec 0429-D-cleanup — 删除 `matching_results.job_action` 老字段

> 状态: design (待实施)
> 日期: 2026-05-06
> 前置: spec-0429-job-candidate-decision 已 ship (`ce2daa9`); 0024 迁移已落地

## 背景

0429-D 主体引入新表 `job_candidate_decisions` 作为人工决策真值, 但保留老字段 `matching_results.job_action` 双写双读以兼容旧前端。当前状态分裂:

| 路径 | 读 | 写 |
|---|---|---|
| `GET /api/matching/results` (list) | 决策表优先 + 老字段回退 | 不写 |
| `PATCH /api/matching/results/{id}/action` (legacy) | — | 老字段 + 决策表 |
| `PATCH /api/jobs/{job}/candidates/{cand}/decision` (new) | — | 仅决策表 |
| `MatchingService.score_pair` 单 row 响应 | 决策表 (P2-b 已修) | — |
| `GET /api/matching/passed-resumes/{job}` | 决策表 | — |

老字段已是冗余, 但仍占空间、仍可能写入、仍误导外部消费者 (报表/导出)。

## 目标

1. 单一真值: 所有人工决策只读写 `job_candidate_decisions`。
2. 删除 `matching_results.job_action` 列。
3. 下线 `PATCH /api/matching/results/{id}/action` 端点。
4. 旧前端兼容窗口 ≥ 14 天后再迁移。

## 非目标

- 不动 `matching_results` 其他字段 (五维分数等)。
- 不改决策表结构。

## 阶段

### Phase A — 观察期 (已开始, P2-b 已完成)

- 旧 PATCH 端点标 `deprecated=True` (OpenAPI), 命中记 INFO 日志。
- `score_pair` 单 row 响应改读决策表。
- 无前端代码动作; HR 工作流走新决策端点 (Jobs.vue 已实装)。

观察指标 (≥ 14 天):
- `legacy set_action endpoint hit` 日志条数 → 0 = 老前端已无人用
- 老 PATCH 端点失败率 → 0 = 同步逻辑健康

### Phase B — 删字段迁移

新迁移 `0025_drop_matching_results_job_action.py`:
1. `ALTER TABLE matching_results DROP COLUMN job_action` (SQLite via batch_op)
2. 不需要回填 (决策表已自 0024 起为真值)
3. 回滚: 重建列 + 从决策表反向回填

代码改动:
- `app/modules/matching/models.py` 删 `job_action` 列
- `app/modules/matching/schemas.py` 删 `MatchingResultResponse.job_action`
- `app/modules/matching/router.py:218` 删兜底 `r.job_action`
- `app/modules/matching/service.py` 删旧字段写路径
- 旧 PATCH 端点删除 (返 410 Gone, 提示用新端点)
- 测试覆盖: 老端点返 410; 决策表是唯一真值

前端改动:
- `frontend/src/api/index.js` 删 `matchingApi.setAction`
- `Jobs.vue:727` 兜底分支删 (现走 `decisionApi.set` 已是默认)
- 无 candidate_id 时给 UI 提示 "请先 promote 简历" 而非静默回退

### Phase C — 文档更新

- CHANGELOG 标注字段移除
- README 招聘流程图更新决策来源

## 风险

| 风险 | 缓解 |
|---|---|
| 老前端缓存仍调老端点 | Phase A 14 天观察 + 浏览器强制刷新弹窗 |
| 0024 回填漏数据 | 已用 `scripts/check_decision_backfill_gap.py` 验证 (dev DB 0 孤儿) |
| 外部脚本读 `matching_results.job_action` | 全仓 grep 已确认无内部消费; 外部脚本需文档通知 |
| `_to_response` 静态方法 → 决策表查询失败 | P2-b 已加 try/except 防御, 失败仅 warning |

## 测试

- 单测: `score_pair` 决策表查询命中/未命中两路径
- 集成: 老端点返 410 后前端 fallback 行为
- E2E: 全套 pytest + frontend npm run build

## 时间表

- 2026-05-06: P2-b ship (本 PR), Phase A 起点
- 2026-05-20+: 评估 legacy hit 日志, 决定是否进入 Phase B
- 2026-06-XX: Phase B 实施
