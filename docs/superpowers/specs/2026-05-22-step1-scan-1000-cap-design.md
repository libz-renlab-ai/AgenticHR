# Step1 Scan: Lift 300-Person Cap — Design

**Date:** 2026-05-22
**Status:** Approved
**Author:** liboze + Claude

## Problem

Users with ~1000 BOSS contacts find Step1 "scan all" toast reports at most
~300 registered candidates, while the BOSS chat list contains 1000+. Bug
report: "显示最多注册300人，然而实际上会更多。"

## Root Cause

Two-layer cap:

1. **Client-side (primary):** `edge_extension/content.js:1903`

   ```js
   const _STEP1_SCAN_LIMIT = 300;
   ```

   Applied via `dataSources.slice(0, _STEP1_SCAN_LIMIT)` (line 1945) on the
   virtual-list result, and `processed.size >= _STEP1_SCAN_LIMIT break` in
   the DOM-fallback branch (line 1971). Introduced in commit `576a46f`
   (2026-04-26) as initial-version safety cap.

2. **Bridge-side (secondary):** `edge_extension/main_world_bridge.js`

   ```js
   const TOTAL_DEADLINE_MS = 90000;   // 90 s
   const STALL_ROUNDS = 4;
   ```

   Comment self-reports "够拉 ~500 人". At Boss's lazy-load cadence
   (~30 candidates / 500 ms tick under nominal network), 1000 candidates
   approach the deadline. With network jitter or backend throttle, bridge
   may timeout before reaching 1000.

## Design

| Layer | Old | New | Rationale |
|---|---|---|---|
| client `_STEP1_SCAN_LIMIT` | 300 | **removed** | No product reason; Step2 already uses `?limit=9999`. `/candidates/register` is idempotent + lightweight, no capacity need. |
| bridge `TOTAL_DEADLINE_MS` | 90000 | 180000 (3 min) | Doubles headroom for ~1200 candidates with worst-case Boss throttle. Stable-rounds still provides early exit when list is exhausted. |
| bridge `STALL_ROUNDS` | 4 | 6 | More tolerant of Boss backend hiccups; with 1.5s stall wait that's +3s before giving up. |
| Step1 main loop | silent | toast every 50 candidates: `Step1: 注册中 X/total...` | User can see progress instead of staring at static "Step1: 共 N 人，注册中..." |
| Step1 main loop | no pause/stop | `await waitIfPaused()` per iteration; `_setRunning(true/false)` at entry/exit | Reuses existing extension pause/stop machinery (`content.js:51-116`, `popup.html:404 btnPause`). Zero new UI. |

## Non-Goals

- Backend `/candidates/register` is untouched: it's already idempotent
  via `ensure_candidate`, no rate-limit needed.
- Step2 (`step2_enrichCandidates`) is untouched: already uses
  `?limit=9999`, no 300 cap.
- 30 ms per-call throttle is kept: `30 ms × 1000 = 30 s`, acceptable.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Bridge 180s makes "small list" users wait longer | Low | Stable-rounds (6 × 1.5s = 9s extra worst case after list exhausted) — bounded by quick exit when no growth. |
| 1000 registers swamp local API | Very low | 30 ms throttle, localhost FastAPI, idempotent. |
| BOSS risk-control triggered mid-run | Medium | Pause/stop wiring lets user halt; existing risk-control detector path in `content.js:1062` remains. |
| `boss_id` uniqueness conflict | None | Backend `ensure_candidate` is idempotent on `(user_id, boss_id)`. |

## Verification

Manual checklist (see plan Task 6):

1. BOSS list with >300 contacts → Step1 → final toast `扫描 N 人` where N matches list size (or > 300 if list <1200).
2. Console log `[step1] 虚拟列表读取: 共 N 条候选人` — N should equal final processed count.
3. Mid-run pause via popup `btnPause` halts within ~1 iteration.
