// PRF-01 / TC-11: paste into Chrome DevTools > Console on the AlgoGuard Live Monitor page
// (127.0.0.1:5000/monitor) right after pressing Start Monitoring on a LIVE capture.
// For 90 seconds it reads the same /monitor/status feed the page uses and keeps, per flow, only:
// detection lag, end reason, model latency, verdict and time. No IP addresses or ports are kept.
// At the end it downloads tc11_live_lag.json to your Downloads folder.
(async () => {
  const seconds = 90, events = new Map();
  let since = 0, session = null;
  const stop = Date.now() + seconds * 1000;
  console.log(`TC-11: collecting for ${seconds} s ...`);
  while (Date.now() < stop) {
    try {
      const r = await fetch("/monitor/status?since=" + since, { credentials: "same-origin" });
      const d = await r.json();
      for (const e of d.events || []) {
        const key = e.event_uuid || e.seq;
        events.set(key, {
          seq: e.seq, time: e.timestamp, verdict: e.prediction, end_reason: e.end_reason,
          detection_lag_ms: e.detection_lag_ms, latency_ms: e.latency_ms,
        });
      }
      since = d.last_seq || since;
      if (d.session) {
        const s = d.session;
        session = {
          state: s.state, source_type: s.source_type, totals: s.totals, capture: s.capture,
          avg_detection_lag_ms: s.avg_detection_lag_ms, avg_latency_ms: s.avg_latency_ms,
          capped: s.capped,
        };
      }
    } catch (err) { console.warn("poll failed", err); }
    await new Promise((ok) => setTimeout(ok, 2000));
  }
  const out = { collected_at: new Date().toISOString(), seconds, session, events: [...events.values()] };
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }));
  a.download = "tc11_live_lag.json";
  a.click();
  console.log(`TC-11: saved ${out.events.length} flows to Downloads/tc11_live_lag.json`);
})();
