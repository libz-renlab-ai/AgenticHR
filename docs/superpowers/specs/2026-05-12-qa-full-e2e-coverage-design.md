# QA 全覆盖 E2E 自动化运动 — 设计文档

**日期**: 2026-05-12
**触发**: 用户要求对 `docs/QA-系统功能清单-v1.md` 的全部 300+ 功能项产出可重放 E2E 测试 + 成功截图证据,失败循环修复直至某一轮全过
**授权范围**: 用户明确放开 `core/` 编辑、循环轮次不限、AI/腾讯/飞书/Boss 真集成都跑

---

## 1. 目标与非目标

### 1.1 目标
1. 为 `docs/QA-系统功能清单-v1.md` 的每条 `F-XXX-NN` 编写可独立运行的自动化测试
2. UI 类测试产生可视化截图 → 由独立 `claude -p` 实例(haiku-4-5)做"看图判定"
3. 失败项写入 `BUGS-qa-round-N.md` 并自动修复(被测代码亦可改,含 `core/`)
4. 循环执行直到某一轮 0 失败
5. 每轮产出 HTML 报告 + 失败截图 + 修复 commit 链供 PM 复盘

### 1.2 非目标
- 不替代项目原有 `tests/` 单元/集成测试;此次新建 `tests/qa_full/` 平行套件
- 不做性能/压力基准(本运动只验证功能正确性)
- 不做安全渗透测试(范围另议)
- 不写永久 CI 接入;此运动结束后是否常态化由 PM 决定

---

## 2. 技术栈选型

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 API 测试 | pytest + httpx | 已有 `tests/integration/test_f2_*.py` 30+ 文件先例 |
| 前端 UI 测试 | Playwright (Python sync API) | Chromium 内置中文字体;`page.screenshot()` 一行截图;比 selenium 稳 |
| Edge 扩展测试 | Playwright + chrome.devtools 协议 | 直接注入 content script,绕过 popup,可重放 |
| DB 迁移测试 | alembic + sqlalchemy reflect | upgrade head→downgrade base→upgrade head 三段式 |
| LLM/ASR mock | pytest-vcr 录制+回放 | 第一轮真实调用录制磁带,后续轮回放节省额度 |
| 截图判定 | `claude -p --model haiku-4-5` | 用户指定;输入截图 + 期望清单,输出 JSON |
| 报告 | jinja2 + 自定义模板 | 输出 `artifacts/round-N/report.html` |

---

## 3. 目录结构

```
tests/qa_full/
├── conftest.py                  # 全局 fixtures
├── pytest.ini                   # markers, paths
├── runners/
│   ├── run_all.py               # 主入口
│   ├── verifier.py              # claude -p 包装
│   ├── report.py                # HTML 报告生成
│   └── budget_guard.py          # 监控 token/ASR/会议额度消耗
├── fixtures/
│   ├── db.py                    # 独立 test DB (data/qa_test_<round>.db)
│   ├── auth.py                  # JWT token 生成
│   ├── feishu_recordings/       # 飞书 API 录像 (vcr)
│   ├── llm_recordings/          # LLM 调用录像
│   └── sample_data/             # PDF, 录音, 聊天记录样本
├── backend/
│   ├── test_F_INFRA_*.py        # 1 章
│   ├── test_F_AUTH_*.py         # 2 章
│   ├── test_F_RES_*.py          # 3 章
│   └── ... (每章一个或多个文件)
├── frontend/
│   ├── test_F_UI_LOGIN_*.py
│   ├── test_F_UI_DASH_*.py
│   └── ... (按页面拆)
├── extension/                   # Edge 扩展测试
├── migrations/                  # alembic 双向
├── scripts_runtime/             # 18 章运维脚本(命名避开根目录的 scripts/)
├── teamagent/                   # 22 章
└── external/                    # 真实集成 (飞书/腾讯/AI/Boss)

artifacts/round-<N>/
├── report.html                  # 总报告
├── report.json                  # 机器可读
├── screenshots/F-UI-XXX-NN.png  # 截图
├── responses/F-XXX-NN.json      # API 响应快照
├── verifier_calls/              # claude -p 输入输出留底
└── logs/                        # uvicorn / playwright / extension 日志

BUGS-qa-round-<N>.md             # 本轮失败 + 修复链
```

---

## 4. 测试运行流程

```
┌────────────────────────────────────────────────────────────┐
│  Round N start                                             │
└─┬──────────────────────────────────────────────────────────┘
  │
  ▼
┌────────────────────────────────────────────────────────────┐
│  Pre-flight                                                │
│  - 停旧 uvicorn / vite                                      │
│  - 删 data/qa_test_<N>.db                                   │
│  - alembic upgrade head                                     │
│  - 起 uvicorn (PORT=8765, env=qa_test)                     │
│  - 起 vite (PORT=5174)                                     │
│  - playwright install chromium (首次)                      │
└─┬──────────────────────────────────────────────────────────┘
  │
  ▼
┌────────────────────────────────────────────────────────────┐
│  pytest tests/qa_full/ -n auto --json-report               │
│  ↓ 每个 test 函数产: 截图 / JSON 响应 / 日志              │
└─┬──────────────────────────────────────────────────────────┘
  │
  ▼
┌────────────────────────────────────────────────────────────┐
│  Verifier pass (仅对 has_screenshot=True 的测试)            │
│  for each screenshot:                                      │
│    claude -p --model haiku-4-5 <prompt + 截图 path>        │
│    解析 JSON 结果, 写入 verifier_calls/                    │
└─┬──────────────────────────────────────────────────────────┘
  │
  ▼
┌────────────────────────────────────────────────────────────┐
│  Aggregate                                                 │
│  - 失败 → BUGS-qa-round-N.md                               │
│  - 全过 → exit 0, 运动结束                                 │
└─┬──────────────────────────────────────────────────────────┘
  │ 有失败
  ▼
┌────────────────────────────────────────────────────────────┐
│  Auto-fix loop                                             │
│  for each bug:                                             │
│    - 定位 root cause (查代码 + 日志 + 错误堆栈)            │
│    - 改 code (app/, frontend/, core/, etc.)                │
│    - 单独 commit "fix(qa-round-N): F-XXX-NN <短描述>"      │
│  - 提交 BUGS-qa-round-N.md 收尾                            │
└─┬──────────────────────────────────────────────────────────┘
  │
  ▼ go to Round N+1
```

---

## 5. claude -p 验证器协议

### 5.1 输入
```
prompt = """
你是 QA 截图验证员。判断这张截图是否符合期望。

测试编号: F-UI-RES-09
功能描述: 查看 PDF (fetch + token, blob URL, 60s revoke)
期望可见元素:
- 浏览器新 tab 打开 PDF
- PDF 内容渲染(非空白)
- URL 是 blob: 协议
失败示例:
- 401/403 错误页
- "无法预览" 提示
- 空白页

仅输出 JSON: {"passed": true|false, "reason": "<一句话>", "anomalies": ["..."]}
"""
+ image attachment: artifacts/round-N/screenshots/F-UI-RES-09.png
```

### 5.2 调用
```bash
claude -p "$prompt" --model haiku-4-5 --image $screenshot_path --output-format json
```

### 5.3 解析
- `passed=true` → 测试通过
- `passed=false` → 测试失败,`reason` 进 BUGS-qa-round-N.md
- claude -p 自身报错 → 标 `verifier_error`, 不算业务失败,改用纯 assertion 重判

### 5.4 不适用 verifier 的场景
- 后端 API 测试(`assert response.status_code == 200` 已确定)
- DB schema 测试(reflect 后比对字段)
- 后台任务测试(检查日志行/DB 行)

---

## 6. 外部依赖处理

| 系统 | 真集成 | mock/限位 |
|---|---|---|
| 飞书 | 第一轮真实调用,vcr 录制后续回放 | 测试帐号专用消息接收人 ID 在 `.env.qa` |
| 腾讯会议 | 真实创建,但每轮限 ≤3 场,主题前缀 `[QA-TEST]` | 跑完自动 cancel |
| 腾讯云 ASR | 真实但限位 ≤1 分钟录音 × 5 次/轮 | 总额度 10 小时,够 ~120 轮 |
| AI LLM | 真实调用,vcr 录制 | 每轮 token 上限 200K |
| Boss 直聘 | **不在自动循环里**,手动 round 单独跑 | 风控触发立即停 + 通知 PM |

`tests/qa_full/runners/budget_guard.py` 实时监控,超额抛 `BudgetExceeded`。

---

## 7. 关键风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| 循环不收敛(改了又坏) | 高 | 每修一项独立 commit;某项连续 3 轮失败 → 标 `BLOCKED` 不再自动修 |
| 改 core/ 引入 IE-001~027 回归 | 中 | 修 core/ 前先跑 `tests/integration/test_*.py` 已有套件作 baseline |
| Boss 封号 | 中 | 不进自动循环,手动单独跑 |
| ASR 额度耗尽 | 低 | budget_guard 在 80% 额度时降级到回放 |
| 飞书 API 限流 | 低 | vcr 缓存 + 限位 5 次/分钟 |
| Playwright 中文字体缺失 | 低 | 安装 `playwright install --with-deps chromium` |
| 截图判定误报(haiku 看错) | 中 | 失败截图额外存 raw bytes,人工抽检 |

---

## 8. 收敛条件

**正常退出**(运动成功): Round N 跑完,失败数 = 0 → 生成 `final-report.html` + git tag `qa-pass-2026-05-12`

**异常退出**(需要 PM 介入):
- 某项连续 3 轮失败,标 BLOCKED,运动停在该轮,生成 `blocked-items.md`
- ASR/会议账号额度耗尽,运动暂停
- 用户终止(Ctrl-C)

---

## 9. 第 0 轮准备清单

- [ ] 确认 `claude -p --model haiku-4-5` 可用
- [ ] 确认 Playwright + Chromium 已安装
- [ ] 创建 `tests/qa_full/` 框架
- [ ] 写 `conftest.py` + 几个核心 fixtures
- [ ] 跑一个 smoke test (F-INFRA-10 health check) 验证管线
- [ ] 上 git tag `before-qa-campaign-2026-05-12` 作回滚锚点

---

## 10. 后续迭代

本运动只覆盖 v1 清单。若清单本身漏了功能(后端新增、前端新增):
- 测试运行时 `pytest --collect-only` 与清单对比,缺失的测试 → 进 `MISSING-tests.md`
- 由 PM 决定是否补测试再下一轮

---

**已定决策**:
- 范围: 全部 300+ 项,真集成都跑
- 凭证: 飞书 + AI + 腾讯会议 + 腾讯云 ASR + Boss 直聘 全部已配
- 修复: 全代码自修,含 core/(本运动专属授权)
- 验证: claude -p haiku-4-5
- 轮次: 不限,直到全过
- 评审: 跳过文档评审,直接进入实现计划
