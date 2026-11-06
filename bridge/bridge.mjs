#!/usr/bin/env node
/**
 * Phase 2 — eufy who-unlocked bridge (long-lived push listener).
 *
 * Scope: who unlocked / last unlock + unlock method ONLY.
 * No camera, motion, battery, or lock-control entities. Read-only:
 * it listens, it never locks or unlocks anything.
 *
 * How it works:
 *  - Login once (captcha / 2FA handled interactively, same as
 *    capture-t85v0-push.mjs). Session is cached in .eufy-session.json,
 *    so re-runs skip login.
 *  - Poll ONCE at startup (getDevice) to seed the locked state.
 *    Then push-only. No 60s poll loop.
 *  - Optional slow reconciliation: RECONCILE_MS (default 0 = off;
 *    set 600000 for the SDK default 10 min). When on, the SDK's
 *    background poll runs at that interval; when off, it is disabled
 *    with pollMs: 0 / setPollInterval(0).
 *  - On push disconnect/reconnect: log it and do ONE catch-up poll
 *    (getDevice) to re-seed the locked state, then back to push-only.
 *    Honest limit: a poll can only re-seed locked/unlocked — it cannot
 *    recover WHO unlocked during a gap. Missed unlocks stay missed.
 *  - Serves a tiny local HTTP API on BIND_HOST:PORT (default 0.0.0.0:8787):
 *      GET /last-unlock -> latest unlock JSON, or 404 if none seen yet
 *      GET /health      -> { status, push_connected, last_unlock_at, uptime_s, locked }
 *    Home Assistant reads /last-unlock for state only. The event itself
 *    is always push, so nothing is missed between HA reads.
 *    The API is LAN-only with no auth — Home Assistant must be able to
 *    reach the Mac Mini over the LAN. Do not expose the port publicly.
 *
 * Hosting: run this on the Mac Mini, NOT on this VM. The VM egress
 * proxy blocks android.clients.google.com/checkin, so FCM push
 * registration fails there (fetch failed, retry backoff doubling —
 * verified Nov 5, 2026). On the Mac, checkin is reachable (HTTP 400
 * on a bare HEAD, i.e. a real response) and push registers fine.
 *
 * Correlation rule for HA (see PLAN.md): a Matter unlock matches the
 * stored push only if |matter_time - push_time| <= 10s. Never assume
 * arrival order — Matter and eufy push are independent paths.
 *
 * Who's-home note: an exterior unlock means that person ARRIVED. It
 * does not prove they are still home — leaving produces no unlock
 * event, and there is no presence entity here. Do not invent one.
 *
 * Env: EUFY_EMAIL, EUFY_PASSWORD, EUFY_LOCK_SN (required),
 *      EUFY_COUNTRY (default US), PORT (default 8787),
 *      RECONCILE_MS (default 0 = off).
 * Credentials come only from env vars. Never log the password, email,
 * serial, or full user_id — logs carry nick_name, short_user_id and
 * event_type only.
 */

import http from "node:http";
import { createInterface } from "node:readline/promises";
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { EufyMega, FileSessionStore, LoginStatus } from "@mega-yfue/eufy-sdk";
import { createUnlockTracker } from "./unlock-map.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SN = process.env.EUFY_LOCK_SN;
const PORT = Number(process.env.PORT || 8787);
// Listen on all interfaces by default: Home Assistant runs on a
// different host from this bridge (the Mac Mini), so loopback-only
// would make the bridge unreachable from HA. LAN-only, no auth —
// trust your home network, and do NOT expose this port to the internet.
// Set BIND_HOST=127.0.0.1 to restrict it to this machine only.
const BIND_HOST = process.env.BIND_HOST || "0.0.0.0";
const RECONCILE_MS = Number(process.env.RECONCILE_MS || 0);
const LAST_UNLOCK_PATH = path.join(__dirname, "last-unlock.json");
const STARTED_AT = Date.now();

if (!process.env.EUFY_EMAIL || !process.env.EUFY_PASSWORD || !SN) {
  console.error("Set EUFY_EMAIL, EUFY_PASSWORD and EUFY_LOCK_SN in the environment (never in a file).");
  process.exit(2);
}

const tracker = createUnlockTracker();
let pushConnected = false;
let lastPushAt = null;

// Restore the last known unlock across restarts, so /last-unlock is not
// empty until the next unlock. It is "last known", not live — the locked
// flag is re-seeded by the startup poll below.
try {
  const saved = JSON.parse(readFileSync(LAST_UNLOCK_PATH, "utf-8"));
  if (saved && typeof saved === "object" && saved.kind === "unlock") {
    tracker.setSeed({ lastUnlock: saved, locked: saved.locked ?? null });
  }
} catch {
  // No saved file yet — fine, start empty.
}

function log(obj) {
  console.log(JSON.stringify({ ts: new Date().toISOString(), ...obj }));
}

/** Persist the current last-unlock view atomically (write tmp + rename). */
function persist() {
  const view = tracker.lastUnlockView;
  if (!view) return;
  const tmp = `${LAST_UNLOCK_PATH}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(view, null, 2)}\n`);
  renameSync(tmp, LAST_UNLOCK_PATH);
}

/** Handle one mapped lock event: update state, log it, persist it. */
function handleMapped(mapped) {
  if (!mapped) return;
  if (mapped.kind === "unlock") {
    // Log nick_name / short_user_id / event_type only — never user_id.
    log({
      kind: "unlock",
      who: mapped.who,
      short_user_id: mapped.short_user_id,
      method: mapped.method,
      method_label: mapped.method_label,
      event_type: mapped.event_type,
      exterior: mapped.exterior,
      at_iso: mapped.at_iso,
      locked: mapped.locked,
    });
    persist();
  } else {
    log({ kind: "lock", method: mapped.method, event_type: mapped.event_type, locked: mapped.locked });
    persist();
  }
}

async function ask(prompt) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    return (await rl.question(`${prompt}: `)).trim();
  } finally {
    rl.close();
  }
}

async function login(eufy) {
  let r = await eufy.login();
  while (r.status !== LoginStatus.Ok) {
    if (r.status === LoginStatus.Captcha) {
      const file = path.join(__dirname, ".eufy-captcha.png");
      writeFileSync(file, Buffer.from(r.image.replace(/^data:image\/\w+;base64,/, ""), "base64"));
      r = await eufy.solveCaptcha(await ask(`captcha required — open ${file} and type the characters`));
    } else if (r.status === LoginStatus.TwoFactor) {
      r = await eufy.submitVerifyCode(await ask(`2FA code sent (${r.method}) — enter it`));
    } else {
      throw new Error(`unexpected login status: ${JSON.stringify(r)}`);
    }
  }
  log({ kind: "login", status: "ok" });
}

/** The ONE startup poll: seed locked state from the cloud device record. */
async function seedOnce(eufy, why) {
  try {
    const dev = await eufy.getDevice(SN);
    const lock = typeof dev.lock === "function" ? dev.lock() : null;
    const locked = lock?.locked ?? null;
    if (locked !== null && locked !== undefined) {
      tracker.setSeed({ locked });
    }
    log({ kind: "seed", why, model: dev.model, has_lock: typeof dev.lock === "function", locked });
    return dev;
  } catch (e) {
    log({ kind: "seed", why, status: "failed", message: e instanceof Error ? e.message : String(e) });
    return null;
  }
}

async function main() {
  const eufy = new EufyMega({
    email: process.env.EUFY_EMAIL,
    password: process.env.EUFY_PASSWORD,
    countryCode: process.env.EUFY_COUNTRY || "US",
    store: new FileSessionStore(path.join(__dirname, ".eufy-session.json")),
    // Push-only by default: no background cloud poll.
    pollMs: 0,
  });

  await login(eufy);

  // Startup poll (the only scheduled poll): seed locked state.
  await seedOnce(eufy, "startup");

  // Optional slow reconciliation, off by default. 600000 = SDK default 10 min.
  if (RECONCILE_MS > 0) {
    eufy.setPollInterval(RECONCILE_MS);
    log({ kind: "reconcile", status: "enabled", reconcile_ms: RECONCILE_MS });
  } else {
    eufy.setPollInterval(0);
    log({ kind: "reconcile", status: "off" });
  }

  // Push transport lifecycle. The SDK retries push itself (backoff
  // 30s doubling to 10 min); we just note it and re-seed on reconnect.
  eufy.on("pushConnect", async () => {
    pushConnected = true;
    log({ kind: "push", status: "connected" });
    await seedOnce(eufy, "reconnect");
  });
  eufy.on("pushDisconnect", () => {
    pushConnected = false;
    log({ kind: "push", status: "disconnected" });
  });
  eufy.on("error", (e) => {
    // Message only — the SDK error object can carry transport detail
    // we do not need in the log. Never log credentials here either.
    log({ kind: "error", message: e instanceof Error ? e.message : String(e) });
  });

  // One physical unlock arrives up to four times (pushRaw, push,
  // lockState, event). The tracker dedupes by event_type + event_time +
  // short_user_id, so each unlock is handled exactly once.
  // lockState is the canonical source; push/event are fallbacks for
  // event types lockState does not decode (e.g. 530 carries no `locked`
  // on lockState — the mapper derives it from param 6000 instead).
  const feed = (payload) => {
    if (!payload || typeof payload !== "object") return;
    lastPushAt = new Date().toISOString();
    handleMapped(tracker.apply(payload));
  };
  eufy.on("lockState", (e) => feed(e));
  eufy.on("push", (e) => {
    if (e.deviceSn !== SN && e.stationSn !== SN) return;
    feed(e.payload);
  });
  eufy.on("event", (e) => {
    if ((e.deviceSn ?? e.stationSn) !== SN) return;
    feed(e);
  });
  eufy.on("pushRaw", (raw) => {
    const inner = raw?.payload?.payload;
    const sn = raw?.payload?.device_sn ?? raw?.payload?.station_sn;
    if (sn !== SN || !inner || typeof inner !== "object") return;
    feed(inner);
  });

  // Local HTTP API. No auth: it is reachable from anywhere on the
  // home LAN (BIND_HOST, default 0.0.0.0), which is how Home Assistant
  // — running on a different host — reads it. Do not expose this
  // port beyond the LAN.
  const server = http.createServer((req, res) => {
    const send = (status, obj) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(`${JSON.stringify(obj)}\n`);
    };
    if (req.method === "GET" && req.url === "/last-unlock") {
      const view = tracker.lastUnlockView;
      if (!view) {
        send(404, { error: "no unlock seen yet", locked: tracker.locked });
        return;
      }
      send(200, view);
      return;
    }
    if (req.method === "GET" && req.url === "/health") {
      send(200, {
        status: pushConnected ? "ok" : "degraded",
        push_connected: pushConnected,
        last_unlock_at: tracker.lastUnlock?.at_iso ?? null,
        last_push_at: lastPushAt,
        locked: tracker.locked,
        uptime_s: Math.floor((Date.now() - STARTED_AT) / 1000),
        reconcile_ms: RECONCILE_MS,
      });
      return;
    }
    send(404, { error: "not found" });
  });
  server.listen(PORT, BIND_HOST, () => {
    log({ kind: "http", status: "listening", host: BIND_HOST, port: PORT });
    log({ kind: "ready", note: "push-only; startup poll done. Waiting for unlocks." });
  });

  const shutdown = async () => {
    log({ kind: "shutdown", status: "stopping" });
    server.close();
    try {
      await eufy.disconnect();
    } catch {
      // Best effort on shutdown.
    }
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main().catch((e) => {
  console.error("FATAL", e instanceof Error ? e.message : String(e));
  process.exit(1);
});
