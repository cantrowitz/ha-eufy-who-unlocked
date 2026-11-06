/**
 * Shared unlock-event mapping for the eufy who-unlocked bridge.
 *
 * One function, used by both bridge.mjs (live) and test-replay.mjs (replay),
 * so the mapping verified against the real capture file is exactly the
 * mapping that runs live. No credentials, no serials, no user_ids here.
 *
 * Verified against the Mac capture 2026-11-05
 * (t85v0-capture-2026-11-06T02-26-13-636Z.jsonl):
 *   261 app unlock    nick 'adam'     short 000A
 *   257 manual        nick 'cantrowitz' short 0000
 *   530 palm/vein     nick per enrolled user — 'cantrowitz' 0000 (Adam),
 *                     'Yvonne ' 0003 (Yvonne, 21:31:26 EST, from chat)
 *   259 passcode      nick 'Emma '    short 0002
 * Param 6000: '3' = unlocked, '4' = locked.
 *
 * Event 530 is NEW: it is not in the SDK's LockPushEvent enum (257-269),
 * and the SDK's lockState decode drops it (no `locked` field on the 530
 * lockState payload). We derive locked from param 6000 instead, so 530
 * works the same as the others.
 *
 * Who's-home note: an exterior unlock means that person ARRIVED. It does
 * not prove they are still home — leaving produces no unlock event, and
 * there is no presence entity here. Do not invent one from this data.
 * Manual (257, thumbturn) is truly anonymous: it carries the account
 * holder's name/short id, not the identity of whoever turned it.
 */

// event_type -> { method, label, exterior, kind }
const EVENT_MAP = {
  // Unlocks
  257: { method: "manual", label: "Manual / thumbturn", exterior: false, kind: "unlock" },
  258: { method: "auto", label: "Auto unlock", exterior: true, kind: "unlock" },
  259: { method: "passcode", label: "Passcode / PIN", exterior: true, kind: "unlock" },
  260: { method: "fingerprint", label: "Fingerprint", exterior: true, kind: "unlock" },
  261: { method: "app", label: "App", exterior: true, kind: "unlock" },
  269: { method: "passcode", label: "Temporary passcode", exterior: true, kind: "unlock" },
  // 530 is the palm/vein unlock. Not in the SDK enum, verified live.
  530: { method: "palm", label: "Palm / vein", exterior: true, kind: "unlock" },
  // Locks — update locked state only, never overwrite the last unlock.
  262: { method: "manual_lock", label: "Manual lock", exterior: false, kind: "lock" },
  263: { method: "keypad_lock", label: "Keypad lock", exterior: false, kind: "lock" },
  264: { method: "app_lock", label: "App lock", exterior: false, kind: "lock" },
  265: { method: "auto_lock", label: "Auto lock", exterior: false, kind: "lock" },
  266: { method: "passcode_lock", label: "Passcode lock", exterior: false, kind: "lock" },
  267: { method: "fingerprint_lock", label: "Fingerprint lock", exterior: false, kind: "lock" },
  268: { method: "temporary_passcode_lock", label: "Temporary passcode lock", exterior: false, kind: "lock" },
};

/**
 * Derive locked/unlocked from the payload.
 * Param 6000 is authoritative ('3' unlocked, '4' locked). Fall back to a
 * boolean `locked` field on the payload (present on lockState for 257-269,
 * absent for 530). Returns true/false, or null when neither is available.
 */
export function lockedFromPayload(payload) {
  const params = Array.isArray(payload?.params) ? payload.params : [];
  const p6000 = params.find((p) => Number(p.param_type) === 6000);
  if (p6000 !== undefined) {
    if (String(p6000.param_value) === "3") return false;
    if (String(p6000.param_value) === "4") return true;
  }
  if (typeof payload?.locked === "boolean") return payload.locked;
  return null;
}

/**
 * Map one raw lock-event payload to a normalised unlock/lock object.
 * Accepts the push payload, the lockState payload, or the event payload —
 * they all carry the same fields (event_type, nick_name, short_user_id,
 * event_time, params). Returns null for event types we do not recognise
 * (not a lock event at all) — the caller should ignore those, not log them
 * as unlocks.
 *
 * Returned shape (unlock):
 *   { who, nick_name, short_user_id, method, method_label, event_type,
 *     exterior, at_iso, at_epoch_ms, locked, kind: "unlock" }
 * Returned shape (lock): same, but kind: "lock" and locked === true.
 * `who` is the trimmed nick_name as eufy reports it. For manual (257)
 * that is the account holder's name — anonymous in practice.
 * NOTE: the full user_id is deliberately NOT copied through. It is in the
 * raw payload; nothing downstream needs it, and it must never be logged.
 */
export function mapUnlockEvent(payload) {
  if (!payload || typeof payload !== "object") return null;
  const eventType = Number(payload.event_type ?? payload.eventType);
  const spec = EVENT_MAP[eventType];
  if (!spec) return null;

  const atEpochMs =
    typeof payload.event_time === "number"
      ? payload.event_time
      : typeof payload.eventTime === "number"
        ? payload.eventTime
        : Date.now();
  const nick = typeof payload.nick_name === "string" ? payload.nick_name.trim() : "";
  const shortId =
    typeof payload.short_user_id === "string" ? payload.short_user_id.trim() : "";
  const locked = lockedFromPayload(payload);

  return {
    who: nick || null,
    nick_name: nick || null,
    short_user_id: shortId || null,
    method: spec.method,
    method_label: spec.label,
    event_type: eventType,
    exterior: spec.exterior,
    at_iso: new Date(atEpochMs).toISOString(),
    at_epoch_ms: atEpochMs,
    locked,
    kind: spec.kind,
  };
}

/**
 * Deduplicating tracker: one physical unlock arrives up to four times
 * (pushRaw, push, lockState, event — all with the same event_type,
 * event_time and short_user_id). apply() returns the mapped object the
 * first time it sees a key and null for repeats, so the bridge and the
 * replay never count one unlock four times.
 * Also holds the current state: lastUnlock (unlocks only) + locked.
 */
export function createUnlockTracker({ keepKeys = 200 } = {}) {
  const seen = new Set();
  let lastUnlock = null;
  let locked = null;

  return {
    /** Feed one raw payload. Returns the mapped object, or null if it was a duplicate / not a lock event. */
    apply(payload) {
      const mapped = mapUnlockEvent(payload);
      if (!mapped) return null;
      const key = `${mapped.event_type}:${mapped.at_epoch_ms}:${mapped.short_user_id ?? ""}`;
      if (seen.has(key)) return null;
      seen.add(key);
      if (seen.size > keepKeys) {
        // Drop the oldest keys; a Set iterates in insertion order.
        for (const k of seen) {
          seen.delete(k);
          if (seen.size <= keepKeys) break;
        }
      }
      if (mapped.locked !== null) locked = mapped.locked;
      if (mapped.kind === "unlock") lastUnlock = mapped;
      return mapped;
    },
    get lastUnlock() {
      return lastUnlock;
    },
    get locked() {
      return locked;
    },
    /** The GET /last-unlock payload: latest unlock, with the current locked flag merged in. Null when no unlock seen yet. */
    get lastUnlockView() {
      if (!lastUnlock) return null;
      return { ...lastUnlock, locked };
    },
    setSeed({ lastUnlock: seedUnlock = null, locked: seedLocked = null } = {}) {
      if (seedUnlock) lastUnlock = seedUnlock;
      if (seedLocked !== null && seedLocked !== undefined) locked = seedLocked;
    },
  };
}
