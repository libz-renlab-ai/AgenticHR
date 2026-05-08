# F-interview-eval Chaos Round 11 BUGS
> 范围：worktree-chaos-qa-2026-5-7 commit 7bb7662..2d26b1d 14 个 commit
> Audit by main agent @ 2026-05-08
> Out-of-scope hunter findings 已归档到 BUGS-out-of-scope-r11.md

## 覆盖率基准
- 11 个生产文件 / 642 stmts / 200 missing / **69%** coverage（baseline）
- 模块级：feishu_push 51% / router 49% / service 55% / tencent_meeting_recording 59%
- pytest 985 passed / 4 skipped / 0 failed

## 已发现 Bug 清单

---
## BUG-IE-001: service.create_job 校验 4 与 INSERT 之间无锁/唯一索引 — 并发可绕过

- **严重**: High
- **类型**: Concurrency / Data
- **代码位置**: `app/modules/interview_eval/service.py:69-91`
- **复现**:
  1. 准备一个 valid Interview(id=1, user_id=1, ..., meeting_account="default")
  2. 用 threading 同时启动两个 `service.create_job(interview_id=1, user_id=1)` 调用
  3. 两个线程都 query active job 返回 None（没有进行中任务），各自 add+commit
  4. 同 interview 出现两个 pending job，spawn 两个 worker 抢同一录像
- **期望**: DB unique partial index `(interview_id) WHERE status IN active states`，或 INSERT 后 verify-or-rollback
- **实际**: 5 道校验门只是逻辑层 check，后续 INSERT 没有 db 层互斥
- **攻击向量**: 并发

---
## BUG-IE-002: worker `_check_cancel` 用同一 db session 重复查询 — identity map 缓存导致看不到外部 cancel

- **严重**: High
- **类型**: Concurrency / Logic
- **代码位置**: `app/modules/interview_eval/worker.py:158-167`
- **复现**:
  1. service.create_job 建 pending job 并 spawn worker.run
  2. worker.run 进入 _check_cancel 第一次（cancel_requested=0），通过
  3. worker 进入 download 阶段（mock 阻塞 N 秒）
  4. 另一线程 `service.cancel_job(job_id=N)` 设 cancel_requested=1 + 自己的 db session commit
  5. worker 走完 download，进入 transcribe 前的 _check_cancel 第二次：
     `db.query(InterviewEvalJob).filter_by(id=job_id).first()` 命中 worker 的 SessionLocal identity map 缓存（同一 db 对象），返回内存中的旧 instance，cancel_requested 仍读为 0
  6. worker 继续跑完整流水线，cancel 永不生效
- **期望**: 每次 _check_cancel 入口 `db.expire_all()` 或 `db.refresh(job)`
- **实际**: 缓存读旧值
- **攻击向量**: 并发

---
## BUG-IE-003: worker `_chat_complete_sync` 不校验 ai_api_key/ai_base_url/ai_model 为空 — fail-not-fast

- **严重**: Medium
- **类型**: UX / Performance
- **代码位置**: `app/modules/interview_eval/worker.py:55-78`
- **复现**:
  1. settings.ai_api_key="" / ai_base_url="" / ai_model=""（默认配置即如此）
  2. worker 进入 score 阶段调 `_chat_complete_sync`
  3. POST URL = `"" + "/chat/completions"` → httpx 抛 InvalidURL/ConnectError
  4. worker 外层 retry 3 次都同样错，浪费 ~30 秒 + 3 倍 LLM 重试时间
- **期望**: 入口立即检查三字段非空，否则一次抛 RuntimeError("AI 服务未配置 ai_base_url/ai_api_key/ai_model")
- **实际**: 失败延迟到网络层，重试浪费
- **攻击向量**: 缺失值 / 配置错误

---
## BUG-IE-004: worker `_score_with_llm` markdown 剥离对 ` ```json\n...\n``` ` 失效

- **严重**: High
- **类型**: Logic
- **代码位置**: `app/modules/interview_eval/worker.py:111-118`
- **复现**:
  1. LLM 返回完整字符串 `"```json\n{\"dimensions\":[...]}\n```"`（OpenAI API 常见格式）
  2. `s = raw.strip()` → `"```json\n{...}\n```"`
  3. `s.startswith("```")` True，进入剥离分支
  4. `s = s.strip("`")` → `"\njson\n{...}\n"` （strip 只剥反引号字符，不剥换行）
  5. `s.lower().startswith("json")` False（s 开头是 `\n`），不剥 "json"
  6. `s = s.strip()` → `"json\n{...}"`
  7. `json.loads("json\n{...}")` 抛 JSONDecodeError
  8. 外层 retry 3 次永远过不了
- **期望**: `s = s.strip("`").strip()` → 然后再判 `startswith("json")` 剥头 → 再 strip
- **实际**: 永久 schema validation 失败
- **攻击向量**: 输入解析

---
## BUG-IE-005: service.create_job _spawn_worker 失败 → pending job 永远卡死

- **严重**: Medium
- **类型**: Logic / UX
- **代码位置**: `app/modules/interview_eval/service.py:91-93`
- **复现**:
  1. db.commit() 写入 InterviewEvalJob status="pending"
  2. _spawn_worker(new_job.id) 抛错（如线程数限制 / OS 资源）
  3. ServiceError 未捕获 spawn 失败，create_job 抛错给 router → 500
  4. 但 db 已 commit，遗留 pending 行 + 没有 worker 跑
  5. 该 interview 后续 create_job 一直被校验 4 拦（已有 pending）→ 永久死锁
- **期望**: try/except _spawn_worker，失败时回滚或更新 status="failed" + 友好 error_msg
- **实际**: 无兜底，留下死锁行
- **攻击向量**: 错误处理路径

---
## BUG-IE-006: tencent_asr `_submit_task` base64 内嵌整个 mp4 — 大文件实际不可用

- **严重**: High
- **类型**: Logic / Performance
- **代码位置**: `app/modules/interview_eval/tencent_asr.py:39-55`
- **复现**:
  1. 60 分钟会议 mp4 ≈ 200MB；base64 编码后 ≈ 270MB
  2. `data_b64 = base64.b64encode(f.read())` 一次性读全文件到内存
  3. `req.from_json_string(json.dumps({...}))` 把 270MB base64 写进 JSON
  4. 腾讯云 ASR CreateRecTask SourceType=1 (base64) 文档限单次请求 ≤5MB；超过 → API 拒绝
  5. 即使 API 接受，本地 RAM 峰值 ~500MB
- **期望**: 改用 SourceType=0 + 把 mp4 上传 COS 给 URL；或限制 ≤5MB 才用 base64
- **实际**: 实际录像几乎都会触发限制
- **攻击向量**: 大数据 / 资源

---
## BUG-IE-007: tencent_meeting_recording `_stream_download` 无大小上限

- **严重**: Medium
- **类型**: Resource
- **代码位置**: `app/modules/interview_eval/tencent_meeting_recording.py:35-44`
- **复现**:
  1. download 出来的 mp4 URL 可能是任意大小（云录制最长 24h ≈ 数十 GB）
  2. `for chunk in r.iter_content(chunk_size=8192): f.write(chunk); size += len(chunk)`
  3. 没有 max_bytes 守卫，磁盘可能写爆
  4. retention 也是事后清理，发生时已经晚了
- **期望**: 增 `MAX_RECORDING_SIZE = settings 配置项`，超过抛错
- **实际**: 无上限
- **攻击向量**: 大数据

---
## BUG-IE-008: feishu_push `_build_card` 引用 `settings.app_host` / `app_port` — 字段未定义会 AttributeError

- **严重**: Medium
- **类型**: Crash
- **代码位置**: `app/modules/interview_eval/feishu_push.py:86-88`
- **复现**:
  1. `grep -nE "app_host|app_port" app/config.py` 检查这俩字段
  2. 如果 Settings 类没定义，访问 `settings.app_host` 抛 AttributeError
  3. push 失败被 line 110 兜底捕获，仅日志——**但 HR 永远收不到推送**
- **期望**: 改成 `getattr(settings, "app_host", "localhost")` + `getattr(settings, "app_port", 8000)` 兜底；或确认 config.py 已定义
- **实际**: 取决于 config.py 实际字段
- **攻击向量**: 缺失值

---
## BUG-IE-009: schemas.EvidenceSegment 不校验 end_ms >= start_ms

- **严重**: Low
- **类型**: Data
- **代码位置**: `app/modules/interview_eval/schemas.py:12-16`
- **复现**:
  1. LLM 输出 `{"start_ms": 5000, "end_ms": 1000, "speaker": "candidate", "text": "x"}`
  2. Pydantic 校验通过（两个字段 ge=0 都满足）
  3. 写入 scorecard.evidence
  4. 前端显示时间段 5.0s-1.0s（负向）显示错乱，跳转失败
- **期望**: model validator 校验 end_ms >= start_ms
- **实际**: 接受非法时间段
- **攻击向量**: 数据校验

---
## BUG-IE-010: tencent_asr.transcribe 凭证空时不 fail-fast

- **严重**: Low
- **类型**: UX
- **代码位置**: `app/modules/interview_eval/tencent_asr.py:90-99`
- **复现**:
  1. settings.tencent_cloud_secret_id="" / secret_key=""
  2. `_get_client()` 构造 Credential("", "") 不抛错（SDK 允许）
  3. `_submit_task` 调 CreateRecTask 时签名为空 → 腾讯云 SDK 抛 AuthFailure
  4. transcribe 返回 RuntimeError("腾讯云 ASR 鉴权失败")——OK 但应该提前
- **期望**: transcribe 入口校验凭证非空，否则抛"未配置"而非"鉴权失败"
- **实际**: 用户看到误导的"鉴权失败"
- **攻击向量**: 缺失值

---
## BUG-IE-011: service.create_job 校验 5 (meeting_id) 用 `if not interview.meeting_id` — 空格不报错

- **严重**: Low
- **类型**: Data
- **代码位置**: `app/modules/interview_eval/service.py:60`
- **复现**:
  1. interview.meeting_id = "  "（只含空格，可能是 boss 抓 IM 时拼写错误）
  2. `if not "  "` False（非空字符串 truthy）
  3. 校验通过，进 worker
  4. worker download 时找 meeting_id="  " 在腾讯会议列表 → 永远找不到 → status=failed
- **期望**: `if not (interview.meeting_id or "").strip()`
- **实际**: 空格 meeting_id 通过校验进入 worker
- **攻击向量**: 边界值

---
## BUG-IE-012: 0027 migration upgrade 防御 `if not in get_table_names` 但不验列结构

- **严重**: Low
- **类型**: Migration
- **代码位置**: `migrations/versions/0027_add_interview_eval.py:23, 63`
- **复现**:
  1. 假设旧 db 已经手动建了 `interview_eval_jobs` 但缺 `cancel_requested` 列（人工 patch 不一致）
  2. alembic upgrade 0027 → `if "interview_eval_jobs" not in get_table_names()` False，**跳过建表**
  3. alembic_version 升到 0027
  4. 后续代码访问 `cancel_requested` → SQLite OperationalError: no such column
- **期望**: 增列检查 `inspect.get_columns("interview_eval_jobs")`，缺列就 alter add
- **实际**: 静默跳过 → 运行时崩
- **攻击向量**: Migration 边界
- **注**: 历史债场景（dev db stamp 0001→0026 已暴露过类似问题）；生产 fresh deploy 不触发

---
## BUG-IE-013: retention.purge_expired 用约定路径而非 job.recording_path 字段

- **严重**: Low
- **类型**: Data
- **代码位置**: `app/modules/interview_eval/retention.py:31-32`
- **复现**:
  1. RECORDING_DIR 配置改了（如挂 NFS），新 download 写到新路径，job.recording_path 字段记新路径
  2. retention 用 `os.path.join("data/recordings", f"{job.id}.mp4")` 拼旧路径
  3. os.path.exists 旧路径 False → 跳过 → 新路径文件未删
- **期望**: 优先用 `job.recording_path`，次选拼接路径
- **实际**: 配置变化时漏删
- **攻击向量**: 配置 / 一致性

---
## BUG-IE-014: worker LLM retry 不区分错误类型 — 临时网络错和永久 schema 错都 retry 3 次

- **严重**: Low
- **类型**: Performance
- **代码位置**: `app/modules/interview_eval/worker.py:225-237`
- **复现**:
  1. LLM API 502 / 网络超时 → 应 retry（合理）
  2. LLM 返回稳定的非法 schema（模型本身限制） → retry 3 次都同样错，浪费 token + 时间
- **期望**: 区分 ConnectError/TimeoutError vs JSONDecodeError/ValidationError；前者 retry，后者退指数 backoff 或直接报
- **实际**: 一刀切 3 次
- **攻击向量**: 性能

---
## BUG-IE-015: feishu_push.push 触发分析的 HR 自己也会收一条卡片（自给自己）

- **严重**: Low
- **类型**: UX
- **代码位置**: `app/modules/interview_eval/feishu_push.py:102-105`
- **复现**:
  1. HR 用户 user_id=1 点 [分析面试]
  2. _publish_feishu(interview, sc) 调 push
  3. _resolve_hr_feishu_id(interview.user_id=1) → HR 自己的 feishu_user_id
  4. _send_card → HR 收到"AI 面评已生成"卡片
  5. 但 HR 是触发者，前端已经看到 done 状态了，飞书卡片是冗余打扰
- **期望**: HR 是触发者时跳过 HR 通道，仅给面试官；或加配置开关
- **实际**: 必发自己一份
- **攻击向量**: UX

---

## 覆盖率快照（第 1 轮 — 仅 F-interview-eval）

| 维度 | 覆盖估计 | 总量估计 | 百分比 |
|------|---------|---------|-------|
| 函数 | 35 | 50 | 70% |
| 分支 (if/else/try) | 38 | 60 | 63% |
| 输入入口 (8 endpoints + 2 cron) | 4 | 10 | 40% |
| 错误路径 | 12 | 25 | 48% |
| 攻击向量类型 | 5 | 8 | 63% |

**已发现 Bug 数**: 15（High: 4, Medium: 4, Low: 7）

## 优先修复顺序
1. **BUG-IE-004** LLM markdown 剥离失效（实际场景 100% 触发）
2. **BUG-IE-002** worker session cache 看不到 cancel
3. **BUG-IE-001** create_job 并发竞争
4. **BUG-IE-006** ASR base64 大文件 API 拒绝
5. **BUG-IE-008** feishu_push settings.app_host AttributeError
6. **BUG-IE-003** LLM 配置 fail-fast
7. **BUG-IE-005** spawn_worker 失败兜底
8. **BUG-IE-007** download size limit
9. **BUG-IE-009** evidence end_ms >= start_ms
10. **BUG-IE-011** meeting_id strip 校验
11. **BUG-IE-010** ASR 凭证 fail-fast
12. **BUG-IE-013** retention 用 job.recording_path
13. **BUG-IE-014** LLM retry 区分错误类型
14. **BUG-IE-015** HR 自己不发卡片
15. **BUG-IE-012** migration 列结构验证（生产 fresh deploy 不触发，最低）
