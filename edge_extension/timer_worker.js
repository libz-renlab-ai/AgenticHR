// timer_worker.js — Worker-based timer pump.
//
// Web Worker 的 setTimeout 不受 Chrome hidden-tab 节流影响 (实测验证
// 2026-05-22: hidden tab 中 setTimeout(30) 实际 32ms, 主线程是 941ms).
// 主线程通过 postMessage 向本 Worker 请求 "睡 X ms", Worker 用真实的
// setTimeout 等待后回复唤醒消息.
//
// 协议:
//   主→W: { type:'sleep', reqId:number, ms:number }
//   W→主: { type:'wake',  reqId:number }
//
// 主线程维护 reqId → resolver 映射, wake 到达时 resolve 对应 Promise.

self.onmessage = (e) => {
  const data = e.data || {};
  if (data.type === 'sleep') {
    const { reqId, ms } = data;
    setTimeout(() => {
      self.postMessage({ type: 'wake', reqId });
    }, ms);
  }
};
