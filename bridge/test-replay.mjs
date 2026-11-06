#!/usr/bin/env node
/**
 * Replay a .jsonl capture file through the SAME mapping + dedupe code
 * bridge.mjs uses live (unlock-map.mjs), and print the resulting
 * last-unlock objects. Verifies the mapping against real pushes without
 * a live login — important, because a live login cannot run on this VM
 * (FCM checkin is blocked by the egress proxy).
 *
 * Usage:
 *   node test-replay.mjs [capture-file]
 *   node test-replay.mjs ~/workspace/user/files/t85v0-capture-2026-11-06T02-26-13-636Z.jsonl
 *
 * Expected, for the Mac capture of 2026-11-05 (5 distinct unlocks):
 *   21:26:39 app      user1       000A  event 261  exterior true
 *   21:27:18 app      user1       000A  event 261  exterior true
 *   21:27:28 manual   user1 0000  event 257  exterior false
 *   21:27:55 palm     user1 0000  event 530  exterior true
 *   21:28:20 passcode user2       0002  event 259  exterior true
 *
 * Also tested, synthetically (not in the capture file): user3's palm
 * at 21:31:26 EST — event 530, nick 'user3 ', short 0003 — which must
 * map to who 'user3', method palm, exterior true. That is the proof
 * palm names the person per enrolled user; user1's own palm reads
 * 'user1'/0000 only because that is his name in the app.
 *
 * Exits 0 when every expected mapping passes, 1 otherwise.
 */

import { readFileSync } from "node:fs";
import { createUnlockTracker } from "./unlock-map.mjs";

const file = process.argv[2];
if (!file) {
  console.error("Usage: node test-replay.mjs <capture-file.jsonl>");
  console.error("The capture file is not committed to this repo (it contains device/user identifiers).");
  process.exit(2);
}
const lines = readFileSync(file, "utf-8").split("\n").filter(Boolean);

const tracker = createUnlockTracker();
const unlocks = [];

// The capture file is local-only and never committed: it carries the
// real nicknames eufy reports. Expected values in this repo use generic
// placeholders (user1/user2/user3), so normalize names before checking
// (and before printing) — method, times and short ids are compared raw.
const NAME_MAP = { adam: "user1", cantrowitz: "user1", Emma: "user2", Yvonne: "user3" };
function normalize(u) {
  if (!u) return u;
  const who = NAME_MAP[u.who] ?? u.who;
  const nick = u.nick_name == null ? u.nick_name : (NAME_MAP[u.nick_name.trim()] ?? u.nick_name.trim());
  return { ...u, who, nick_name: nick };
}

/** Extract the lock-event payload from one capture line, whatever its kind. */
function payloadOf(line) {
  const d = JSON.parse(line);
  switch (d.kind) {
    case "push":
    case "lockState":
    case "event":
      return d.payload ?? null;
    case "pushRaw":
      return d.raw?.payload?.payload ?? null;
    default:
      return null; // "device" line etc.
  }
}

for (const line of lines) {
  let payload;
  try {
    payload = payloadOf(line);
  } catch {
    continue;
  }
  const mapped = tracker.apply(payload);
  if (mapped && mapped.kind === "unlock") unlocks.push(normalize(mapped));
}

console.log("Replayed:", file);
console.log(`Distinct unlocks: ${unlocks.length}`);
for (const u of unlocks) console.log(JSON.stringify(u));

// --- Assertions against the verified Mac capture ---
const expected = [
  { at_epoch_ms: 1793931999000, event_type: 261, who: "user1", short_user_id: "000A", method: "app", exterior: true, locked: false },
  { at_epoch_ms: 1793932038000, event_type: 261, who: "user1", short_user_id: "000A", method: "app", exterior: true, locked: false },
  { at_epoch_ms: 1793932048000, event_type: 257, who: "user1", short_user_id: "0000", method: "manual", exterior: false, locked: false },
  { at_epoch_ms: 1793932075000, event_type: 530, who: "user1", short_user_id: "0000", method: "palm", exterior: true, locked: false },
  { at_epoch_ms: 1793932100000, event_type: 259, who: "user2", short_user_id: "0002", method: "passcode", exterior: true, locked: false },
];

let failures = 0;
function check(name, cond, detail = "") {
  console.log(`${cond ? "PASS" : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!cond) failures++;
}

check("unlock count is 5", unlocks.length === 5, `got ${unlocks.length}`);
expected.forEach((exp, i) => {
  const got = unlocks[i];
  const ok =
    got &&
    got.at_epoch_ms === exp.at_epoch_ms &&
    got.event_type === exp.event_type &&
    got.who === exp.who &&
    got.short_user_id === exp.short_user_id &&
    got.method === exp.method &&
    got.exterior === exp.exterior &&
    got.locked === exp.locked;
  check(`unlock ${i + 1}: ${exp.method} / ${exp.who}`, ok, ok ? "" : `got ${JSON.stringify(got)}`);
});

// --- Synthetic: user3's palm (from chat, 21:31:26 EST, not in the file) ---
const yvonne = tracker.apply({
  event_type: 530,
  event_time: 1793932286000,
  short_user_id: "0003",
  nick_name: "user3 ",
  params: [
    { param_type: 6001, param_value: "72" },
    { param_type: 6000, param_value: "3" },
  ],
});
check(
  "synthetic user3 palm: who user3, method palm, exterior true",
  yvonne?.who === "user3" && yvonne?.method === "palm" && yvonne?.exterior === true && yvonne?.locked === false,
  `got ${JSON.stringify(yvonne)}`,
);

// --- Synthetic: a lock event updates locked without overwriting last unlock ---
const lockEv = tracker.apply({
  event_type: 265,
  event_time: 1793932300000,
  short_user_id: "0000",
  nick_name: "user1",
  params: [{ param_type: 6000, param_value: "4" }],
});
check(
  "synthetic auto-lock: kind lock, locked true, last unlock still user3 (from the synthetic palm just before)",
  lockEv?.kind === "lock" && tracker.locked === true && tracker.lastUnlock?.who === "user3",
  `got ${JSON.stringify(lockEv)}, lastUnlock=${JSON.stringify(tracker.lastUnlock)}`,
);

// --- Dedupe: replaying the same payload again must return null ---
const dup = tracker.apply({
  event_type: 259,
  event_time: 1793932100000,
  short_user_id: "0002",
  nick_name: "user2 ",
  params: [{ param_type: 6000, param_value: "3" }],
});
check("duplicate user2 PIN is ignored", dup === null, `got ${JSON.stringify(dup)}`);

console.log(failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
