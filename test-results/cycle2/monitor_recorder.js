// AlgoGuard test cycle 2 - Live Monitor recorder (read only).
//
// Records one monitoring session from the app's own /monitor/status and summarises it for:
//   TC-11  live event-to-verdict lag, p95 of flows ended by FIN/RST (budget 500 ms)
//   TC-24  share of benign live flows labelled Attack (target 8% or less)
//   TC-25  how the flows of a port scan were labelled
//   TC-23  the capture ID that test-results/def_fixes_20261023/upload_delay.py needs
//
// How to use (Chrome or Edge, signed in to AlgoGuard):
//   1. Open Live Monitor and choose the source and storage the procedure asks for.
//   2. Press F12, open Console, paste this whole file and press Enter. The first time, the
//      browser asks you to type "allow pasting".
//   3. Press Start Monitoring. The recorder waits for the session, follows it, and finishes when
//      the session stops (or after MAX_SECONDS). It prints a summary and saves a JSON file to
//      the Downloads folder. If the download is blocked, type copy(algoguardRecord) in the
//      console and paste the text into a file.
//
// It only reads; it changes nothing in AlgoGuard. The saved file holds no e-mail address,
// password, token or IP address: ports, protocols, verdicts, bands and timings only.
(async () => {
  const LABEL = "cycle2"; // e.g. "TC-11_PC-B": becomes part of the file name
  const MAX_SECONDS = 1800;
  const ACTIVE = ["starting", "running", "paused", "stopping"];
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const events = new Map();
  let since = 0;
  let sessionId = null;
  let session = null;
  let firstSeq = null;
  let lastSeq = 0;
  const deadline = Date.now() + MAX_SECONDS * 1000;
  console.log("AlgoGuard recorder: waiting for a monitoring session...");

  while (Date.now() < deadline) {
    let body;
    try {
      const query = `since=${since}` + (sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : "");
      const response = await fetch(`/monitor/status?${query}`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) {
        console.warn("AlgoGuard recorder: status answered", response.status);
        await pause(1000);
        continue;
      }
      body = await response.json();
    } catch (error) {
      console.warn("AlgoGuard recorder:", error);
      await pause(1000);
      continue;
    }
    const current = body.session || {};
    if (!sessionId) {
      if (!current.session_id || !ACTIVE.includes(current.state)) {
        await pause(500);
        continue;
      }
      sessionId = current.session_id;
      console.log("AlgoGuard recorder: following session", sessionId);
    } else if (current.session_id && current.session_id !== sessionId) {
      console.warn("AlgoGuard recorder: another session started; stopping here.");
      break;
    }
    session = current;
    for (const event of body.events || []) {
      events.set(event.seq, event);
      if (firstSeq === null || event.seq < firstSeq) firstSeq = event.seq;
    }
    lastSeq = Math.max(lastSeq, body.last_seq || 0);
    since = lastSeq;
    if (!ACTIVE.includes(current.state)) break;
    await pause(500);
  }

  const percentile = (values, p) => {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const rank = (p / 100) * (sorted.length - 1);
    const low = Math.floor(rank);
    const high = Math.ceil(rank);
    return Math.round((sorted[low] + (sorted[high] - sorted[low]) * (rank - low)) * 10) / 10;
  };
  const list = [...events.values()].sort((a, b) => a.seq - b.seq);
  const teardown = list
    .filter((e) => (e.end_reason === "fin" || e.end_reason === "rst") && e.detection_lag_ms !== null && e.detection_lag_ms !== undefined)
    .map((e) => e.detection_lag_ms);
  const count = (key) => list.reduce((acc, e) => ({ ...acc, [e[key] ?? "-"]: (acc[e[key] ?? "-"] || 0) + 1 }), {});
  const totals = (session && session.totals) || {};
  const capture = (session && session.capture) || {};
  const packets = capture.packets || 0;
  const summary = {
    label: LABEL,
    recorded_at_utc: new Date().toISOString(),
    session_id: sessionId,
    capture_id: session ? session.capture_id : null,
    state: session ? session.state : null,
    source_type: session ? session.source_type : null,
    persist: session ? session.persist : null,
    started_at_utc: session ? session.started_at : null,
    deployment_id: session && session.deployment ? session.deployment.deployment_id : null,
    flows: totals.flows ?? null,
    attacks: totals.attacks ?? null,
    attack_share_percent: totals.flows ? Math.round((totals.attacks / totals.flows) * 10000) / 100 : null,
    alerts: totals.alerts ?? null,
    stored: totals.persisted ?? null,
    events_received: list.length,
    events_missed: firstSeq === null ? 0 : lastSeq - firstSeq + 1 - list.length,
    fin_rst_lag_ms: {
      n: teardown.length,
      p50: percentile(teardown, 50),
      p95: percentile(teardown, 95),
      max: teardown.length ? Math.max(...teardown) : null,
    },
    end_reasons: count("end_reason"),
    bands: count("attack_score_band"),
    capture: { packets, dropped: capture.dropped ?? null, excluded_cloud: capture.excluded_cloud ?? null,
      drop_percent: packets ? Math.round(((capture.dropped || 0) / packets) * 10000) / 100 : null },
  };
  const record = {
    summary,
    events: list.map((e) => ({
      seq: e.seq, prediction: e.prediction, band: e.attack_score_band, attack_probability: e.attack_probability,
      confidence: e.confidence, detection_lag_ms: e.detection_lag_ms, end_reason: e.end_reason, proto: e.proto,
      service: e.service, state: e.state, destination_port: e.destination_port,
    })),
  };
  window.algoguardRecord = record;
  console.log("AlgoGuard recorder summary:", JSON.stringify(summary, null, 2));
  const stamp = summary.recorded_at_utc.replace(/[:.]/g, "-");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: "application/json" }));
  link.download = `algoguard_${LABEL}_${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
})();
