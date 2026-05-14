// One-off test for intake_retry — run with: node edge_extension/test_intake_retry.mjs
// edge_extension/ has no JS test framework, so this extracts the REAL
// intake_retry from content.js and exercises it with stubbed sleep/log.
// Covers the Step2 network-resilience fix (李振华: BOSS直聘 lag → bare
// `continue` → candidate permanently skipped, zero retry).
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./content.js", import.meta.url), "utf8");
// content.js is CRLF — match the end-of-top-level-function brace either way.
const m = src.match(/async function intake_retry[\s\S]*?\r?\n\}\r?\n/);
if (!m) {
  console.error("FAIL: could not extract intake_retry from content.js");
  process.exit(1);
}

// Stubbed deps the function closes over in content.js.
let slept = 0;
const sleep = async (ms) => { slept += ms; };
const log = () => {};
const intake_retry = eval(`(${m[0].trim()})`);

let pass = 0, fail = 0;
const ok = (cond, name) => {
  if (cond) { pass++; console.log("  ok   -", name); }
  else { fail++; console.log("  FAIL -", name); }
};

// 1. success on first try → returns result, never sleeps
await (async () => {
  slept = 0;
  let calls = 0;
  const r = await intake_retry(() => { calls++; return "done"; }, { tries: 3 });
  ok(r === "done" && calls === 1 && slept === 0, "success on first try — no retry, no sleep");
})();

// 2. fails (falsy) twice then succeeds → result returned on 3rd attempt
await (async () => {
  let calls = 0;
  const r = await intake_retry(
    (attempt) => { calls++; return attempt === 3 ? "ok3" : null; },
    { tries: 3, delayMs: 5 },
  );
  ok(r === "ok3" && calls === 3, "retries falsy returns, succeeds on 3rd attempt");
})();

// 3. every attempt fails (falsy) → returns null after exactly `tries` attempts
await (async () => {
  let calls = 0;
  const r = await intake_retry(() => { calls++; return null; }, { tries: 3, delayMs: 1 });
  ok(r === null && calls === 3, "gives up after `tries` falsy returns");
})();

// 4. a thrown error counts as a failed attempt and is retried
await (async () => {
  let calls = 0;
  const r = await intake_retry(
    (attempt) => { calls++; if (attempt < 2) throw new Error("boss lag"); return "recovered"; },
    { tries: 3, delayMs: 1 },
  );
  ok(r === "recovered" && calls === 2, "thrown error is retried, then succeeds");
})();

// 5. throws on every attempt → returns null, never propagates the throw
await (async () => {
  let threw = false, r;
  try {
    r = await intake_retry(() => { throw new Error("always"); }, { tries: 2, delayMs: 1 });
  } catch { threw = true; }
  ok(!threw && r === null, "exhausted-by-throw returns null, never throws");
})();

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
