# 后台运行诊断指南

**Date:** 2026-05-22
**Status:** Research — 等用户数据
**Author:** liboze + Claude

## 这是干什么的

用户反馈："离开 BOSS 标签页后自动化就停了"。要修这个 bug，我们要先**测量**究竟是什么停了——
是 timer 被节流？是输入失效？还是页面冻结？

本诊断器跑 90s 采样，输出一份硬数据，用来决定接下来的修复方案。

**零副作用**：只采样浏览器行为 + 用屏幕外合成 contenteditable 测试输入，
不会触碰 BOSS 真实输入框、不会发任何消息。

## 怎么跑

1. 在 Edge 里**重新加载扩展**（`edge://extensions/` → 重新加载招聘助手）
2. 打开 **BOSS 直聘** （`https://www.zhipin.com/web/chat/index` 任意页都行）
3. 按 **F12** 打开 DevTools，切到 **Console** 标签
4. 在 Console 输入并回车：
   ```js
   await window.intake_bgDiagnostic()
   ```
5. 看到右下角 toast "🔬 后台诊断启动 90s..." → **立即切到其他标签页**
   （例如切到一个空白新 tab，或刷邮箱、刷网页都行）
6. 等大约 90s
7. 切回 BOSS 标签页，DevTools Console 会出现"诊断完成"toast 和汇总日志

## 看什么结果

Console 最后会打印 `[bg-diag] ====== 汇总 ======` 两行：

```
[bg-diag] 前台样本汇总: { count: 5, avg30msActual: 35, avg300msActual: 305, ... }
[bg-diag] 后台样本汇总: { count: 40, avg30msActual: 1003, avg300msActual: 1004, ... }
```

**关键看四件事**：

| 指标 | 前台预期 | 后台 = bug 实锤 |
|---|---|---|
| `avg30msActual` | ~30-40 ms | 如果变 ≥ 1000ms → **timer 被节流 33×** |
| `avg300msActual` | ~300-310 ms | 如果变 ≥ 1000ms → 同上 |
| `execAlwaysProduced: true` | true | 如果后台变 false → **execCommand 在后台失效** |
| `dispatchAlwaysProduced: true` | true | 如果后台变 false → **dispatchEvent 也失效** |

把整段 Console 截图发给我即可，我会基于数据设计具体方案。

## 我等你的数据来做什么

根据 4 个指标的真实组合，可能的修复路径：

| 数据画面 | 方案 |
|---|---|
| timer 后台变 1s，dispatch 仍工作 | dispatchEvent 路径 + Audio 保活足够 |
| timer 后台变 1s，dispatch 也失败 | 必须短暂激活 tab |
| timer 没变，但 exec 失败 | 只需切换输入方法 |
| 全部都失败 | 桌面 playwright 替代方案 |

## 完整样本数据存在哪

```js
// Console 里可以这样取出全部样本：
(await chrome.storage.local.get('bg_diag_last')).bg_diag_last
```

也可以复制粘贴给我做更细的分析。
