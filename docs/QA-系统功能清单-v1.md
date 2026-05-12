# AgenticHR 系统功能清单（QA 逐条测试用）

> **文档版本**：v1.0
> **生成日期**：2026-05-12
> **覆盖范围**：仓库内所有可执行代码（后端 / 前端 / 浏览器扩展 / TeamAgent / 运维脚本 / 数据库迁移）
> **使用方法**：每一条都是一项可独立验证的功能。QA 同学按本文目录顺序逐条执行；每条标注【触发方式】【预期结果】【相关文件】，便于回归对照。
> **编号规则**：`F-<模块>-<序号>`（F = Feature）。已知历史 BUG 用 `BUG-xxx` / `BUG-IE-xxx` / `IE-xxx` 标识，附录 A 列回归点。

---

## 目录

| 章节 | 模块 | 功能数（含子项） |
|---|---|---|
| 1 | 系统启动与基础设施 | 12 |
| 2 | 用户认证与权限 (`/api/auth`) | 8 |
| 3 | 简历管理 (`/api/resumes`) | 22 |
| 4 | 岗位管理与硬筛 (`/api/screening`) | 18 |
| 5 | 能力模型与技能库 (`/api/skills`) | 11 |
| 6 | AI 智能筛选 (`/api/jobs/.../ai-screening`) | 9 |
| 7 | F2 简历匹配 (`/api/matching`) | 11 |
| 8 | F4 IM 智能接待 (`/api/intake`) | 24 |
| 9 | 面试调度 (`/api/scheduling`) | 21 |
| 10 | 腾讯会议账号池 (`/api/meeting`) | 4 |
| 11 | 通知系统 (`/api/notification`) | 6 |
| 12 | AI 面试评价 (`/api/interview-eval`) | 14 |
| 13 | 飞书机器人 (`/api/feishu`) | 4 |
| 14 | Boss 直聘自动化 (`/api/boss`) | 5 |
| 15 | HITL 审核 (`/api/hitl`) | 7 |
| 16 | 招聘评分 (`/api/recruit`) + 全局设置 (`/api/settings`) | 6 |
| 17 | 数据库迁移 (alembic) | 28 |
| 18 | 运维脚本 (`scripts/`) | 7 |
| 19 | 启动与打包 (launcher / build / dev) | 8 |
| 20 | Edge 浏览器扩展 (`edge_extension/`) | 14 |
| 21 | 前端 Vue 应用 (12 页面 + 6 组件) | 60+ |
| 22 | TeamAgent 自学习系统 (`.teamagent/`) | 9 |
| 附录 A | 历史 BUG 回归清单 | 50+ |
| 附录 B | 完整环境变量配置项 | 60+ |

---

## 0. 测试基础说明

### 0.1 环境准备
- **后端**：`python launcher.py` 或 `python -m uvicorn app.main:app --port 8000`
- **前端**：`cd frontend && npm run dev`（或一键 `dev.bat`）
- **数据库**：`data/recruitment.db`（SQLite，WAL 模式）
- **测试账号**：首次注册自动成为系统首任管理员；后续注册需要管理员开放
- **测试模式跳鉴权**：`AGENTICHR_TEST_BYPASS_AUTH=1` + 设置 `PYTEST_CURRENT_TEST` 可在自动化测试中跳过 JWT

### 0.2 通用验证项
- 所有 `/api/*` 路由（白名单除外）必须带 `Authorization: Bearer <token>`，缺失 → 401「未登录」，无效 → 401「登录已过期」
- 所有跨用户访问其它人的资源 → 统一 404（不暴露存在性，BUG-056）
- 所有 `clear-all` / `delete` 高危操作 → 前端二次确认（部分需输入"确认清空"）

---

## 1. 系统启动与基础设施

| 编号 | 功能 | 触发方式 | 预期结果 | 相关文件 |
|---|---|---|---|---|
| F-INFRA-01 | SQLite 自动建表 | 首次启动 | `data/recruitment.db` 出现，所有表存在 | `app/database.py` |
| F-INFRA-02 | WAL 模式启用 | 启动 | DB 同目录出现 `*.db-wal` / `*.db-shm` | `app/database.py` |
| F-INFRA-03 | 自动列迁移 | 启动 | 缺列自动 ALTER ADD；字段数据回填执行；`_migration_flags` 防重复 | `app/database.py: _migrate()` |
| F-INFRA-04 | 启动时清理僵尸 ScreeningJob | 启动 | `>10 分钟`未进展的 `ScreeningJob` 标 `failed` | `app/main.py` startup hook |
| F-INFRA-05 | 飞书 WS 后台启动 | 启动且飞书凭证齐全 | 后台线程连接成功；缺凭证仅警告，不阻塞 | `app/adapters/feishu_ws.py` |
| F-INFRA-06 | 简历 AI 解析 worker 自动续跑 | 启动 | 检测到 `ai_parsed='no'` 的行后启动 daemon 线程；幂等 | `app/modules/resume/worker.py` |
| F-INFRA-07 | Interview-Eval 后台任务 | `INTERVIEW_EVAL_ENABLED=true` 启动 | retention（180 天）+ reconcile（300s）线程开启 | `app/modules/interview_eval/` |
| F-INFRA-08 | CORS 中间件 | 任意请求 | OPTIONS 预检通过；白名单源放行 | `app/main.py` |
| F-INFRA-09 | JWT 鉴权中间件 | `/api/*` 非白名单 | 缺 token → 401；token 失效 → 401；malformed sub → 401 | `app/main.py` |
| F-INFRA-10 | 健康检查 `/api/health` | 任意 | 匿名返回 `{status, app_name}`；登录返完整服务配置 | `app/main.py` |
| F-INFRA-11 | 健康检查 `/api/health/detailed` | 已登录 | 返回 feishu/ai/email/meeting 各项配置状态 | `app/main.py` |
| F-INFRA-12 | API 404 兜底 | `/api/不存在的路径` | 返回 JSON 404 而非 HTML（BUG-150） | `app/main.py` |
| F-INFRA-13 | SPA fallback | `/任意非 API 路径` | 返回 `frontend/dist/index.html`；无缓存 | `app/main.py` |
| F-INFRA-14 | 静态资源缓存 | `/assets/*.js` | 长期缓存（chunk hash） | `app/main.py` |

---

## 2. 用户认证与权限 `/api/auth`

| 编号 | 功能 | 触发方式 | 预期结果 | 相关文件 |
|---|---|---|---|---|
| F-AUTH-01 | 系统初始化检查 | `GET /api/auth/status` | 返回是否已有用户 | `app/modules/auth` |
| F-AUTH-02 | 首任管理员注册 | `POST /api/auth/register`，库内尚无用户 | 201；返 token+user；后续注册被锁 | 同上 (BUG-010) |
| F-AUTH-03 | 后续注册拦截 | 同上但已有用户 | 403 | 同上 |
| F-AUTH-04 | 用户名重复 | 同名注册 | 409 | 同上 |
| F-AUTH-05 | 用户名/密码长度校验 | username 2-50；password 6-100 | 不符合 → 422 | 同上 |
| F-AUTH-06 | 登录速率限制 | 同 IP 连续 10 次失败 | 锁定 15 分钟，返 429；成功登录清零计数（BUG-009） | 同上 |
| F-AUTH-07 | 当前用户信息 | `GET /api/auth/me` | 返完整 user；token 无效 → 401（BUG-015） | 同上 |
| F-AUTH-08 | is_active=false 用户 | 登录 | 401 | 同上 |
| F-AUTH-09 | Token 30 天有效期 | 任意请求 | 30 天后失效 | `app/modules/auth` |

---

## 3. 简历管理 `/api/resumes`

### 3.1 增删改查
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-RES-01 | 创建/更新简历（智能去重） | `POST /api/resumes/` | phone > email > 同名无联系方式 优先级去重；新简历触发 AI 解析 worker |
| F-RES-02 | 批量导入 | `POST /api/resumes/batch`（≤100） | 超 100 → 400；返 created/duplicates 计数 |
| F-RES-03 | PDF 上传 | `POST /api/resumes/upload`（multipart） | 落盘→解析→ensure_candidate→3 hard slot 填→promote→Resume；图片型 PDF→422 并删文件 |
| F-RES-04 | 路径穿越防御 | 上传带 `../` 文件名 | 拒绝（BUG-084） |
| F-RES-05 | 清空全部 | `DELETE /api/resumes/clear-all` | 级联清简历+候选人+槽位+面试+通知+匹配+outbox+PDF 文件（BUG-062） |
| F-RES-06 | 简历列表 | `GET /api/resumes/?keyword=&status=&page=` | keyword 限长 64 防 DoS（BUG-082） |
| F-RES-07 | 单条获取 | `GET /api/resumes/{id}` | 跨用户 → 404（BUG-056） |
| F-RES-08 | 更新简历 | `PATCH /api/resumes/{id}` | 修改 status / reject_reason 自动 promote 候选人（BUG-057）；跨用户 FK 拒写（BUG-058） |
| F-RES-09 | 删除简历 | `DELETE /api/resumes/{id}` | 级联清 interview/match/decision/outbox/PDF |
| F-RES-10 | PDF 下载 | `GET /api/resumes/{id}/pdf` | 流式返回；归属校验 |
| F-RES-11 | 二维码生成/重生成 | `GET /api/resumes/{id}/qr?regen=1` | 320×320 PNG；扫码可填手机号 |
| F-RES-12 | 简历库 PDF 路径设置 | `GET /api/resumes/settings/storage-path` | 返根目录 |
| F-RES-13 | Boss ID 批量查重 | `POST /api/resumes/check-boss-ids`（≤1000） | 返已存在 boss_id 列表 |

### 3.2 AI 解析 worker
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-RES-14 | 解析进度查询 | `GET /api/resumes/ai-parse-status` | 返 `{total, parsed, in_progress}` |
| F-RES-15 | 全量启动 AI 解析 | `POST /api/resumes/ai-parse-all` | 幂等（worker 已跑则跳过） |
| F-RES-16 | 单条解析 | `POST /api/resumes/{id}/ai-parse` | 后台任务；失败标 `ai_parsed=failed` |
| F-RES-17 | 解析超时停止 | 等待 3 分钟无进展 | 前端轮询自动停止 |

### 3.3 关键业务规则
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-RES-18 | 去重优先级 | 创建多条 | phone 优先匹配；email 次之；同名+无 contact 最后 |
| F-RES-19 | 字段覆盖策略 | 同人多次 | 非空才覆盖；raw_text 仅更长才覆盖 |
| F-RES-20 | per-job vs global status | screening | screening 不改 `Resume.status`，写 `MatchingResult.job_action`（BUG-064） |
| F-RES-21 | promote 跨用户 FK 校验 | candidate→Resume | 跨用户 promote 拒写（BUG-058） |
| F-RES-22 | surrogate boss_id | 上传无 boss_id 的 PDF | SHA256(file_bytes)[:16] 自动生成，天然去重 |

---

## 4. 岗位管理与硬筛 `/api/screening`

### 4.1 岗位 CRUD
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-JOB-01 | LLM 解析 JD | `POST /api/screening/jobs/parse-jd` | 返薪资/学历/年限/技能；LLM 失败 → 降级 fallback；`parse_success=false` 时前端提示手填 |
| F-JOB-02 | 创建岗位 | `POST /api/screening/jobs` | 201 |
| F-JOB-03 | 列表 | `GET /api/screening/jobs?active_only=` | user_id 隔离；按 created_at desc |
| F-JOB-04 | 单条 | `GET /api/screening/jobs/{id}` | 跨用户 → 403 |
| F-JOB-05 | 更新岗位 | `PATCH /api/screening/jobs/{id}` | JD 变化 → 能力模型自动重置为 `none`（防过时，BUG-011） |
| F-JOB-06 | 删除岗位 | `DELETE /api/screening/jobs/{id}` | 有未取消面试 → 409；级联清 cancelled interview / 通知 / 匹配 / 决策 |

### 4.2 硬筛
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-JOB-07 | 学历门槛 | 硬筛 | 大专=1/本科=2/硕士=3/博士=4；统一 helper（BUG-124） |
| F-JOB-08 | 院校等级 | 硬筛 | 不限/QS200/211/985 四档 |
| F-JOB-09 | 工作年限范围 | 硬筛 | min ≤ 候选 ≤ max；越界进 reject_reasons |
| F-JOB-10 | 技能必备 | 硬筛 | hard_skills.must_have=true 或 required_skills 逐项匹配；缺失进 reject_reasons |
| F-JOB-11 | 不改全局 status | 硬筛 | 仅写 MatchingResult；`Resume.status` 由 admin 控制（BUG-064） |
| F-JOB-12 | 单条手动筛选 | `POST /api/screening/jobs/{id}/screen` | 可指定 resume_ids 限定范围 |

### 4.3 评分权重
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-JOB-13 | 获取岗位权重 | `GET /api/screening/jobs/{id}/scoring-weights` | 5 维 JSON |
| F-JOB-14 | 设置岗位权重 | `PUT .../scoring-weights` | 5 项总和必须=100，否则 422 |
| F-JOB-15 | 重置为全局 | `DELETE .../scoring-weights` | scoring_weights→null |
| F-JOB-16 | 全局权重设置 | `GET/PUT /api/settings/scoring-weights` | 总和=100；需登录（BUG-041） |

### 4.4 能力模型生命周期
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-JOB-17 | LLM 抽取能力模型 | `POST /api/screening/jobs/{id}/competency/extract` | 写 draft 状态，建 HITL 任务；LLM 超时 → 降级扁平表单 |
| F-JOB-18 | 获取能力模型 | `GET .../competency` | 返 JSON + 状态 |
| F-JOB-19 | 手工填充能力模型 | `POST .../competency/manual` | 扁平 → schema |
| F-JOB-20 | 保存草稿 | `PUT .../competency/save` | status=draft，锁定 JD |
| F-JOB-21 | 批准能力模型 | `POST .../competency/approve` | draft→approved；触发后台 F2 重算 |

---

## 5. 能力模型与技能库 `/api/skills`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-SKILL-01 | 搜索/分类/分页 | `GET /api/skills?search=&category=&pending=` | 列表 |
| F-SKILL-02 | 分类列表 | `GET /api/skills/categories` | 所有 category |
| F-SKILL-03 | 单技能详情 | `GET /api/skills/{id}` | 不含 embedding |
| F-SKILL-04 | 创建 | `POST /api/skills` | 重名冲突检测 |
| F-SKILL-05 | 更新 | `PUT /api/skills/{id}` | 清缓存 |
| F-SKILL-06 | 合并 | `POST /api/skills/{id}/merge` | merge_into_id 必填；冲突保留主 |
| F-SKILL-07 | 删除 | `DELETE /api/skills/{id}` | seed 来源不可删；usage_count>0 不可删 |
| F-SKILL-08 | LLM 自动分类 | `POST /api/skills/auto-classify` | 批量分类 pending；LLM 失败 → 关键词降级 |
| F-SKILL-09 | 向量打包/解包 | 内部 | float[] ↔ float32 LE bytes |
| F-SKILL-10 | 余弦相似度 | 匹配 | 零向量返 0.0 |
| F-SKILL-11 | 最近邻检索 | F1_competency 匹配 | O(n) 遍历 |

---

## 6. AI 智能筛选 `/api/jobs/{job_id}/ai-screening`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-AISCR-01 | 候选池预览 | `GET .../preview` | 返 eligible_count + 是否已 running |
| F-AISCR-02 | 启动筛选 | `POST .../start` body=`{mode, threshold}` | mode∈{count, ratio}；threshold 越界 → 400；池空 → 422；已 running → 409；CLI 不存在 → 503 |
| F-AISCR-03 | CLI 路径锁定 | start 时 | 保存 cli_path 到 ScreeningJob，防 PATH 变化（BUG-102） |
| F-AISCR-04 | 取消任务 | `POST /api/ai-screening/{id}/cancel` | terminate 子进程 + cancel_requested=1（BUG-090） |
| F-AISCR-05 | 当前任务 | `GET .../current` | running 优先，否则最近 finished |
| F-AISCR-06 | 结果列表 | `GET .../items?sort=&limit=` | 任务未 finished → 409；按 score desc |
| F-AISCR-07 | 决赛轮 | 后台 | top (threshold + FINALIST_BUFFER=5)；标 batch_no=-1（BUG-114） |
| F-AISCR-08 | 空白 PDF 过滤 | 后台 | trim(pdf_path)!='' 才入候选池（BUG-100） |
| F-AISCR-09 | rejected 排除 | 后台 | per-job decision 已 rejected 的不再筛 |

---

## 7. F2 简历匹配 `/api/matching`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-MATCH-01 | 单对评分 | `POST /api/matching/score` body=`{resume_id, job_id}` | 5 维分数 + hard_gate_passed + 证据 |
| F-MATCH-02 | 结果列表 | `GET /api/matching/results?job_id=&tag=&page=` | SQL EXISTS 过滤死候选(abandoned/timed_out)（BUG-097） |
| F-MATCH-03 | 通过候选人 | `GET /api/matching/passed-resumes/{job_id}?action=passed\|rejected\|undecided` | 与硬筛门槛一致 |
| F-MATCH-04 | 后台重算 | `POST /api/matching/recompute?job_id=&resume_id=` | 后台任务；in-memory 状态；进程重启丢失 |
| F-MATCH-05 | 任务状态 | `GET .../recompute/status/{task_id}` | total/completed/failed/running/current；24h 自动清 stale |
| F-MATCH-06 | 决策表覆盖（旧 PATCH） | `PATCH .../results/{id}/action` | 与 decision_router 表原子化（BUG-098/106） |
| F-MATCH-07 | resume_id 翻译 | candidate.id → Resume | promote 失败 → 500；不存在 → 404（BUG-072） |
| F-MATCH-08 | hash stale 检测 | 评分时 | competency_hash / weights_hash 不匹配 → 标 stale=true |
| F-MATCH-09 | 级联清理 | 修改岗位后 | 不通过新硬筛的旧 result 自动删除 |
| F-MATCH-10 | LLM 证据生成 | 评分 | `MATCHING_EVIDENCE_LLM_ENABLED=False` 时降级启发式证据 |
| F-MATCH-11 | 标签推导 | 评分后 | derive_tags 按总分/硬门槛/缺失项打 tag |

---

## 8. F4 IM 智能接待 `/api/intake`

### 8.1 候选人 CRUD
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-INT-01 | 候选人列表 | `GET /api/intake/candidates?status=&recruit_status=&page=` | enum 校验（BUG-122） |
| F-INT-02 | 注册身份 | `POST /api/intake/candidates/register` | LLM-free 匹配岗位；幂等 |
| F-INT-03 | 单条详情 | `GET /api/intake/candidates/{id}` | 含 slots 详情 |
| F-INT-04 | 槽位手填 | `PUT /api/intake/slots/{id}` | terminal 候选不可改 → 409 |

### 8.2 主流程
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-INT-05 | 收集聊天 | `POST .../collect-chat` | LLM 抽 slot → 决策下一动作；PDF 合法性校验（BUG-A2）；terminal 返 no-op（BUG-050） |
| F-INT-06 | PDF 路径校验 | 同上 | 必须 http(s):// 或绝对路径在 storage_path 下；卡片名"简历.pdf" → 拒绝 |
| F-INT-07 | 主动放弃 | `POST .../{id}/abandon` | 标 abandoned；expire 相关 outbox；幂等 |
| F-INT-08 | 强制完成 | `POST .../{id}/force-complete` | 强 promote → Resume |
| F-INT-09 | 标超时 | `POST .../{id}/mark-timed-out` | status=timed_out；expire outbox |
| F-INT-10 | 状态修改 | `PATCH .../{id}/status` | terminal 同步 promote + intake_completed_at |
| F-INT-11 | 上次检查时间 | `PATCH .../{id}/last-checked` | 更新 last_checked_at |
| F-INT-12 | 重抽 slot | `POST .../{id}/reextract` | 对存量 chat_snapshot 重跑 SlotFiller，修补漏抽 |
| F-INT-13 | 扩展 ack-sent | `POST .../{id}/ack-sent` body=`{action_type}` | expire outbox + re-analyze；state drift → 409（BUG-052） |

### 8.3 outbox 与限流
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-INT-14 | 领取 outbox | `POST /api/intake/outbox/claim?limit=` | pending→claimed；is_running=false 返空（防关闭后重放） |
| F-INT-15 | 确认发送 | `POST /api/intake/outbox/{id}/ack` body=`{success, error}` | claimed→sent/failed；attempts++ |
| F-INT-16 | 终态时 expire | 候选转 complete/abandoned/timed_out/pending_human | 全部 pending+claimed → expired |
| F-INT-17 | 行年龄上限 | outbox 老化 | 双轨防 stale-row（owner 终态 + 行年龄） |
| F-INT-18 | 每日额度查询 | `GET /api/intake/daily-cap` | 今日使用 vs cap |
| F-INT-19 | 设置查询 | `GET /api/intake/settings` | enabled / target / current / is_running |
| F-INT-20 | 设置修改 | `PUT /api/intake/settings` | running→stop 时 bulk-expire 所有未发 outbox |

### 8.4 自扫与启动会话
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-INT-21 | 自扫排序 | `GET .../autoscan/rank?limit=` | collecting>awaiting_reply；blind extract 候选 demote（BUG-B2） |
| F-INT-22 | 自扫上报 | `POST .../autoscan/tick` | 返当日 tick 计数 |
| F-INT-23 | 启动会话深链 | `POST .../{id}/start-conversation` | URL-encode boss_id 防注入（BUG-046） |
| F-INT-24 | LLM 问询限制 | 内联 | hard_max_asks=3；ask_cooldown_hours=6；soft_max_n=3 |

---

## 9. 面试调度 `/api/scheduling`

### 9.1 面试官
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-SCH-01 | 创建面试官 | `POST /api/scheduling/interviewers` | phone 去重；至少填一项联系方式；自动反查飞书 open_id |
| F-SCH-02 | 列表 | `GET .../interviewers` | 仅本用户 |
| F-SCH-03 | 更新 | `PATCH .../interviewers/{id}` | 同样自动反查 |
| F-SCH-04 | 删除 | `DELETE .../interviewers/{id}` | 有待面试 → 409；级联清 cancelled interview + 通知 + availability |
| F-SCH-05 | 添加可用时段 | `POST .../availability` | 重复可叠加 |
| F-SCH-06 | 查可用时段 | `GET .../availability/{interviewer_id}` | 全量 |
| F-SCH-07 | 时段匹配 | `POST .../match-slots` | 排除既有面试；30min 步长 |
| F-SCH-08 | 飞书忙闲查询 | `GET .../interviewers/{id}/freebusy?days=` | 飞书日历+系统已安排 |

### 9.2 面试创建/管理
| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-SCH-09 | 创建面试 | `POST .../interviews` | resume_id 可为 IntakeCandidate.id（自动 promote）或 Resume.id；时间过去 → 400；同候选并行待面试 → 409（BUG-079）；冲突 → 409 |
| F-SCH-10 | 列表分页 | `GET .../interviews?status=&page=&size=` | 装备 resume_name/candidate_id/interviewer_name |
| F-SCH-11 | 单条 | `GET .../interviews/{id}` | enriched |
| F-SCH-12 | 更新 | `PATCH .../interviews/{id}` | 改时间触发 6 步 reschedule（建新会议→DB→取消旧会议→飞书） |
| F-SCH-13 | 取消（DELETE） | `DELETE .../interviews/{id}` | 标 cancelled，立即返；后台清外部状态 |
| F-SCH-14 | 取消（POST） | `POST .../interviews/{id}/cancel` | 同上，notes 记清理结果 |
| F-SCH-15 | 清空全部面试 | `DELETE .../interviews/clear-all` | 两阶段：DB 立删 + 异步清外部 |
| F-SCH-16 | 询问时间 | `POST .../interviews/{id}/ask-time` | 飞书消息确认；面试官无飞书 ID → 400 |
| F-SCH-17 | promote 时序 | 创建时 | 全校验通过后才 promote，避免残留 Resume（BUG-066） |
| F-SCH-18 | 改期护栏 | reschedule | 新会议失败不动 DB；旧会议取消失败仅 notes 记录 |
| F-SCH-19 | 飞书日历同步 | 创建/改期/取消 | 同步删/建 calendar_event，存 feishu_event_id |
| F-SCH-20 | 状态机 | - | created→scheduled→completed；中途可 cancelled |
| F-SCH-21 | notes 审计链 | 任何变更 | 时间戳行追加，可审计 |

---

## 10. 腾讯会议账号池 `/api/meeting`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-MEET-01 | 自动创建会议 | `POST /api/meeting/auto-create?interview_id=` | 挑空闲账号→Playwright 创建→回填 link/password/id/account；30min 取整提示 warning |
| F-MEET-02 | 全忙 | 所有账号都被占同时段 | 409 |
| F-MEET-03 | exclude_interview_id | 重建会议时 | 排除自己旧占用，防自占 |
| F-MEET-04 | 多账号配置 | `TENCENT_MEETING_ACCOUNTS=a,b,c` | 每个标签独立 Chrome profile（`data/meeting_browser_{label}/`） |
| F-MEET-05 | 首次登录扫码 | 新账号 | Playwright 弹可见浏览器，120s 等扫码 |
| F-MEET-06 | 僵尸 Chrome 清理 | 启动 | wmic 杀进程+删 lockfile |
| F-MEET-07 | "重复会议"弹窗 | 会议创建 | Escape→点不重复→DOM 强制移除 |

---

## 11. 通知系统 `/api/notification`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-NOTI-01 | 综合发送 | `POST /api/notification/send` body=`{interview_id, send_email_to_candidate, send_feishu_to_interviewer, generate_template}` | 邮件→飞书消息→日历事件→PDF 附件→生成模板 |
| F-NOTI-02 | 候选人邮件 | resume.email 非空 | 包含会议链接/密码/北京时间 |
| F-NOTI-03 | 面试官飞书消息 | interviewer.feishu_user_id 非空 | 含候选人摘要 |
| F-NOTI-04 | 飞书日历事件 | 有 feishu 配置 | 先删旧再建新，存 feishu_event_id |
| F-NOTI-05 | PDF 附件上传飞书 | resume.pdf_path 在 storage_path 下 | 上传后发文件消息；失败仅 warning |
| F-NOTI-06 | 模板生成 | `generate_template=true` | 即使发送失败也返手工复制模板 |
| F-NOTI-07 | 通知日志 | `GET /api/notification/logs?interview_id=` | 按 created_at desc |
| F-NOTI-08 | 清空全部日志 | `DELETE /api/notification/clear-all` | 仅本用户 |

---

## 12. AI 面试评价 `/api/interview-eval`

> 仅当 `INTERVIEW_EVAL_ENABLED=true` 且 `TENCENT_CLOUD_SECRET_ID` 配置时才挂载，否则路由返 404。

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-IE-01 | 启动评价任务 | `POST /api/interview-eval/start` | 5 道校验门：interview 存在/competency approved/meeting_id+account 有效/无 active 任务/并发再检（BUG-IE-001） |
| F-IE-02 | 任务详情 | `GET /api/interview-eval/{job_id}` | 含状态/error_msg/duration_sec |
| F-IE-03 | 按 interview 查 | `GET .../by-interview/{interview_id}` | 该面试最新任务 |
| F-IE-04 | 按 resume 查 | `GET .../by-resume/{resume_id}` | 该候选所有 scorecard |
| F-IE-05 | 取得 scorecard | `GET .../{job_id}/scorecard` | 评分/维度/建议/文件可用性 |
| F-IE-06 | 取得 transcript | `GET .../{job_id}/transcript` | 转录 JSON（可用时） |
| F-IE-07 | 取得 recording | `GET .../{job_id}/recording` | mp4 流式下载 |
| F-IE-08 | 取消任务 | `POST .../{job_id}/cancel` | db.expire_all() 强制重读，防 identity map 缓存（BUG-IE-002） |
| F-IE-09 | 状态机 | worker 内部 | pending→downloading→transcribing→scoring→done\|failed\|cancelled |
| F-IE-10 | 心跳自愈 | 后台 | 每次状态转移 + LLM 调用前后 bump heartbeat；threshold 默认 180s 无心跳 → failed（BUG-IE-017/018） |
| F-IE-11 | reconcile 周期 | 后台 | 默认 300s 扫陈旧任务；最低 10s |
| F-IE-12 | 启动恢复僵尸 | 启动 | 一次扫描清跨进程残留（BUG-IE-008/012） |
| F-IE-13 | LLM 重试策略 | scoring 阶段 | 临时错误重试 3 次（5xx/超时/连接）；永久错误立抛（JSON 解析/校验） |
| F-IE-14 | LLM markdown 容错 | scoring | 剥 ```...``` 包裹（BUG-IE-004） |
| F-IE-15 | LLM Config fail-fast | 启动 | api_key/base_url/model 任一缺失立即抛（BUG-IE-003） |
| F-IE-16 | 文件路径优先级 | 下载/读取 | job.recording_path 优先于硬编码（BUG-IE-013/025） |
| F-IE-17 | retention 清理 | 180 天 | 删 mp4/transcript + soft-delete 行 |
| F-IE-18 | 飞书推送 | scorecard 完成 | 推评价摘要；失败仅日志 |
| F-IE-19 | spawn 失败兜底 | 启动 worker 失败 | 标 failed + error_msg（BUG-IE-005） |
| F-IE-20 | audit 不在事务内 | 单事件失败 | 不回滚业务事务（BUG-IE-024） |

---

## 13. 飞书机器人 `/api/feishu`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-FB-01 | 事件回调 | `POST /api/feishu/event` | challenge 响应 + 消息事件处理 |
| F-FB-02 | SHA256 签名验证 | 同上 | timestamp+nonce+secret+body → SHA256；伪造拒绝（BUG-008） |
| F-FB-03 | 状态查询 | `GET /api/feishu/status` | 配置状态 |
| F-FB-04 | 多用户隔离 | 指令处理 | 按 user_id 过滤，不返全库（BUG-039） |
| F-FB-05 | WS 自动保存回复 | 面试官在飞书回复 | 按 feishu_user_id 找面试官 → 写到最新 scheduled 面试的 notes |
| F-FB-06 | 卡片回调 | "available"/"unavailable" 按钮 | 返 toast + 写 notes |

---

## 14. Boss 直聘自动化 `/api/boss`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-BOSS-01 | 自动打招呼 | `POST /api/boss/greet` | 限速 boss_max_operations_per_hour/day |
| F-BOSS-02 | 批量采集简历 | `POST /api/boss/collect` | 通过 Edge 扩展或 Playwright |
| F-BOSS-03 | 状态查询 | `GET /api/boss/status` | adapter 状态 |
| F-BOSS-04 | 多用户隔离 | 所有操作 | get_current_user_id 鉴权（BUG-042） |
| F-BOSS-05 | Playwright 反检测 | 内部 | 禁 webdriver flag + 注入伪 chrome.runtime + 人类行为模拟（随机延迟、逐字输入） |

---

## 15. HITL 审核队列 `/api/hitl`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-HITL-01 | 任务列表 | `GET /api/hitl/tasks?stage=&status=` | 分页 |
| F-HITL-02 | 单任务详情 | `GET /api/hitl/tasks/{id}` | 含 payload |
| F-HITL-03 | 批准 | `POST .../{id}/approve` | 触发 stage callback；callback 失败任务回退 pending |
| F-HITL-04 | 拒绝 | `POST .../{id}/reject` | note 必填 |
| F-HITL-05 | 修改 | `POST .../{id}/edit` | 改 payload，标 edited |
| F-HITL-06 | 状态不变性 | 已终态再操作 | InvalidHitlStateError → 409 |
| F-HITL-07 | F1 能力模型批准 hook | approve 触发 | 自动更新 jobs.competency_model |

---

## 16. 招聘评分 `/api/recruit` + 全局设置 `/api/settings`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-REC-01 | 评分并记录 | `POST /api/recruit/evaluate_and_record` | 返 hire/no_hire/hold |
| F-REC-02 | 记录打招呼 | `POST /api/recruit/record-greet` | 成功/失败 + error_msg |
| F-REC-03 | 今日额度 | `GET /api/recruit/daily-usage` | 已用/限额 |
| F-REC-04 | 改额度上限 | `PUT /api/recruit/daily-cap` | 全局设置 |
| F-SET-01 | 取全局权重 | `GET /api/settings/scoring-weights` | 5 维 |
| F-SET-02 | 改全局权重 | `PUT /api/settings/scoring-weights` | 总和=100；需登录（BUG-041） |
| F-AIE-01 | 旧评估端点 | `POST /api/ai-evaluation/evaluate` | 410 Gone，迁移提示 |
| F-AIE-02 | 旧批量评估 | `POST /api/ai-evaluation/evaluate/batch` | 410 |
| F-AIE-03 | AI provider 状态 | `GET /api/ai-evaluation/status` | enabled/configured/provider/model |

---

## 17. 数据库迁移 (alembic)

`migrations/versions/` 共 28 个版本。**测试方法**：`alembic upgrade head` 一路升级到最新，再 `alembic downgrade base` 全部降级；中间断点抽样验证 schema。

| 版本 | 功能 |
|---|---|
| 0001 | M2 baseline schema 快照 |
| 0002 | 创建 skills 表 |
| 0003 | 创建 hitl_tasks 表 |
| 0004 | 创建 audit_events 表（WORM） |
| 0005 | jobs 增加能力要求列 |
| 0006 | 初始化技能库种子数据 |
| 0007 | 创建 F2 matching_results 表 |
| 0008 | matching_results 加 job_action 列 |
| 0009 | jobs 加 scoring_weights |
| 0010 | F3 自动打招呼字段（users.daily_cap、jobs.greet_threshold、resumes.boss_id/greet_status） |
| 0011 | F4 intake_slots 表 |
| 0012 | 0012 IntakeCandidate 表 |
| 0013 | IntakeCandidate.user_id FK |
| 0014 | IntakeSlot.msg_sent_at |
| 0015 | IntakeSlot 关键时间点（ask_sent_at/answered_at/last_checked） |
| 0016 | jobs 批量采集条件 |
| 0017 | F4 outbox 表 + 过期时间 |
| 0018 | intake_user_settings 表 |
| 0019 | IntakeCandidate.last_checked |
| 0020 | IntakeCandidate.school_tier + Resume 关联 |
| 0021 | 历史 Resume → IntakeCandidate 回填 |
| 0022 | IntakeCandidate 决策字段 |
| 0023 | candidate ↔ resume 1:1 约束 |
| 0024 | job_candidate_decisions 新表（从 matching_results 回填） |
| 0025 | ai_screening 相关表 |
| 0026 | chaos round 8 修复 |
| 0027 | interview_eval 表 |
| 0028 | interview_eval.last_heartbeat |

---

## 18. 运维脚本 `scripts/`

| 编号 | 功能 | 命令 | 预期 |
|---|---|---|---|
| F-OPS-01 | 清理无效 PDF 路径 | `python scripts/cleanup_invalid_pdf_paths.py [--apply]` | dry-run 默认；--apply 自动备份 DB；状态回滚 |
| F-OPS-02 | 重抽 intake slot | `python scripts/reextract_intake_slots.py` | 历史候选人 chat_snapshot 重跑 SlotFiller |
| F-OPS-03 | seed 39 候选人 | `python scripts/seed_40_candidates.py` | 创建演示数据；输出 seed_map.json |
| F-OPS-04 | 验证 embedding API | `python -m scripts.verify_embedding_api` | 验智谱 /v1/embeddings；查向量维度 |
| F-OPS-05 | 检查 0024 回填差额 | `python scripts/check_decision_backfill_gap.py` | dry-run；列孤儿候选人 |
| F-OPS-06 | 快速 DB 检查 | `python check_db.py` | 列表 + users |
| F-OPS-07 | 生成测试 JWT | `python gen_token.py` | 输出 30 天 token |
| F-OPS-08 | 学历筛选策略 e2e | `python test_school_only.py` | 211/985/普通三场景 |

---

## 19. 启动与打包

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-RUN-01 | launcher.py 双击启动 | 双击 exe | 检端口 8000；启 uvicorn；2.5s 后开浏览器 |
| F-RUN-02 | 首次复制 .env | 启动且 .env 缺失 | 从 default.env 复制 |
| F-RUN-03 | 端口占用退出 | 8000 已占 | 报错退出 |
| F-RUN-04 | dev.bat 开发模式 | `dev.bat` | 杀旧进程→alembic upgrade→uvicorn:8000+vite:3000→打开浏览器 |
| F-RUN-05 | dev.bat 健康探针 | dev 启动 | 30s 内等 backend /openapi.json + frontend LISTENING |
| F-RUN-06 | build.py EXE 打包 | `python build.py` | 前端 build→PyInstaller→`dist/招聘助手.exe` |
| F-RUN-07 | build_release.py 完整发布 | `python build_release.py` | 含 Chromium + edge_extension/ → `招聘助手-v1.0-Windows.zip` |
| F-RUN-08 | 招聘助手.spec 存档 | - | 旧 PyInstaller 配置，已被 build_release.py 替代 |

---

## 20. Edge 浏览器扩展 `edge_extension/`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-EXT-01 | 加载扩展 | edge://extensions → 开发者模式 → 加载 edge_extension/ | 扩展图标出现 |
| F-EXT-02 | manifest V3 权限 | - | activeTab/storage/downloads/alarms/tabs；host: zhipin.com + 127.0.0.1:* |
| F-EXT-03 | 服务器连接 | popup → 设置 | 输入服务器 URL，登录后保 token 到 localStorage |
| F-EXT-04 | 上下文条 | 打开 BOSS 不同页面 | 自动识别 recommend / chat / list / detail |
| F-EXT-05 | F3 自动打招呼 | 推荐页 → 选岗位 → 开始 | 限日配额（默认 1000）；阈值默认 60 分；自动检测风控 |
| F-EXT-06 | F4 单聊采集 | 聊天页 → 自动扫描开关 | toggle 写 chrome.storage.local.intake_enabled |
| F-EXT-07 | Step1 候选人扫描 | 每 60min（alarm） | 扫 BOSS 列表→注册新候选人；互斥锁 30min 自动清 |
| F-EXT-08 | Step2 聊天分析 | 每 180min（alarm） | 逐个开聊天→LLM 分析→发提问 |
| F-EXT-09 | 手动 Step1/Step2 | popup 按钮 | 立即触发，不等 alarm |
| F-EXT-10 | 紧急停止 | popup → intake_force_reset | 清锁 + 停 alarm |
| F-EXT-11 | 列表批量求简历 | 列表页 → 批量按钮 | 调用 click_request_resume 接口 |
| F-EXT-12 | 列表批量采集 | 同上 | 抓字段→注册到后端 |
| F-EXT-13 | 风控检测 | 自动 | captcha-wrap / verify-dialog / 文案"操作过于频繁" 触发告警 |
| F-EXT-14 | 付费墙检测 | 自动 | pay-dialog / upgrade-dialog 阻止操作 |

> ⚠️ **QA 重点**：BOSS 改版后必须验证 `f3_selectors.js` 中所有 CSS 选择器是否仍匹配（推荐页 iframe、卡片字段、打招呼按钮、风控对话框）。

---

## 21. 前端 Vue 应用

> 路由表（`frontend/src/router/index.js`）：所有非 `/login` 路由需登录；401 → 自动跳转登录。

### 21.1 Login `/login`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-LOGIN-01 | 自动检测系统初始化 | 无用户时显示注册；有用户时显示登录 |
| F-UI-LOGIN-02 | 用户名/密码登录 | 必填；密码 ≥ 6 |
| F-UI-LOGIN-03 | 注册 | 两次密码一致；可填显示名 |
| F-UI-LOGIN-04 | Enter 提交 | 焦点在输入框时按 Enter |
| F-UI-LOGIN-05 | 错误消息 | 红字提示 |
| F-UI-LOGIN-06 | 成功硬刷新 | 加载新 token |

### 21.2 Dashboard `/`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-DASH-01 | 4 张统计卡片 | 总简历/已通过/已淘汰/待面试 |
| F-UI-DASH-02 | 系统状态卡 | 飞书/AI/邮箱/腾讯会议 已配置/未配置 |
| F-UI-DASH-03 | 6 步快速开始 | 配置→面试官→岗位→扩展→筛选→面试 |
| F-UI-DASH-04 | 可点击跳转 | 点统计卡跳详情页 |

### 21.3 Resumes `/resumes`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-RES-01 | 搜索条 | keyword + status 筛选 |
| F-UI-RES-02 | 上传 PDF | 单文件；上传后入库 |
| F-UI-RES-03 | 启动内容解析 | 触发 ai-parse-all |
| F-UI-RES-04 | 清空全部 | 二次确认输入"确认清空" |
| F-UI-RES-05 | 紧凑列表行 | 展开后进入详情卡 |
| F-UI-RES-06 | 详情字段 | 姓名/求职意向/手机/邮箱/学历/年限/学校 |
| F-UI-RES-07 | 二维码 | 加载失败显示重试 |
| F-UI-RES-08 | 状态按钮 | 通过/淘汰 |
| F-UI-RES-09 | 查看 PDF | fetch + token，blob URL，60s revoke |
| F-UI-RES-10 | AI 评分（单条） | 后台轮询 |
| F-UI-RES-11 | 删除 | 二次确认 |
| F-UI-RES-12 | AI 面评弹窗 | 总评+技能+项目+自评+原文 |
| F-UI-RES-13 | 对接岗位分数表 | 显示 5 维分数 |
| F-UI-RES-14 | 手机/邮箱校验 | 11 位中国号、标准邮箱 |

### 21.4 Jobs `/jobs`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-JOB-01 | 表格列 | 名称/部门/学历/年限/技能/能力模型状态/启用 |
| F-UI-JOB-02 | 能力模型状态标签 | 未生成/待审/已生效/已驳回 + 抽取中 spinner |
| F-UI-JOB-03 | 新建对话框 - 解析 JD | 粘贴→点击解析→自动填表 |
| F-UI-JOB-04 | 基本信息表单 | 必填校验；薪资/年限范围合法 |
| F-UI-JOB-05 | 能力模型 Tab | CompetencyEditor 组件（见 21.13） |
| F-UI-JOB-06 | 匹配候选人 Tab | 排序 passed→null→rejected；通过/淘汰/改 按钮 |
| F-UI-JOB-07 | 五维筛选 Tab | 警告：先发布能力模型；进度条 |
| F-UI-JOB-08 | AI 智能筛选 Tab | AiScreeningPanel（见 21.15） |
| F-UI-JOB-09 | 权重总和=100 | 否则保存禁用 |
| F-UI-JOB-10 | 删除岗位 | 有面试 → 弹窗提示 |

### 21.5 HitlQueue `/hitl`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-HITL-01 | 类型/状态筛选 | 能力模型/新技能 × 待审/已通过/已驳回 |
| F-UI-HITL-02 | 一键自动分类 | 仅有 pending 技能时显示；按钮 loading |
| F-UI-HITL-03 | 能力模型审核跳转 | → /jobs?id=&tab=competency |
| F-UI-HITL-04 | 技能归类弹窗 | 必选分类 |

### 21.6 Intake `/intake`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-INT-01 | 自动化总开关 | 启动/暂停 |
| F-UI-INT-02 | 目标候选人数 | 0-1000 |
| F-UI-INT-03 | 进度条 | complete/target |
| F-UI-INT-04 | 每日额度 | used/cap，剩余 |
| F-UI-INT-05 | 列表筛选 | 状态下拉 + 姓名/Boss ID 搜 |
| F-UI-INT-06 | 行内状态下拉改 | PATCH .../status |
| F-UI-INT-07 | 操作按钮 | 开始沟通/重抽/标完成/放弃/删除 |
| F-UI-INT-08 | 展开行 SlotsPanel | 见 21.17 |

### 21.7 SkillLibrary `/skills`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-SKL-01 | 搜索+分类筛选+仅待归类 | 按需过滤 |
| F-UI-SKL-02 | 新增/编辑技能 | 名称+分类必填 |
| F-UI-SKL-03 | 合并 | SkillPicker 选目标 |
| F-UI-SKL-04 | 批量分类 | 选中行后批量改 |
| F-UI-SKL-05 | 删除限制 | seed 来源/usage>0 不可删 |

### 21.8 Interviewers `/interviewers`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-IVR-01 | 表格 | 姓名/部门/手机/邮箱/飞书 ID |
| F-UI-IVR-02 | 新建/编辑表单 | 三项至少填一；手机 11 位；邮箱合法 |
| F-UI-IVR-03 | 飞书 ID 自动反查 | 留空时由后端按手机/邮箱反查 |
| F-UI-IVR-04 | 删除 | 有待面试 → 409 友好提示 |

### 21.9 Interviews `/interviews`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-INV-01 | 列表卡片按状态分组 | scheduled/completed/cancelled |
| F-UI-INV-02 | 卡片头部 | 候选人 + 状态标签 + 编辑/删除 |
| F-UI-INV-03 | 候选人 2x2 信息块 | 学校/学历/手机/邮箱 |
| F-UI-INV-04 | 操作组（scheduled） | 创建/重建腾讯会议 + 复制邀请 + 发通知 + AI 面评 + 取消 |
| F-UI-INV-05 | 操作组（completed） | 仅 AI 面评 |
| F-UI-INV-06 | 新建对话框 - 岗位下拉 | filterable |
| F-UI-INV-07 | 候选人下拉 | 基于岗位通过状态 |
| F-UI-INV-08 | 面试官下拉 | filterable |
| F-UI-INV-09 | 面试官 5 天日历 | 拖拽选时间范围 |
| F-UI-INV-10 | 清空全部面试 | 二次确认 |

### 21.10 Notifications `/notifications`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-NOT-01 | 表格 | 接收人/类型/渠道/主题/状态/时间 |
| F-UI-NOT-02 | 状态 tag 颜色 | sent=绿/failed=红/generated=灰 |
| F-UI-NOT-03 | 查看弹窗 | pre 格式展示 |
| F-UI-NOT-04 | 清空全部 | 输入"确认清空" |
| F-UI-NOT-05 | 分页 | 20/页 |

### 21.11 Settings `/settings`
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-SET-01 | AI 配置 Tab | 状态+模型+检测按钮 |
| F-UI-SET-02 | 评分权重 Tab | 5 维输入+进度条+总和=100 |
| F-UI-SET-03 | 保存按钮 | 总和≠100 时禁用 |
| F-UI-SET-04 | 恢复默认 | 重置 5 维 |
| F-UI-SET-05 | Boss 直聘 Tab | 适配器状态+今日操作次数 |
| F-UI-SET-06 | 飞书 Tab | 连接状态+检测按钮 |

### 21.12 SlotsPanel（Intake 展开）
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-SLT-01 | 硬性信息表 | 字段/原话+时间戳/来源/操作 |
| F-UI-SLT-02 | PDF 简历区 | 已收到/未收到 + 询问次数 |
| F-UI-SLT-03 | 软性问答表 | 问题/回答/来源/计数 |
| F-UI-SLT-04 | 槽位手填 | terminal 候选不可改（提示 409） |

### 21.13 CompetencyEditor 组件
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-CMP-01 | 状态徽章 | 待审/已通过/已驳回/未生成 |
| F-UI-CMP-02 | JD 折叠区 | 编辑/查看切换 |
| F-UI-CMP-03 | 统计卡 | 硬技能数/软素质数/年经验/最低学历 |
| F-UI-CMP-04 | 硬技能网格 | 等级 + 必须标记 |
| F-UI-CMP-05 | 保存草稿 | status=draft |
| F-UI-CMP-06 | 通过发布 | draft→approved |

### 21.14 SkillPicker 组件
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-PCK-01 | 自动完成 | 下拉搜索；选中触发 select 事件 |

### 21.15 AiScreeningPanel 组件
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-AISP-01 | Idle 状态 | 候选池规模 + 模式 + 阈值 |
| F-UI-AISP-02 | Running 状态 | 进度条 + 取消按钮 |
| F-UI-AISP-03 | Done 状态 | 完成数 + ItemsTable + 重新筛选 |
| F-UI-AISP-04 | Failed/Cancelled | 警告或消息 |

### 21.16 AiScreeningItemsTable / ResumeAiEvaluationsList / AiInterviewEvalPanel
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-ITB-01 | items 列表 | 决策按钮（可选显示） |
| F-UI-AEL-01 | 简历的所有面评 | 跳转到 /interviews |
| F-UI-AEP-01 | 面试 AI 评价弹窗 | 维度/总评/建议 |

### 21.17 全局 UI 行为
| 编号 | 功能 | 验证点 |
|---|---|---|
| F-UI-GLB-01 | axios 401 拦截 | 清 token，跳 /login |
| F-UI-GLB-02 | QR/PDF token 注入 | Authorization header；blob URL；60s revoke |
| F-UI-GLB-03 | 长轮询超时 | 3-5 分钟无进展自动停止 |
| F-UI-GLB-04 | 高危确认弹窗 | 删除/淘汰/清空需二次 |
| F-UI-GLB-05 | extractingJobs store | 跨页面持久化抽取状态 |
| F-UI-GLB-06 | hitlState store | 待审数 + 自动分类状态 |

---

## 22. TeamAgent 自学习系统 `.teamagent/`

| 编号 | 功能 | 触发 | 预期 |
|---|---|---|---|
| F-TA-01 | pre-commit hook 检查 | `git commit` | 执行 `teamagent m5-bootstrap --check`；CLI 缺失仅警告不阻塞；`TEAMAGENT_BOOTSTRAP_SKIP=1` 跳过 |
| F-TA-02 | post-merge hook 同步 | `git pull` | 执行 `teamagent m5-sync --apply`；失败不阻塞 |
| F-TA-03 | 增量扫描 | Stop 事件每 turn | scan-cursor.json turn 索引推进；last-harvest.md 新增条目 |
| F-TA-04 | 全量重扫 | `/clear`、`/compact`、退出、关窗 | 完整重扫保证一致性 |
| F-TA-05 | 规则编译到 CLAUDE.md | `teamagent compile` | `<!-- TEAMAGENT:START -->` 段更新；3000 token 预算；超出脚注提示 |
| F-TA-06 | 知识库存储 | 自动 | knowledge.db (SQLite, WAL)；规则去重 |
| F-TA-07 | 团队 shared-claude.md | 自动合并 | git 拉取后注入个人 CLAUDE.md |
| F-TA-08 | hook 注册 | `.claude/settings.local.json` | Stop / UserPromptSubmit 必须显式注册才生效 |
| F-TA-09 | /checkteamagent skill | 健康检查 | 输出版本/hook 状态/规则数/团队规则可用性 |

---

## 附录 A. 历史 BUG 回归清单

测试时务必覆盖以下已修复的 BUG（按编号）：

### Auth/Resume/Job
- BUG-006/056/072/097/098/106 — F2 决策一致性、跨用户 404、resume 翻译
- BUG-008 — 飞书签名验证
- BUG-009 — 登录速率限制
- BUG-010 — 公开注册锁
- BUG-011 — JD 更新自动重置能力模型
- BUG-015 — `/auth/me` 修复
- BUG-018/066/076/079 — 面试调度并发与 promote 时序
- BUG-039/041/042 — 多用户隔离（飞书/设置/Boss）
- BUG-046 — start-conversation URL 注入防御
- BUG-050 — terminal 候选 LLM 返 no-op
- BUG-052 — outbox state drift 409
- BUG-057 — auto promote 触发条件
- BUG-058 — 跨用户 FK 拒写
- BUG-062 — clear-all 级联完整
- BUG-064 — screening 不改全局 status
- BUG-082 — keyword 限长 64
- BUG-084 — 路径穿越校验
- BUG-089/141 — 启动时清僵尸 ScreeningJob
- BUG-090 — AI 筛选取消子进程 terminate
- BUG-100 — 空白 PDF 过滤
- BUG-102 — CLI 路径锁定
- BUG-114 — 决赛轮 batch_no=-1
- BUG-122 — intake 状态 enum 校验
- BUG-124 — 学历门槛统一 helper
- BUG-148 — total 返权威值
- BUG-150 — `/api/*` 404 JSON
- BUG-151 — JWT 中间件白名单可选 token
- BUG-A2 — PDF 路径合法性
- BUG-B2 — blind extract 候选 demote

### Interview-Eval（chaos round 11/12）
- IE-001 并发竞争防御
- IE-002 cancel 缓存强制重读
- IE-003 LLM Config fail-fast
- IE-004 markdown 容错
- IE-005 spawn 失败兜底
- IE-008/012/014/015 跨进程僵尸自愈
- IE-013/025 文件路径优先级
- IE-014 临时/永久错误 + 3 次重试
- IE-016~027 chaos round 12 共 12 处（4 High + 6 Medium + 2 Low）
- IE-017/018 心跳自愈 + threshold 防御最低值
- IE-020 cancel_requested 不误标"中断"
- IE-024 audit 不在事务内

---

## 附录 B. 完整环境变量配置项

> 所有变量在 `.env` 中配置；缺失时模块自动降级。

### 应用
- `APP_NAME` (默认 "招聘助手")
- `APP_HOST` (默认 127.0.0.1)
- `APP_PORT` (默认 8000)
- `DEBUG` (默认 False)

### 数据库
- `DATABASE_URL` (默认 sqlite:///./data/recruitment.db)

### AI
- `AI_ENABLED` (默认 False)
- `AI_PROVIDER` (默认 openai_compatible)
- `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL`
- `AI_MODEL_COMPETENCY` / `AI_MODEL_INTAKE` (空则回退 AI_MODEL)

### F2 匹配
- `MATCHING_ENABLED` / `MATCHING_EVIDENCE_LLM_ENABLED`
- `MATCHING_TRIGGER_DAYS_BACK` (默认 90)
- `MATCHING_SKILL_SIM_EXACT` / `MATCHING_SKILL_SIM_EDGE` / `MATCHING_INDUSTRY_SIM`

### 邮件
- SMTP: `SMTP_HOST` / `SMTP_PORT` (465) / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_USE_SSL`
- IMAP: `IMAP_HOST` / `IMAP_PORT` (993) / `IMAP_USER` / `IMAP_PASSWORD` / `IMAP_CHECK_INTERVAL` (300)

### 飞书
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
- `FEISHU_NOTIFY_TRIGGER_HR`

### 腾讯会议
- `TENCENT_MEETING_ACCOUNTS` (逗号分隔标签)

### F-interview-eval
- `INTERVIEW_EVAL_ENABLED`
- `TENCENT_CLOUD_SECRET_ID` / `TENCENT_CLOUD_SECRET_KEY` / `TENCENT_CLOUD_ASR_REGION`
- `INTERVIEW_EVAL_RECORDING_RETENTION_DAYS` (180)
- `INTERVIEW_EVAL_HEARTBEAT_INTERVAL_SECONDS` (30，≥5)
- `INTERVIEW_EVAL_STALE_THRESHOLD_SECONDS` (180，≥10)
- `INTERVIEW_EVAL_RECONCILE_PERIOD_SECONDS` (300，≥10)

### Boss
- `BOSS_ADAPTER` (默认 edge_extension)
- `BOSS_MAX_OPERATIONS_PER_HOUR` (30) / `BOSS_MAX_OPERATIONS_PER_DAY` (200)
- `BOSS_DELAY_MIN` (3.0) / `BOSS_DELAY_MAX` (8.0)
- `BOSS_CHAT_URL_TEMPLATE`

### F3 Boss 自动打招呼
- `F3_DEFAULT_GREET_THRESHOLD` (60)
- `F3_DEFAULT_DAILY_CAP` (1000)
- `F3_AI_PARSE_ENABLED`

### F4 IM 接待
- `F4_HARD_MAX_ASKS` (3)
- `F4_PDF_TIMEOUT_HOURS` (72)
- `F4_ASK_COOLDOWN_HOURS` (24)
- `F4_SOFT_QUESTION_MAX` (3)
- `F4_DAILY_CAP` (200)
- `F4_SCHEDULER_ENABLED` / `F4_SCHEDULER_INTERVAL_SEC` (300)
- `F4_EXPIRES_DAYS` (14)
- `F4_CLAIM_STALE_MINUTES` (10)
- `F4_OUTBOX_MAX_AGE_HOURS` (24)
- `F4_MAX_CHAT_MESSAGES` (500)

### 认证
- `JWT_SECRET` ⚠️ **生产必须改**
- `CORS_ORIGINS` (逗号分隔)
- `AGENTICHR_TEST_BYPASS_AUTH` (仅测试)

### 文件
- `RESUME_STORAGE_PATH` (默认 ./data/resumes)

---

## 文档维护

- 新增功能 → 增加新条目，编号顺延
- 已弃用功能 → 标 `[DEPRECATED]`，保留历史记录
- 修复 BUG → 更新对应条目并在附录 A 加编号
- 任何 schema 变更 → 同步更新章节 17
