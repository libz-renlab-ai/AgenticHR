// main_world_bridge.js — runs in MAIN world so it can access page-side
// Vue component internals (e.g. el.__vue__) which are invisible from the
// extension's isolated content-script world.
// Responds to postMessage requests from content.js.

(function () {
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data?.__intakeBridge) return;
    const { cmd, id, bossId } = e.data;
    const ulVue = document.querySelector(".user-list")?.__vue__;

    if (cmd === "get_datasources") {
      // Scroll virtual list to bottom repeatedly to trigger lazy-load of all pages.
      //
      // 2026-05-13 incident: 旧实现在第一次 "tick 内 dataSources 没增长" 就 break,
      // 实际 boss 懒加载有时单 tick 700ms 不够 (网络抖动/服务端节流),早退会让收
      // 集卡在 ~100 人。修复:
      //  - 真正的停止条件 = 连续 N 轮无增长 (默认 4 轮),不是 1 轮
      //  - 每轮 hard scroll: 容器 scrollTop=scrollHeight + scrollToIndex(末项 +1)
      //    多手段触发懒加载哨兵
      //  - 增长后立刻 0.5s 短间隔继续推, 卡住时退避到 1.5s 给后端追时间
      //  - 总时长上限 90s (远超旧实现的 30*0.7=21s),够拉 ~500 人
      async function loadAll() {
        if (!ulVue) return null;
        const STALL_ROUNDS = 4;          // 连续 N 轮无增长才停
        const FAST_WAIT_MS = 500;
        const STALL_WAIT_MS = 1500;
        const TOTAL_DEADLINE_MS = 90000;
        const start = Date.now();
        // The actual scrollable element. Prefer the explicit user-list scroller
        // class first; fall back to ulVue.$el / its parent.
        const scroller =
          document.querySelector(".user-list.b-scroll-stable") ||
          ulVue.$el?.parentElement ||
          ulVue.$el ||
          null;

        let stallCount = 0;
        let prev = (ulVue.$props?.dataSources || []).length;
        while (Date.now() - start < TOTAL_DEADLINE_MS) {
          // Trigger lazy-load via two complementary mechanisms.
          if (scroller) {
            try { scroller.scrollTop = scroller.scrollHeight; } catch { /* noop */ }
          }
          try {
            const targetIdx = Math.max(0, (ulVue.$props?.dataSources || []).length); // one past last
            if (typeof ulVue.scrollToIndex === "function") ulVue.scrollToIndex(targetIdx);
          } catch { /* noop */ }

          await new Promise(r => setTimeout(r, stallCount === 0 ? FAST_WAIT_MS : STALL_WAIT_MS));

          const curLen = (ulVue.$props?.dataSources || []).length;
          if (curLen > prev) {
            prev = curLen;
            stallCount = 0;          // progress — reset
          } else {
            stallCount += 1;
            if (stallCount >= STALL_ROUNDS) break;
          }
        }
        return ulVue.$props?.dataSources || null;
      }
      loadAll().then(ds => {
        const data = ds
          ? ds.map((d) => ({
              uniqueId: d.uniqueId,
              name: d.name || "",
              jobName: d.jobName || "",
            }))
          : null;
        window.postMessage({ __intakeBridgeReply: true, id, data }, "*");
      });
      return;  // reply sent async inside loadAll().then()
    } else if (cmd === "scroll_to_geek") {
      const ds = ulVue?.$props?.dataSources || [];
      const idx = ds.findIndex((d) => String(d.uniqueId) === String(bossId));
      if (idx !== -1 && ulVue?.scrollToIndex) ulVue.scrollToIndex(idx);
      window.postMessage({ __intakeBridgeReply: true, id, idx }, "*");
    } else if (cmd === "send_text") {
      const editor = document.querySelector(".conversation-editor");
      const vm = editor?.__vue__;
      let ok = false;
      if (vm && typeof vm.sendText === "function") {
        try { vm.sendText(); ok = true; } catch (_) {}
      }
      window.postMessage({ __intakeBridgeReply: true, id, ok }, "*");
    }
  });
})();
