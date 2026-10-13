/* FactoryPilot front-end. Vanilla JS, no build step. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const STATUS_TEXT = { ok: "Normal", info: "Normal", warn: "Warning", alarm: "Alarm", trip: "Trip" };
  const ORDER_STATUS = { awaiting_approval: "Needs approval", dispatched: "Dispatched", closed: "Closed", rejected: "Rejected" };

  const state = {
    tab: "floor", machines: [], selected: "conveyor-c2", samples: [], sample: null, file: null,
    orders: [], orderId: null, status: null, running: false, chat: [],
  };

  /* ---------------- utilities ---------------- */
  async function api(path, opts = {}) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    return r.json();
  }
  async function sse(path, opts, onEvent) {
    const r = await fetch(path, opts);
    if (!r.ok || !r.body) {
      let msg = `Request failed (${r.status})`;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch (e) { console.error(e); continue; }
        onEvent(ev);
      }
    }
  }
  let toastTimer;
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 3200);
  }
  const stateChip = (s) => `<span class="state ${esc(s)}">${esc(STATUS_TEXT[s] || s)}</span>`;
  const sevChip = (s) => `<span class="state ${esc(s)}">${esc(s.charAt(0).toUpperCase() + s.slice(1))}</span>`;

  /* ---------------- tabs ---------------- */
  function setTab(tab) {
    state.tab = tab;
    $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    $$(".view").forEach((v) => (v.hidden = v.dataset.view !== tab));
    if (tab === "orders") loadOrders();
    if (tab === "system") loadSystem();
    history.replaceState(null, "", `#${tab}`);
  }
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  /* ---------------- clock & status ---------------- */
  setInterval(() => ($("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })), 1000);

  async function loadStatus() {
    try {
      const s = await api("/api/status");
      state.status = s;
      const amd = $("#pillAmd");
      amd.classList.toggle("on", s.llm.amd && s.llm.endpoint?.reachable);
      amd.classList.toggle("partial", s.llm.amd && !s.llm.endpoint?.reachable);
      $("span", amd).textContent = s.llm.amd ? (s.llm.endpoint?.reachable ? s.llm.hardware : "AMD endpoint unreachable") : "Offline demo";
      const ev = $("#pillEvolus");
      const blocks = Object.values(s.evolus.blocks).filter(Boolean).length;
      ev.classList.toggle("on", blocks >= 2);
      ev.classList.toggle("partial", blocks === 1);
      $("span", ev).textContent = s.evolus.live ? `${blocks}/3 blocks live` : "Local fallback";
      $("#backendHint").textContent = s.llm.amd
        ? `Reasoning runs on ${s.llm.model} · ${s.llm.hardware}.`
        : "Offline demo mode: diagnosis is composed by the rule-based reasoner. Set VLLM_BASE_URL to reason on AMD.";
    } catch (e) { console.warn(e); }
  }

  /* ---------------- charts ---------------- */
  function sparkline(values, limits) {
    if (!values?.length) return "";
    const w = 200, h = 44, lo = Math.min(...values, limits?.warn ?? Infinity) * 0.95, hi = Math.max(...values, limits?.alarm ?? -Infinity) * 1.05;
    const x = (i) => (i / (values.length - 1)) * w, y = (v) => h - ((v - lo) / (hi - lo || 1)) * h;
    const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const lines = limits ? `<line x1="0" x2="${w}" y1="${y(limits.warn)}" y2="${y(limits.warn)}" stroke="var(--warn)" stroke-dasharray="3 3"/>
      <line x1="0" x2="${w}" y1="${y(limits.alarm)}" y2="${y(limits.alarm)}" stroke="var(--alarm)" stroke-dasharray="3 3"/>` : "";
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${lines}
      <path d="${d}" fill="none" stroke="var(--chart-line)" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function lineChart(t, values, ch) {
    const W = 520, H = 130, pl = 34, pr = 8, pt = 8, pb = 18;
    const lim = ch?.limits;
    const pool = [...values];
    if (lim) { pool.push(lim.warn); if (lim.alarm != null) pool.push(lim.alarm); }
    let lo = Math.min(...pool), hi = Math.max(...pool);
    const pad = (hi - lo) * 0.12 || 1; lo -= pad; hi += pad;
    const x = (i) => pl + (i / Math.max(1, values.length - 1)) * (W - pl - pr);
    const y = (v) => pt + (1 - (v - lo) / (hi - lo)) * (H - pt - pb);
    const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const ticks = [lo + pad, (lo + hi) / 2, hi - pad];
    let grid = ticks.map((v) => `<line x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}" stroke="var(--chart-grid)"/>
      <text x="${pl - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="var(--muted)">${v.toFixed(v < 10 ? 1 : 0)}</text>`).join("");
    if (lim && !lim.low) {
      grid += `<rect x="${pl}" width="${W - pl - pr}" y="${pt}" height="${Math.max(0, y(lim.alarm) - pt)}" fill="var(--alarm)" opacity=".07"/>`;
      grid += `<line x1="${pl}" x2="${W - pr}" y1="${y(lim.warn)}" y2="${y(lim.warn)}" stroke="var(--warn)" stroke-dasharray="4 3"/>`;
      grid += `<line x1="${pl}" x2="${W - pr}" y1="${y(lim.alarm)}" y2="${y(lim.alarm)}" stroke="var(--alarm)" stroke-dasharray="4 3"/>`;
    }
    const labels = [0, Math.floor(t.length / 2), t.length - 1].map((i) => `<text x="${x(i)}" y="${H - 4}" font-size="10" fill="var(--muted)" text-anchor="${i === 0 ? "start" : i === t.length - 1 ? "end" : "middle"}">${esc(t[i])}</text>`).join("");
    const last = values[values.length - 1];
    const col = ch?.status === "alarm" || ch?.status === "trip" ? "var(--alarm)" : ch?.status === "warn" ? "var(--warn)" : "var(--chart-line)";
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(ch?.label)} trend">${grid}${labels}
      <path d="${d}" fill="none" stroke="var(--chart-line)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
      <circle cx="${x(values.length - 1)}" cy="${y(last)}" r="3.5" fill="${col}"/></svg>`;
  }

  /* ---------------- floor ---------------- */
  async function loadFleet() {
    try {
      state.machines = await api("/api/fleet");
      renderPlates(); renderDetail(); fillMachineSelects();
    } catch (e) { console.warn(e); }
  }
  function renderPlates() {
    $("#plates").innerHTML = state.machines.map((m) => {
      const vib = m.channels.find((c) => c.name === "vibration_rms_mm_s");
      const top = m.findings[0]?.text || (m.status === "ok" ? "Inside normal operating envelope" : "");
      return `<button class="plate st-${esc(m.status)}" data-id="${esc(m.id)}" aria-pressed="${m.id === state.selected}">
        <div class="plate-tag"><span class="plate-name">${esc(m.name)}</span>${stateChip(m.status)}</div>
        <div class="plate-line">${esc(m.line)} · ${esc(m.model)}</div>
        <div class="plate-body"><div class="health num">${m.health}<small>health</small></div>${sparkline(m.series.vibration_rms_mm_s, vib?.limits)}</div>
        <div class="plate-foot">${esc(top)}</div></button>`;
    }).join("");
    $$(".plate").forEach((p) => p.addEventListener("click", () => { state.selected = p.dataset.id; renderPlates(); renderDetail(); }));
  }
  function renderDetail() {
    const m = state.machines.find((x) => x.id === state.selected);
    if (!m) return;
    const show = ["vibration_rms_mm_s", "motor_temp_c", "current_a", "reject_rate_pct"];
    const charts = show.map((name) => {
      const ch = m.channels.find((c) => c.name === name);
      if (!ch || !m.series[name]) return "";
      return `<div class="chart"><h3>${esc(ch.label)} <span class="num">${ch.last} ${esc(ch.unit)}</span></h3>${lineChart(m.series.t, m.series[name], ch)}</div>`;
    }).join("");
    const fc = m.forecast;
    const fcBlock = fc ? `<div class="forecast"><span class="muted">Vibration forecast</span>
      <strong class="num">${fc.hours_to_trip === 0 ? "At trip level" : fc.hours_to_alarm === 0 ? (fc.hours_to_trip != null ? `${fc.hours_to_trip} h to trip` : "In alarm") : fc.hours_to_alarm != null ? `${fc.hours_to_alarm} h to alarm` : "No crossing in 72 h"}</strong>
      <span class="muted">${esc(fc.model)}</span></div>` : "";
    $("#machineDetail").innerHTML = `
      <div class="panel charts">
        <div class="charts-head"><h2>${esc(m.name)} · last 8 hours</h2><span class="muted">Dashed lines: warning and alarm limits from the manual</span></div>
        ${charts}
      </div>
      <aside class="panel side">
        <h2>What the sensors say</h2>
        <ul class="findings">${m.findings.length ? m.findings.map((f) => `<li class="${esc(f.severity)}">${esc(f.text)}</li>`).join("") : `<li>All channels inside the normal operating envelope.</li>`}</ul>
        ${fcBlock}
        <dl class="kv"><dt>Manual</dt><dd>${esc(m.manual)}</dd><dt>Last service</dt><dd>${esc(m.last_service)}</dd></dl>
        <button class="btn primary big" id="inspectThis" type="button">Inspect ${esc(m.name)}</button>
        <a class="btn" href="/api/machines/${esc(m.id)}/log.csv" download>Download sensor log (CSV)</a>
      </aside>`;
    $("#inspectThis").addEventListener("click", () => {
      $("#machineSelect").value = m.name;
      selectSample(m.sample);
      setTab("inspect");
    });
  }
  function fillMachineSelects() {
    const sel = $("#machineSelect");
    if (!sel.options.length) {
      sel.innerHTML = state.machines.map((m) => `<option>${esc(m.name)}</option>`).join("");
      sel.addEventListener("change", markSuggested);
      const cm = $("#chatMachine");
      cm.innerHTML += state.machines.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
      markSuggested();
    }
  }

  /* ---------------- inspect ---------------- */
  async function loadSamples() {
    state.samples = await api("/api/samples");
    $("#samples").innerHTML = state.samples.map((s) => `<button type="button" data-name="${esc(s.name)}" aria-pressed="false" title="${esc(s.label)}">
      <img src="${esc(s.url)}" alt="${esc(s.label)}" loading="lazy"></button>`).join("");
    $$("#samples button").forEach((b) => b.addEventListener("click", () => selectSample(b.dataset.name)));
    selectSample("conveyor_c2_scratch.png");
  }
  function selectSample(name) {
    state.sample = name; state.file = null; $("#imageInput").value = "";
    $$("#samples button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.name === name)));
    const d = $("#drop"); d.classList.remove("has");
    $("#dropText").innerHTML = "Drop a photo here or <u>browse</u>";
    markSuggested();
  }
  function markSuggested() {
    const m = state.machines.find((x) => x.name === $("#machineSelect").value);
    $$("#samples button").forEach((b) => b.classList.toggle("suggested", !!m && b.dataset.name === m.sample));
  }
  function setFile(f) {
    if (!f) return;
    state.file = f; state.sample = null;
    $$("#samples button").forEach((b) => b.setAttribute("aria-pressed", "false"));
    $("#drop").classList.add("has");
    $("#dropText").textContent = `${f.name} · ${(f.size / 1024).toFixed(0)} KB`;
  }
  $("#imageInput").addEventListener("change", (e) => setFile(e.target.files[0]));
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));
  $$('input[name="logsrc"]').forEach((r) => r.addEventListener("change", () => {
    const up = $('input[name="logsrc"]:checked').value === "upload";
    if (up) $("#logsInput").click();
  }));
  $("#logsInput").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) toast(`Sensor log attached: ${f.name}`);
  });

  const ICON = {
    done: '<svg viewBox="0 0 12 12"><path d="M2 6.5 5 9l5-6" stroke="currentColor" stroke-width="2" fill="none"/></svg>',
    warn: '<svg viewBox="0 0 12 12"><path d="M6 2v5M6 9.2v.6" stroke="currentColor" stroke-width="2"/></svg>',
    error: '<svg viewBox="0 0 12 12"><path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" stroke-width="2"/></svg>',
  };
  let t0 = 0;
  function upsertStep(ev) {
    const list = $("#trace");
    let li = $(`#step-${ev.id}`, list);
    if (!li) {
      li = document.createElement("li"); li.id = `step-${ev.id}`; li.className = "step";
      list.appendChild(li);
    }
    li.className = `step ${ev.status}`;
    let extra = "";
    if (ev.data?.checks) {
      extra = `<ul class="checks">${ev.data.checks.map((c) => `<li class="${c.pass ? "" : "fail"}"><b>${c.pass ? "✓" : "✕"}</b><span>${esc(c.name)} · ${esc(c.detail)}</span></li>`).join("")}</ul>`;
    }
    const secs = ((ev.ts * 1000 - t0) / 1000).toFixed(1);
    li.innerHTML = `<span class="dot">${ICON[ev.status] || ""}</span>
      <div class="step-title">${esc(ev.title)} <span class="step-tool">${esc(ev.tool)}</span><span class="step-time">${ev.status === "running" ? "" : `+${secs}s`}</span></div>
      ${ev.detail ? `<div class="step-detail">${esc(ev.detail)}</div>` : ""}${extra}`;
  }

  $("#intake").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.running) return;
    const form = e.target;
    const fd = new FormData();
    fd.append("machine", form.machine.value);
    fd.append("note", form.note.value);
    fd.append("language", form.language.value);
    if (state.file) fd.append("image", state.file);
    else if (state.sample) fd.append("sample", state.sample);
    const src = $('input[name="logsrc"]:checked').value;
    fd.append("live_logs", src === "live" ? "true" : "false");
    if (src === "upload" && $("#logsInput").files[0]) fd.append("logs", $("#logsInput").files[0]);

    state.running = true;
    const btn = $("#runBtn"); btn.disabled = true; btn.textContent = "Diagnosing…";
    $("#trace").innerHTML = ""; $("#tokens").hidden = true; $("#tokens").textContent = "";
    $("#traceMeta").textContent = "";
    $("#result").innerHTML = `<div class="result-empty"><p>Working on it…</p></div>`;
    t0 = Date.now();
    try {
      await sse("/api/diagnose/stream", { method: "POST", body: fd }, (ev) => {
        if (ev.type === "start") $("#traceMeta").textContent = ev.backend;
        else if (ev.type === "step") upsertStep(ev);
        else if (ev.type === "token") { const tk = $("#tokens"); tk.hidden = false; tk.textContent += ev.text; tk.scrollTop = tk.scrollHeight; }
        else if (ev.type === "result") renderResult(ev);
        else if (ev.type === "error") throw new Error(ev.message);
      });
    } catch (err) {
      $("#result").innerHTML = `<div class="result-empty"><p><strong>Diagnosis stopped.</strong> ${esc(err.message)}</p><p>Check the photo and sensor file, then run it again.</p></div>`;
    } finally {
      state.running = false; btn.disabled = false; btn.textContent = "Run diagnosis";
      refreshBadge();
    }
  });

  function renderResult(r) {
    const d = r.diagnosis, wo = r.work_order;
    const floor = (state.status?.confidence_floor ?? 0.6) * 100;
    const ev = d.evidence.map((x) => `<li><span class="src">${esc(x.source)}</span><span>${esc(x.text)}</span></li>`).join("");
    const hitMap = Object.fromEntries((r.manual_hits || []).map((h) => [h.source, h]));
    const cites = d.manual_citations.map((c) => `<span class="cite" title="${esc(hitMap[c]?.text || "")}">${esc(c)}</span>`).join("");
    const safety = d.safety_notes?.length ? `<div class="rsec"><div class="safety-note">${warnSign()}<div>${d.safety_notes.map(esc).join("<br>")}</div></div></div>` : "";
    const woBlock = wo ? `<div class="wo-card"><div><span class="muted">Work order</span><br><strong>${esc(wo.id)}</strong> ${sevChip(wo.priority)}
        <div class="muted">${wo.status === "awaiting_approval" ? `Held for your approval · ${esc(wo.escalate_reason)}` : `Dispatched to ${esc(wo.assigned_to)} via ${esc(wo.dispatch_channel)}`}</div></div>
        <button class="btn ${wo.status === "awaiting_approval" ? "approve" : ""}" type="button" data-open="${esc(wo.id)}">${wo.status === "awaiting_approval" ? "Review and approve" : "Open work order"}</button></div>`
      : `<div class="wo-card"><div><strong>No work order needed</strong><div class="muted">Nothing abnormal found.</div></div></div>`;
    const tele = r.telemetry || {};
    const teleLine = tele.tokens_per_s ? `${tele.tokens_out} tokens · ${tele.tokens_per_s} tok/s · TTFT ${tele.ttft_s}s on ${esc(tele.backend)}` : esc(tele.backend || "");
    $("#result").innerHTML = `
      <div class="verdict sev-${esc(d.severity)}">
        <div class="verdict-row">${d.defect_detected ? sevChip(d.severity) : `<span class="state">No defect</span>`}
          ${d.defect_detected ? `<span class="state">${esc(d.defect_type.replace(/_/g, " "))}</span>` : ""}
          ${d.needs_human ? `<span class="state warn">Human approval required</span>` : ""}
          ${d.estimated_downtime_min ? `<span class="state">~${d.estimated_downtime_min} min downtime</span>` : ""}</div>
        <h2>${esc(d.summary)}</h2>
        <div class="conf"><span class="conf-bar" style="--floor:${floor}%"><i style="width:${Math.round(d.confidence * 100)}%"></i></span>
          <span class="num">Confidence ${Math.round(d.confidence * 100)}%</span><span class="muted">· approval floor ${floor}%</span></div>
      </div>
      ${r.overlay ? `<img class="overlay" src="${r.overlay}" alt="Photo with detected regions highlighted">` : ""}
      ${d.defect_detected ? `<div class="rsec"><h3>Likely root cause</h3><p>${esc(d.likely_root_cause)}</p></div>` : ""}
      <div class="rsec"><h3>Recommended actions</h3><ol class="actions">${d.recommended_actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ol></div>
      ${safety}
      <div class="rsec"><h3>Evidence</h3><ul class="evidence">${ev}</ul></div>
      ${cites ? `<div class="rsec"><h3>Manual sources</h3><div class="cites">${cites}</div></div>` : ""}
      ${woBlock}
      <div class="rsec muted" style="font-size:12.5px">Finished in ${r.elapsed_s}s · ${teleLine}</div>`;
    const open = $("[data-open]", $("#result"));
    if (open) open.addEventListener("click", () => { state.orderId = open.dataset.open; setTab("orders"); });
    loadFleet();
  }
  function warnSign() {
    return '<svg viewBox="0 0 24 21" aria-label="Safety"><path d="M12 1 23 20H1z" fill="#F2C230" stroke="#1D2321" stroke-width="1.5"/><path d="M12 8v6M12 16.2v1.4" stroke="#1D2321" stroke-width="2.2"/></svg>';
  }

  /* ---------------- orders ---------------- */
  async function loadOrders() {
    try {
      state.orders = await api("/api/workorders");
      renderOrders();
      if (state.orderId) openOrder(state.orderId);
    } catch (e) { console.warn(e); }
    refreshBadge();
  }
  function renderOrders() {
    const f = $('input[name="of"]:checked').value;
    const rows = state.orders.filter((o) => f === "all" || o.status === f);
    $("#orderList").innerHTML = rows.length ? rows.map((o) => `<button class="order-row" data-id="${esc(o.id)}" aria-current="${o.id === state.orderId}">
        <span class="t">${esc(o.id)} · ${esc(o.title)}</span>
        <span class="s">${sevChip(o.priority)}</span>
        <span class="m">${esc(o.machine)} · ${esc(o.assigned_to)} · ${fmtTime(o.created_at)}</span>
        <span class="s"><span class="state ${o.status === "awaiting_approval" ? "warn" : ""}">${esc(ORDER_STATUS[o.status] || o.status)}</span></span>
      </button>`).join("")
      : `<div class="order-empty">${state.orders.length ? "No orders in this view." : "No work orders yet. Run a diagnosis on the Inspect tab and the agent will raise one here."}</div>`;
    $$(".order-row").forEach((b) => b.addEventListener("click", () => openOrder(b.dataset.id)));
  }
  $$('input[name="of"]').forEach((r) => r.addEventListener("change", renderOrders));

  async function openOrder(id) {
    state.orderId = id;
    $$(".order-row").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === id)));
    let o;
    try { o = await api(`/api/workorders/${encodeURIComponent(id)}`); } catch (_) { return; }
    const d = o.diagnosis || {};
    const actionsBar = o.status === "awaiting_approval"
      ? `<input id="woComment" placeholder="Comment for the technician (optional)"><button class="btn approve" data-act="approve" type="button">Approve and dispatch</button><button class="btn danger" data-act="reject" type="button">Reject</button>`
      : o.status === "dispatched" ? `<input id="woComment" placeholder="Closing note (optional)"><button class="btn approve" data-act="complete" type="button">Mark as done</button>`
      : `<button class="btn" data-act="reopen" type="button">Reopen</button>`;
    $("#orderDetail").innerHTML = `
      <div class="head">
        <div class="verdict-row">${sevChip(o.priority)}<span class="state ${o.status === "awaiting_approval" ? "warn" : ""}">${esc(ORDER_STATUS[o.status] || o.status)}</span>
          <span class="state">SLA ${o.sla_h} h</span>${o.est_downtime_min ? `<span class="state">~${o.est_downtime_min} min downtime</span>` : ""}</div>
        <h2>${esc(o.id)} · ${esc(o.title)}</h2>
        <p>${esc(o.summary)}</p>
        ${o.escalate ? `<p class="muted">Escalated because: ${esc(o.escalate_reason)}</p>` : ""}
        <dl class="kv"><dt>Machine</dt><dd>${esc(o.machine)}</dd><dt>Technician</dt><dd>${esc(o.assigned_to)}</dd>
          <dt>Dispatch</dt><dd>${esc(o.dispatch_channel || "not yet dispatched")}${o.evolus_ref ? ` · run ${esc(o.evolus_ref)}` : ""}</dd>
          <dt>Confidence</dt><dd class="num">${Math.round((d.confidence || 0) * 100)}%</dd></dl>
      </div>
      ${o.image_overlay ? `<img class="overlay" src="${o.image_overlay}" alt="Inspection photo with highlighted defect">` : ""}
      <div class="rsec"><h3>Root cause</h3><p>${esc(o.root_cause)}</p></div>
      <div class="rsec"><h3>Technician checklist</h3><ul class="checklist">${o.actions.map((a, i) => `<li><label><input type="checkbox" id="ck${i}"><span>${esc(a)}</span></label></li>`).join("")}</ul></div>
      ${d.safety_notes?.length ? `<div class="rsec"><div class="safety-note">${warnSign()}<div>${d.safety_notes.map(esc).join("<br>")}</div></div></div>` : ""}
      ${o.citations.length ? `<div class="rsec"><h3>Manual sources</h3><div class="cites">${o.citations.map((c) => `<span class="cite">${esc(c)}</span>`).join("")}</div></div>` : ""}
      <div class="rsec"><h3>History</h3><ul class="history">${o.history.map((h) => `<li><time>${fmtTime(h.ts)}</time>${esc(h.event)}</li>`).join("")}</ul></div>
      <div class="bar">${actionsBar}</div>`;
    $$("[data-act]", $("#orderDetail")).forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api(`/api/workorders/${encodeURIComponent(o.id)}/${b.dataset.act}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ by: "Shift supervisor", comment: $("#woComment")?.value || "" }),
        });
        toast({ approve: `${o.id} approved and dispatched`, reject: `${o.id} rejected`, complete: `${o.id} closed`, reopen: `${o.id} reopened` }[b.dataset.act]);
        loadOrders();
      } catch (e) { toast(`Could not update ${o.id}: ${e.message}`); b.disabled = false; }
    }));
  }
  async function refreshBadge() {
    try {
      const orders = state.tab === "orders" ? state.orders : await api("/api/workorders");
      const n = orders.filter((o) => o.status === "awaiting_approval").length;
      const b = $("#approvalBadge"); b.hidden = !n; b.textContent = n;
    } catch (_) { /* ignore */ }
  }

  /* ---------------- copilot ---------------- */
  function addMsg(role, text) {
    const m = document.createElement("div"); m.className = `msg ${role}`; m.textContent = text;
    $("#messages").appendChild(m); $("#messages").scrollTop = 1e9; return m;
  }
  async function ask(q) {
    if (!q.trim()) return;
    $("#suggest").hidden = true;
    addMsg("user", q);
    const bot = addMsg("bot", "");
    const textNode = document.createTextNode(""); bot.appendChild(textNode);
    let sources = [], answer = "";
    try {
      await sse("/api/copilot", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, machine_id: $("#chatMachine").value || null, history: state.chat }) }, (ev) => {
        if (ev.type === "sources") sources = ev.hits;
        else if (ev.type === "token") { answer += ev.text; textNode.nodeValue = answer; $("#messages").scrollTop = 1e9; }
        else if (ev.type === "done") {
          const meta = document.createElement("span"); meta.className = "meta";
          const tl = ev.telemetry || {};
          meta.innerHTML = `${sources.map((s) => `<span class="cite" title="${esc(s.title)}">${esc(s.source)}</span>`).join("")}<br>${esc(tl.backend || "")}${tl.tokens_per_s ? ` · ${tl.tokens_per_s} tok/s` : ""}`;
          bot.appendChild(meta);
        }
      });
      state.chat.push({ role: "user", content: q }, { role: "assistant", content: answer });
    } catch (e) { textNode.nodeValue = `Copilot could not answer: ${e.message}`; }
  }
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#question").value; $("#question").value = ""; ask(q); });
  $$("#suggest button").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));

  /* ---------------- system ---------------- */
  async function loadSystem() {
    await loadStatus();
    const s = state.status; if (!s) return;
    const ep = s.llm.endpoint || {};
    $("#sysModel").innerHTML = `<h2>Reasoning model</h2><dl class="kv">
      <dt>Mode</dt><dd>${s.llm.amd ? "AMD vLLM endpoint" : "Offline demo reasoner"}</dd>
      <dt>Model</dt><dd>${esc(s.llm.model)}</dd><dt>Hardware</dt><dd>${esc(s.llm.hardware)}</dd>
      <dt>Endpoint</dt><dd>${s.llm.amd ? (ep.reachable ? `reachable · ${ep.ms} ms · serves ${esc((ep.models || []).join(", "))}` : `unreachable · ${esc(ep.reason)}`) : "not configured (set VLLM_BASE_URL)"}</dd>
      <dt>Approval floor</dt><dd>${Math.round(s.confidence_floor * 100)}% confidence</dd></dl>`;
    const b = s.evolus.blocks;
    const calls = s.evolus.recent_calls || [];
    $("#sysEvolus").innerHTML = `<h2>Evolus building blocks</h2><dl class="kv">
      <dt>Knowledge library</dt><dd>${b.knowledge_library ? "live" : "local BM25 fallback"}</dd>
      <dt>Work-order workflow</dt><dd>${b.workflow ? "live" : "local queue fallback"}</dd>
      <dt>Copilot agent</dt><dd>${b.chat_agent ? "live (OpenAI-compatible endpoint)" : "direct model / offline"}</dd></dl>
      ${calls.length ? `<table><thead><tr><th>Block</th><th>Path</th><th>Status</th><th>ms</th></tr></thead><tbody>${calls.map((c) => `<tr><td>${esc(c.block)}</td><td><code>${esc(c.path)}</code></td><td>${esc(c.status ?? "error")}</td><td>${c.ms}</td></tr>`).join("")}</tbody></table>` : `<p class="muted" style="margin-top:10px">No Evolus API calls yet.</p>`}`;
    const m = await api("/api/metrics");
    const L = m.llm;
    $("#sysMetrics").innerHTML = `<h2>Inference this session</h2>
      ${L.calls ? `<div class="stat-row"><div class="stat"><b class="num">${L.calls}</b><span>model calls</span></div>
        ${L.simulated ? "" : `<div class="stat"><b class="num">${L.avg_ttft_s ?? "–"}s</b><span>avg time to first token</span></div>
        <div class="stat"><b class="num">${L.avg_tokens_per_s ?? "–"}</b><span>avg decode tok/s</span></div>`}
        ${L.simulated ? `<div class="stat"><b>Offline</b><span>connect the AMD endpoint to measure TTFT and tok/s</span></div>` : `<div class="stat"><b class="num">${L.avg_latency_s ?? "–"}s</b><span>avg end-to-end</span></div>`}</div>
        <table><thead><tr><th>Time</th><th>Purpose</th><th>Backend</th><th>TTFT s</th><th>Total s</th><th>Tokens</th><th>tok/s</th></tr></thead><tbody>
        ${L.recent.map((r) => `<tr><td>${fmtTime(r.ts)}</td><td>${esc(r.purpose || "")}</td><td>${esc(r.backend)}</td><td>${r.ttft_s ?? "–"}</td><td>${r.latency_s ?? "–"}</td><td>${r.tokens_out ?? "–"}</td><td>${r.tokens_per_s ?? "–"}</td></tr>`).join("")}</tbody></table>`
        : `<p class="muted">No model calls yet. Run a diagnosis or ask the copilot.</p>`}`;
    $("#sysBench").innerHTML = `<h2>Throughput benchmark on AMD</h2>${m.benchmarks.length
      ? `<table><thead><tr><th>Concurrency</th><th>Output tok/s</th><th>Total tok/s</th><th>Req/s</th><th>Mean TTFT ms</th><th>P99 TTFT ms</th><th>Mean TPOT ms</th></tr></thead><tbody>
        ${m.benchmarks.map((b) => `<tr><td>${b.concurrency ?? "–"}</td><td>${fx(b.output_tok_s)}</td><td>${fx(b.total_tok_s)}</td><td>${fx(b.req_s, 2)}</td><td>${fx(b.mean_ttft_ms)}</td><td>${fx(b.p99_ttft_ms)}</td><td>${fx(b.mean_tpot_ms, 1)}</td></tr>`).join("")}</tbody></table>`
      : `<p class="muted">No benchmark results yet. Run <code>bash infra/amd/bench.sh</code> on the droplet and copy the JSON files into <code>docs/benchmarks/</code>.</p>`}`;
  }
  const fx = (v, d = 0) => (v == null ? "–" : Number(v).toFixed(d));
  $("#resetDemo").addEventListener("click", async () => {
    await api("/api/demo/reset", { method: "POST" });
    state.orderId = null; toast("Demo data reset"); loadFleet(); refreshBadge(); loadSystem();
  });

  /* ---------------- boot ---------------- */
  async function boot() {
    await Promise.all([loadStatus(), loadFleet()]);
    await loadSamples();
    refreshBadge();
    const h = location.hash.slice(1);
    if (["floor", "inspect", "orders", "copilot", "system"].includes(h)) setTab(h);
    setInterval(() => { if (state.tab === "floor") loadFleet(); }, 4000);
    setInterval(refreshBadge, 6000);
    setInterval(loadStatus, 30000);
  }
  boot();
})();
