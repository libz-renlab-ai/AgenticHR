# Spec: AI 智能筛选 (Jobs.vue → ai_smart Tab)

> 状态: design (待实施)
> 日期: 2026-05-06
> 前置: spec-0429-job-candidate-decision (`ce2daa9`) 已 ship; `job_candidate_decisions` 表生效

## 背景

现状 `Jobs.vue` "AI智能筛选" tab 是占位 `<el-empty description="开发中" />`。
HR 已可走 "匹配候选人" tab (五维硬筛 + 模型打分),但缺一种**轻量直觉式**筛选:
HR 只想说"给我挑 10 个最合适的",不想配能力模型/权重。

## 目标

1. HR 在岗位详情 → ai_smart tab 输入 **人数 N** 或 **比例 X%**,一键启动 AI 筛选。
2. 后端调**本地 Claude Code CLI** (`claude --print` headless 模式),把 JD + 多份 PDF 喂给它,让它**横向对比** 0-100 打分 + 给理由。
3. 跑完按 N/X% 取 top → 直接写 `job_candidate_decisions.action='passed'`,HR 在结果列表可手动改 reject (复用 0429-D 端点)。
4. 前端实时进度;退出再回来还原状态;可取消。

## 非目标

- 不调 Anthropic API/SDK (走 Claude Code 订阅,免 token 费)。
- 不动 `matching_results` 表结构、不动 `job_candidate_decisions` schema。
- 不替换"匹配候选人"tab,共存。
- 不做跨 job 横向对比 (单 job 内挑人)。

## 用户流

```
[HR 打开岗位编辑] → [ai_smart tab]
  │
  ├ 候选池预览: "硬筛通过 N 人 (排除已 reject)"
  │   若 = 0 → 提示 "请先在 [匹配候选人] tab 跑硬筛"
  │
  ├ 输入: ① 人数 N (1..池大小) ② 比例 X% (1..100)
  │
  ├ [开始分析] → 创建 screening_job(status=running) → worker 异步跑
  │
  ├ 进度区: 进度条 X/Y, 当前批次, "取消" 按钮
  │   退出再回来 → 读 status running → 还原此视图
  │
  ├ 跑完 → status=done → 显示结果列表
  │   每行: 名字 / 分数 / 理由 / [通过 ✓] [拒绝 ✕] (覆盖 CC 决定)
  │   通过的人已自动写 job_candidate_decisions.action='passed'
  │
  └ 异常: failed → 显错误 + [重试] 按钮
       cancelled → 显已处理结果 + [重新开始]
```

## 数据模型

### 新表 `screening_jobs`

| 列 | 类型 | 说明 |
|---|---|---|
| id | INT PK | |
| user_id | INT FK | 多租户隔离 |
| job_id | INT FK | 关联岗位 |
| mode | VARCHAR(10) | `count` / `ratio` |
| threshold | INT | mode=count 时是 N (绝对人数);mode=ratio 时是 X (1..100, 百分比整数) |
| status | VARCHAR(16) | `pending` / `running` / `done` / `failed` / `cancelled` |
| total | INT | 候选池大小 (启动时锁定) |
| processed | INT | 已分析数 (worker 增量更新) |
| cancel_requested | INT | 0/1 |
| error_msg | TEXT | failed 时填 |
| started_at | DATETIME | |
| finished_at | DATETIME | |
| created_at | DATETIME | |

**约束:** `(user_id, job_id, status='running')` 唯一 — 单 job 同时只能有 1 个 running 任务。

### 新表 `screening_job_items`

| 列 | 类型 | 说明 |
|---|---|---|
| id | INT PK | |
| screening_job_id | INT FK | |
| candidate_id | INT FK | IntakeCandidate.id (硬筛通过那批源头) |
| pdf_path | VARCHAR(500) | 启动时锁定 (防 candidate 之后改 pdf) |
| score | INT | 0-100, null = 未跑/失败 |
| reason | TEXT | CC 输出理由 |
| pass | INT | 0/1, 跑完按 mode/threshold 切线 |
| error | TEXT | 单条失败原因 |
| batch_no | INT | 第几批 (调试用) |
| processed_at | DATETIME | |

### 迁移 `0025_ai_screening.py`

- 建两张表 + 索引 `idx_screening_jobs_user_job (user_id, job_id, status)`
- `idx_screening_job_items_job (screening_job_id, score DESC)` (取 top 用)

## 后端

### 模块 `app/modules/ai_screening/`

```
ai_screening/
├── models.py          # ORM
├── schemas.py         # pydantic
├── router.py          # /api/jobs/{job_id}/ai-screening/*
├── service.py         # start/cancel/status/result
├── worker.py          # 异步任务 (asyncio.create_task)
├── cli_runner.py      # subprocess claude CLI 封装
└── prompts.py         # 系统/用户 prompt 模板
```

### CLI 调用 (`cli_runner.py`)

```python
async def run_claude_batch(jd: str, candidates: list[CandIn], timeout: int = 300) -> list[CandOut]:
    """
    candidates: [{candidate_id, pdf_path}, ...]  长度 ≤ 10
    返回: [{candidate_id, score:0-100, reason:str}, ...]
    """
    prompt = render_prompt(jd, candidates)
    proc = await asyncio.create_subprocess_exec(
        "claude", "--print", "--output-format", "json",
        prompt,
        stdout=PIPE, stderr=PIPE,
    )
    # 取消支持: 把 proc 暴露给 worker, cancel_requested 时 proc.terminate()
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return parse_claude_json(stdout)
```

**关键点:**
- `claude --print` headless 模式,走当前用户订阅,不消耗 API key
- `--output-format json` 拿结构化输出
- `proc.terminate()` 可中断
- 超时 5 分钟 (单批 10 份 PDF + JD 估算)
- prompt 里 PDF 用绝对路径, CC 自己 Read 工具读 PDF

### Worker 流程 (`worker.py`)

```
async def run_screening(screening_job_id):
    job = load(screening_job_id)
    candidates = pre_filter(job.job_id)  # 硬筛通过 + 排除已 reject
    persist_items(job.id, candidates)    # 锁定池
    update_total(job.id, len(candidates))

    BATCH = 10
    batches = chunk(candidates, BATCH)

    if len(batches) == 1:
        # 单批 → 直接对比打分
        results = await run_claude_batch(jd, batches[0])
        write_items(results)
        update_processed(job.id, len(batches[0]))
    else:
        # 多批 → 初评 + 决赛
        # Stage 1: 各批独立打分
        all_scored = []
        for i, batch in enumerate(batches):
            if check_cancel(job.id): return
            r = await run_claude_batch(jd, batch)
            write_items(r, batch_no=i+1)
            update_processed(job.id, +len(batch))
            all_scored.extend(r)

        # Stage 2: 各批 top 取 max(N+5, 20) 进决赛重打分 (公平横向对比)
        if check_cancel(job.id): return
        finalists_n = max(threshold + 5, 20)  # 决赛参赛者数
        finalists = top_k(all_scored, finalists_n)
        if len(finalists) > BATCH:
            # 决赛仍超批,再分一次,但只算一轮 (不再递归)
            for i, fb in enumerate(chunk(finalists, BATCH)):
                if check_cancel(job.id): return
                r = await run_claude_batch(jd, fb)
                update_items_score(r, batch_no=100+i)  # 100+ 标记决赛批
        else:
            r = await run_claude_batch(jd, finalists)
            update_items_score(r, batch_no=100)

    # 跑完, 切线, 写决策
    finalize(job.id)
```

### `finalize()`

1. 按 `score DESC` 排序 items
2. mode=count → top N 标 pass=1; mode=ratio → top ceil(total*X/100) 标 pass=1
3. 同分 tie-breaker: candidate_id ASC (稳定)
4. **直接写** `job_candidate_decisions`:
   - 每个 pass=1 → `set_decision(action='passed')`
   - 未通过的**不动决策表** (HR 看完可选手动 reject)
5. status=done, finished_at=now

### API

| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/jobs/{job_id}/ai-screening/preview` | GET | 返 `{eligible_count}` (硬筛通过且未 reject) |
| `/api/jobs/{job_id}/ai-screening/start` | POST | body: `{mode, threshold}` → 返 `{screening_job_id}` |
| `/api/jobs/{job_id}/ai-screening/current` | GET | 当前 running 或最新一次 → 返 `{id, status, total, processed, started_at, finished_at, error_msg}` |
| `/api/ai-screening/{id}/cancel` | POST | 写 cancel_requested=1 |
| `/api/ai-screening/{id}/items` | GET | 跑完取结果列表 (含 score/reason/pass/candidate 名字) |

### Prompt 模板 (`prompts.py`)

```
你是资深 HR 面试官。下面给你 1 个岗位 JD 和 N 份候选人简历 PDF。
请综合 JD 要求, 横向对比所有候选人, 给每位 0-100 分:
- 90-100: 完全胜任, 强烈推荐
- 75-89: 胜任, 推荐
- 60-74: 部分匹配, 可考虑
- 0-59: 不匹配
理由 ≤ 80 字, 必须引用简历具体内容/JD 关键词, 禁空话。

== JD ==
{jd_text}

== 候选人 ==
{for each: candidate_id={id}, pdf={absolute_path}}

请用 Read 工具读每份 PDF。

输出严格 JSON, 无任何其他文字:
[{"candidate_id":1, "score":85, "reason":"..."}, ...]
```

## 前端

### `Jobs.vue` ai_smart tab 改写

```vue
<el-tab-pane label="AI智能筛选" name="ai_smart" v-if="editingJob">
  <AiScreeningPanel :job-id="editingJob.id" />
</el-tab-pane>
```

### 新组件 `AiScreeningPanel.vue`

状态机:
- `idle` 没跑过/已 done → 显配置区 + (若有结果) 历史列表
- `running` → 显进度条 + 取消
- `done` → 显结果列表 + 重跑按钮
- `failed/cancelled` → 显错误 + 重试

```vue
<template>
  <!-- idle: 配置 -->
  <div v-if="status === 'idle'">
    <el-form>
      <el-form-item label="筛选方式">
        <el-radio-group v-model="mode">
          <el-radio value="count">指定人数</el-radio>
          <el-radio value="ratio">通过比例</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item :label="mode === 'count' ? '通过人数' : '通过比例 (%)'">
        <el-input-number v-model="threshold" :min="1" :max="mode === 'count' ? eligibleCount : 100" />
      </el-form-item>
      <div>候选池: 硬筛通过 {{ eligibleCount }} 人 (已排除 reject)</div>
      <el-button type="primary" :disabled="eligibleCount === 0" @click="start">开始分析</el-button>
    </el-form>
  </div>

  <!-- running: 进度 -->
  <div v-else-if="status === 'running'">
    <el-progress :percentage="percent" :format="() => `${processed}/${total}`" />
    <el-button @click="cancel">取消</el-button>
  </div>

  <!-- done: 结果 -->
  <div v-else-if="status === 'done'">
    <div v-for="item in items" :key="item.id" class="screening-row">
      <span>{{ item.candidate_name }}</span>
      <el-tag :type="item.pass ? 'success' : 'info'">{{ item.score }}</el-tag>
      <p>{{ item.reason }}</p>
      <el-button-group>
        <el-button size="small" :type="decision[item.candidate_id]==='passed' ? 'success' : ''" @click="setPass(item)">通过</el-button>
        <el-button size="small" :type="decision[item.candidate_id]==='rejected' ? 'danger' : ''" @click="setReject(item)">拒绝</el-button>
      </el-button-group>
    </div>
    <el-button @click="reset">重新筛选</el-button>
  </div>

  <!-- failed/cancelled -->
  <div v-else>
    <el-alert :title="errorMsg || '已取消'" type="warning" />
    <el-button @click="reset">重试</el-button>
  </div>
</template>
```

### 进度推送

**轮询**, 不上 SSE:
- `running` 状态每 2s 拉 `/current` → 更新 processed/status
- 跑完 (status=done) 拉 `/items` 一次
- 简单可靠, 无中间件

### 持久化还原

- 组件 mount 时 → GET `/current`
- 若 status=running → 直接进 running 视图 + 启动轮询
- 若 status=done → 拉 items 进结果视图
- 若无任何记录 → idle

## 异常处理

| 场景 | 处理 |
|---|---|
| `claude` CLI 未装 | start 时检测 `which claude`, 不存在 → 返 503 + 提示用户安装 |
| 子进程超时 | 单批 5min timeout, 超时 → 该批所有 item.error="timeout", batch_no 标记;若全部批超时 → status=failed |
| JSON 解析失败 | 重试 1 次 (重发同 prompt), 仍失败 → batch fail |
| PDF 读不出 | CC 自己报错 → reason="无法读取" + score=0 |
| Worker crash | 进程级 try/except, status=failed + error_msg |
| 候选池空 | start 直接 422 "无可筛选候选人" |
| 重复启动 | start 时查 running 任务存在 → 返 409 |

## 取消机制

```
HR 点取消
  → POST /cancel → DB 写 cancel_requested=1
Worker 在每批之间检查
  → if cancel_requested: proc.terminate(); status=cancelled; return
```

子进程级中断:`proc.terminate()` (Windows 下走 `TerminateProcess`)。

## 测试

### 单测
- `cli_runner.parse_claude_json` 各种格式容错
- `worker.finalize` 切线逻辑 (count/ratio, tie-breaker)
- `service.preview` 候选池过滤 (硬筛+reject)
- `service.start` 重复启动 → 409

### 集成
- start → mock CLI (返写死 JSON) → status 走 running → done
- cancel → 中途 cancel → status=cancelled
- 多批走决赛分支
- finalize → `job_candidate_decisions` 落 N 行 passed
- 退出还原: 创 running 任务 → 直接 GET /current 还原

### E2E (可选)
- 真调 `claude --print --output-format json` 跑 1 个 dummy job + 2 份 PDF
- 标 `pytest.mark.requires_claude_cli`, CI 默认 skip

## 安全

- pdf_path 限制必须在配置的简历存储目录下 (防路径穿越塞别的文件给 CC)
- prompt 注入风险: PDF 内容不直接拼 prompt, 只给路径让 CC 用 Read 工具读 — CC 自己有沙箱
- 结果 JSON 解析严格, 拒收非数字 score / 缺字段

## 时间表

- Day 1: 迁移 0025 + ORM + service preview/start/cancel/current
- Day 2: cli_runner + worker (单批分支) + 单测
- Day 3: worker 多批决赛 + finalize 写决策表 + 集成测
- Day 4: 前端 AiScreeningPanel.vue + 状态还原 + 轮询
- Day 5: 异常路径 + E2E 真跑 + 文档

## 风险

| 风险 | 缓解 |
|---|---|
| Claude Code CLI 路径不可达 / 不在 PATH | 启动检测 + 配置项 `CLAUDE_CLI_PATH` 兜底 |
| 单批 10 份 PDF 太大 → context 爆 | 实测调小批 (5/批),配置项 `AI_SCREEN_BATCH_SIZE` |
| HR 同时跑 2 个 job 的 AI 筛选 → 子进程并发 | 设计上 (user_id, job_id, running) 唯一,但跨 job 不限,可能拖慢机器 → V2 加全局并发上限 |
| 决赛 stage 2x 调用 → HR 等更久 | 进度条到 stage 1 末显 "进入决赛对比中..." 让 HR 知情 |
| CC 输出格式漂 (中英混合 / 多余 markdown) | prompt 强约束 + parse 容错 + 重试 1 次 |
| pdf_path 启动后被改/删 | items 启动时落 pdf_path 快照,worker 用快照路径,不再读 candidate 当前 pdf_path |

## 未来扩展 (非本期)

- 多 job 跨岗位推荐 (一份简历适合哪些 job)
- 复用历史结果: 同一份 PDF 跑过不同 job 的筛选 → 缓存 score+reason
- 模型可选 (claude vs gpt vs gemini CLI)
- 可视化分数分布直方图

## 当前提交状态 (2026-05-06, commit 0cfb022)

✅ 已完成:
- 后端 7 文件 + 迁移 0025 + 5 端点
- 前端 2 组件 + Jobs.vue 接入 + aiScreeningApi
- 47/47 单测+集成绿 (mock cli_runner.run_claude_batch)
- 后端全量回归 841/0 fail

⚠️ **未做完善检查/测试** (post-ship 待办):

1. **真跑 E2E** — commit 仅 mock 跑通, 未真调 `claude --print` 子进程
   - 需用真 PDF + JD 跑完整 HR 流: 启动 → 进度 → 通过/拒绝
   - 验证 claude 真返回 JSON 格式是否稳定 (含中英混合/markdown 包裹概率)

2. **claude CLI 异常路径** — 未覆盖
   - 未登录 claude (无 token) 时 stderr / exit code
   - `--add-dir` 路径含中文/空格 (Windows 常见)
   - PDF 文件路径不存在 CC 怎么报错
   - JSON 输出漂移真发生时 prompts.py 是否需微调

3. **前端体验** — 未验证
   - 退出再进 (刷新/切 tab) 还原 running 视图
   - 进度条 2s 轮询无 UI 卡顿
   - cancel → cancelled 实际延迟 (取决当前批次剩余时间)
   - done 状态手动改 decision 实时反映

4. **边界 case** — 未补测
   - batch_size hardcode 10, 未做配置项
   - 多用户并发跑 ai_screening 无全局上限 (V2)
   - start → 立即 cancel (子进程未起) 的边界
   - 决赛批失败 fallback 到 stage1 分数的真路径

5. **`frontend/CLAUDE.md`** — TeamAgent 自动文件仍未跟踪, 待决策入 .gitignore 或入库
