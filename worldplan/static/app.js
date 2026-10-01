"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 저장 못 해도 화면은 돈다 */ } },
  };

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v;
      else if (k === "style") Object.assign(n.style, v);
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v === true) n.setAttribute(k, "");
      else if (v !== false && v !== null && v !== undefined) n.setAttribute(k, v);
    }
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return n;
  }

  // ---- 요청: 한 벌의 상태(state)를 폼과 JSON 칸이 같이 본다. 폼에 없는 칸(휴일·체류·바쁜 시간)은 그대로 남는다 ----
  let state = null;
  const ZONES = (() => {
    try { if (Intl.supportedValuesOf) return Intl.supportedValuesOf("timeZone"); } catch (e) { /* 옛 브라우저 */ }
    return ["UTC", "Asia/Seoul", "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata", "Europe/London", "Europe/Berlin",
      "America/New_York", "America/Chicago", "America/Los_Angeles", "America/Sao_Paulo", "Australia/Sydney"];
  })();

  async function loadSample() {
    const inline = $("sample-data");
    let s;
    if (inline) s = JSON.parse(inline.textContent);
    else s = await (await fetch("/sample.json")).json();
    setState(s);
  }

  function setState(s) {
    state = s;
    state.participants = state.participants || [];
    state.events = state.events || [];
    state.horizon = state.horizon || {};
    $("req").value = JSON.stringify(state, null, 2);
    drawForm();
    drawClock();
  }

  function syncJson() { $("req").value = JSON.stringify(state, null, 2); }

  function readReq() {
    try { return JSON.parse($("req").value); }
    catch (e) { throw new Error("요청 JSON 을 못 읽는다: " + e.message); }
  }

  function field(label, value, onchange, attrs) {
    const inp = el("input", Object.assign({ value: value === undefined || value === null ? "" : value }, attrs || {}));
    inp.addEventListener("change", () => { onchange(inp.value); syncJson(); });
    return el("label", {}, label, inp);
  }

  function ids() { return state.participants.map((p) => p.id).filter(Boolean); }

  function personRow(p, i) {
    p.work = p.work || {};
    return el("div", { class: "row" },
      field("이름", p.id, (v) => { const old = p.id; p.id = v.trim(); renameRefs(old, p.id); drawEvents(); }, { required: true }),
      field("시간대", p.tz, (v) => { p.tz = v.trim(); drawClock(); }, { list: "zones", required: true }),
      field("일 시작", p.work.start, (v) => { p.work.start = v; }, { type: "time" }),
      field("일 끝", p.work.end, (v) => { p.work.end = v; }, { type: "time" }),
      field("선호 시작", p.core && p.core.start, (v) => { setCore(p, "start", v); }, { type: "time" }),
      field("선호 끝", p.core && p.core.end, (v) => { setCore(p, "end", v); }, { type: "time" }),
      el("button", { type: "button", class: "x", "aria-label": `${p.id || "이 사람"} 빼기`,
        onclick: () => { const id = p.id; state.participants.splice(i, 1); renameRefs(id, null); syncJson(); drawForm(); drawClock(); } }, "×"));
  }

  function setCore(p, k, v) {
    if (!v) { if (p.core) delete p.core[k]; if (p.core && !p.core.start && !p.core.end) delete p.core; return; }
    p.core = p.core || {}; p.core[k] = v;
  }

  function renameRefs(old, neu) {
    if (!old) return;
    for (const e of state.events) for (const k of ["required", "optional"]) {
      if (!e[k]) continue;
      e[k] = e[k].map((x) => (x === old ? neu : x)).filter(Boolean);
    }
  }

  function chips(e, who) {
    const box = el("div", { class: "chips" }, el("span", {}, "누가:"));
    for (const id of ids()) {
      const role = (e.required || []).includes(id) ? "required" : (e.optional || []).includes(id) ? "optional" : "";
      const sel = el("select", { "aria-label": `${e.title || e.id} 에서 ${id}` },
        el("option", { value: "" }, "안 옴"), el("option", { value: "required" }, "필수"), el("option", { value: "optional" }, "선택"));
      sel.value = role;
      sel.addEventListener("change", () => {
        e.required = (e.required || []).filter((x) => x !== id);
        e.optional = (e.optional || []).filter((x) => x !== id);
        if (sel.value) e[sel.value].push(id);
        if (!e.optional.length) delete e.optional;
        syncJson();
      });
      box.append(el("label", { class: "chip" }, id, sel));
    }
    return box;
  }

  function eventRow(e, i) {
    return el("div", { class: "row" },
      field("제목", e.title, (v) => { e.title = v; if (!e.id) e.id = slug(v, i); }, { required: true }),
      field("길이(분)", e.duration_minutes, (v) => { e.duration_minutes = Number(v); }, { type: "number", min: 5, max: 1440 }),
      field("먼저 끝날 것(쉼표)", (e.after || []).join(", "), (v) => {
        const a = v.split(",").map((s) => s.trim()).filter(Boolean); if (a.length) e.after = a; else delete e.after;
      }),
      el("button", { type: "button", class: "x", "aria-label": `${e.title || "이 일정"} 빼기`,
        onclick: () => { state.events.splice(i, 1); syncJson(); drawEvents(); } }, "×"),
      chips(e));
  }

  function slug(s, i) { return (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "e" + (i + 1); }

  function drawEvents() { $("events").replaceChildren(...state.events.map(eventRow)); }

  function drawForm() {
    $("people").replaceChildren(...state.participants.map(personRow));
    drawEvents();
    const h = state.horizon;
    const s = Date.parse(h.start), e = Date.parse(h.end);
    if (s) $("h-start").value = new Date(s).toISOString().slice(0, 10);
    if (s && e > s) $("h-days").value = Math.max(1, Math.round((e - s) / 86400000));
    $("h-slot").value = state.slot_minutes || 15;
    $("h-buf").value = state.buffer_minutes || 0;
  }

  function horizonFromForm() {
    const d = $("h-start").value, n = Number($("h-days").value) || 1;
    if (d) {
      const s = Date.parse(d + "T00:00Z");
      state.horizon = { start: d + "T00:00Z", end: new Date(s + n * 86400000).toISOString().slice(0, 16) + "Z" };
    }
    state.slot_minutes = Number($("h-slot").value) || 15;
    state.buffer_minutes = Number($("h-buf").value) || 0;
    syncJson();
  }

  // ---- 세계 시계: 서버 없이 브라우저의 시간대 표로 -- 요청 속 사람들의 시간대 ----
  function drawClock() {
    const zones = [...new Set((state ? state.participants : []).map((p) => p.tz).filter(Boolean))];
    if (!zones.length) zones.push("UTC");
    const now = new Date();
    const items = zones.map((z) => {
      let t = "?";
      try { t = new Intl.DateTimeFormat("ko-KR", { timeZone: z, hour: "2-digit", minute: "2-digit", weekday: "short", hour12: false }).format(now); }
      catch (e) { t = "알 수 없는 시간대"; }
      return el("li", {}, el("span", {}, z.split("/").pop().replace(/_/g, " ") + " "), el("b", {}, t));
    });
    $("clock").replaceChildren(...items);
  }

  async function call(path, body) {
    const headers = { "Content-Type": "application/json" };
    const tok = $("token").value.trim();
    if (tok) headers.Authorization = "Bearer " + tok;
    const r = await fetch(path, body === undefined
      ? { headers } : { method: "POST", headers, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({ error: "응답이 JSON 이 아니다" }));
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    return data;
  }

  function list(title, items, cls) {
    if (!items || !items.length) return null;
    return el("div", {}, el("h3", {}, title),
      el("ul", { class: "plain" }, items.map((x) => el("li", { class: cls || "" }, x))));
  }

  function timeline(req, plan) {
    if (!req || !req.horizon || !plan) return null;
    const h0 = Date.parse(req.horizon.start), h1 = Date.parse(req.horizon.end);
    if (!(h1 > h0)) return null;
    const pct = (t) => (100 * (t - h0) / (h1 - h0)).toFixed(3) + "%";
    const people = (req.participants || []).map((p) => p.id);
    const box = el("div", { class: "tl", role: "img", "aria-label": "UTC 타임라인" });
    const days = [];
    for (let t = h0; t <= h1; t += 86400000) days.push(t);
    for (const pid of people) {
      const row = el("div", { class: "tl-row" }, el("span", { class: "tl-label" }, pid));
      for (const d of days) row.append(el("div", { class: "tl-day", style: { left: pct(d) } }));
      for (const [eid, ev] of Object.entries(plan)) {
        const who = (ev.people || []).find((x) => x.participant === pid);
        if (!who) continue;
        const s = Date.parse(ev.start_utc), e = Date.parse(ev.end_utc);
        const opt = who.role === "optional";
        if (opt && !who.available) continue;
        row.append(el("div", {
          class: "tl-bar" + (opt ? " opt" : ""),
          style: { left: pct(s), width: (100 * (e - s) / (h1 - h0)).toFixed(3) + "%" },
          title: `${ev.title} · ${who.local_start || ""} (${who.tz || ""})`,
        }));
      }
      box.append(row);
    }
    const axis = el("div", { class: "tl-axis" },
      days.map((d) => el("span", {}, new Date(d).toISOString().slice(5, 10))));
    return el("div", {}, el("h3", {}, "타임라인 (UTC, 칸 = 하루)"), box, axis);
  }

  function planTable(plan) {
    if (!plan) return null;
    const rows = [];
    for (const [eid, ev] of Object.entries(plan)) {
      for (const p of ev.people) {
        const bad = p.role === "required" ? !p.ok : !p.available;
        rows.push(el("tr", {},
          el("td", {}, ev.title || eid), el("td", {}, p.participant),
          el("td", { class: "dim" }, p.role === "required" ? "필수" : "선택"),
          el("td", { class: bad ? "x" : "" }, (p.local_start || "").replace("T", " ")),
          el("td", { class: "dim" }, p.tz || ""),
          el("td", { class: bad ? "x" : "dim" },
            p.why || (p.role === "optional" ? (p.available ? "가능" : "불가") :
              (p.outside_core_minutes ? `선호 밖 ${p.outside_core_minutes}분` : "")))));
      }
    }
    return el("div", { class: "tblwrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["일정", "참가자", "", "현지 시작", "시간대", "비고"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows)));
  }

  function render(res, req) {
    const box = $("out");
    box.replaceChildren();
    const out = { append: (...xs) => { for (const x of xs) if (x) box.append(x); } };
    const ok = res.verdict === "ACCEPT";
    if (res.verdict) out.append(el("p", {}, el("span", { class: "badge " + (ok ? "ok" : "bad") }, res.verdict), " ", res.reason || ""));
    if (res.input_errors) out.append(list("입력 오류", res.input_errors, "v"));
    if (res.search) {
      out.append(el("div", { class: "note" }, res.search.optimality));
    }
    if (res.note) out.append(el("div", { class: "note" }, res.note));
    if (res.warning) out.append(el("div", { class: "note" }, res.warning));
    const kv = [];
    if (res.cost) kv.push(["비용", `${res.cost.total} (선호 밖 ${res.cost.outside_core_minutes}분 · 못 오는 선택 참가자 ${res.cost.optional_missing})`
      + (res.cost.solver_total !== undefined ? ` · 생성자 ${res.cost.solver_total}` : "")]);
    if (res.search) kv.push(["탐색", `${res.search.status} · 노드 ${res.search.nodes} · ${res.search.elapsed_s}s`]);
    if (res.tzdata) kv.push(["tz 데이터", res.tzdata]);
    if (res.ledger) kv.push(["원장", `#${res.ledger.seq} · ${res.ledger.hash.slice(0, 16)}…`]);
    if (kv.length) out.append(el("dl", { class: "kv" }, kv.map(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])));
    if (res.judge) out.append(list("심판 위반", res.judge.violations.map((v) => `[${v.check}] ${v.message}`), "v"));
    if (res.plan) { out.append(timeline(req, res.plan)); out.append(el("h3", {}, "참가자별 현지 시각")); out.append(planTable(res.plan)); }
    if (res.diagnostics) out.append(list("왜 못 넣었나", res.diagnostics.map((d) =>
      [d.event ? `'${d.event}': ` : "", d.why, d.participants ? ` (${d.participants.join(", ")})` : "",
        d.conflicting_pairs ? ` 겹치지 않는 쌍: ${d.conflicting_pairs.map((p) => p.join("–")).join(", ")}` : ""].join(""))));
    if (res.best_partial) { out.append(el("h3", {}, `부분 계획(참고, 성공 아님) -- 못 넣음: ${res.best_partial.unscheduled.join(", ")}`)); out.append(planTable(res.best_partial.plan)); }
    if (res.slots) {
      out.append(el("h3", {}, `후보 ${res.count}개 (${res.ranking})`));
      out.append(el("div", { class: "tblwrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ["UTC 시작", "비용", "현지 시각"].map((h) => el("th", {}, h)))),
        el("tbody", {}, res.slots.map((s) => el("tr", {},
          el("td", {}, s.start_utc.replace("T", " ")), el("td", {}, s.cost.total),
          el("td", { class: "dim" }, s.people.map((p) => `${p.participant} ${(p.local_start || "").slice(11, 16)}`).join(" · "))))))));
    }
    if (res.chain) {
      out.append(el("p", {}, el("span", { class: "badge " + (res.chain.ok ? "ok" : "bad") }, res.chain.ok ? "사슬 성함" : "사슬 끊김"),
        ` 항목 ${res.chain.entries}` + (res.chain.why ? ` -- ${res.chain.why}` : "")));
      out.append(list("최근 판정", res.recent.slice().reverse().map((r) =>
        `#${r.seq} ${r.time} ${r.kind} ${r.body.verdict} ${r.body.status || ""} 입력 ${String(r.body.input_sha256).slice(0, 10)}…`)));
    }
    out.append(list("이 판정이 무효가 되는 조건", res.invalidated_if));
    out.append(list("심판이 재지 않은 것", res.not_checked));
    out.append(el("details", {}, el("summary", {}, "원본 JSON"), el("pre", { class: "raw" }, JSON.stringify(res, null, 2))));
  }

  async function run(fn, box) {
    const all = document.querySelectorAll("button");
    all.forEach((b) => (b.disabled = true));
    try { await fn(); }
    catch (e) { (box || $("out")).replaceChildren(el("p", {}, el("span", { class: "badge bad" }, "오류"), " ", e.message)); }
    finally { all.forEach((b) => (b.disabled = false)); }
  }

  // ---- 물어보기: /api/assistant -- 앞단(WALP)이 답했는지, Claude 까지 갔는지, 토큰 · 시간을 같이 보인다 ----
  async function ask(q) {
    const li = el("li", {}, el("p", { class: "q" }, q), el("p", { class: "a" }, "…"));
    $("ask-log").prepend(li);
    try {
      const t0 = performance.now();
      const r = await call("/api/assistant", { q, request: state });
      const ms = Math.round(performance.now() - t0);
      li.querySelector(".a").textContent = r.answer || "(빈 답)";
      const tok = r.tokens ? ` · 토큰 ${r.tokens.total.toLocaleString()}` : " · 토큰 0";
      li.append(el("p", { class: "meta" }, (r.by === "walp" ? "앞단(WALP)" : "Claude") + (r.route ? ` · ${r.route}` : "") + tok + ` · ${ms}ms`));
      if (r.request) { setState(r.request); }
      if (r.result) render(r.result, r.request || state);
    } catch (e) {
      li.querySelector(".a").textContent = "오류: " + e.message;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const dl = el("datalist", { id: "zones" }, ZONES.map((z) => el("option", { value: z })));
    document.body.append(dl);
    const t = store.get("worldplan.token");
    if (t) $("token").value = t;
    $("token").addEventListener("change", () => store.set("worldplan.token", $("token").value));
    loadSample().catch(() => setState({ participants: [], events: [], horizon: {} }));
    setInterval(drawClock, 30000);
    for (const id of ["h-start", "h-days", "h-slot", "h-buf"]) $(id).addEventListener("change", horizonFromForm);
    $("add-person").onclick = () => { state.participants.push({ id: "", tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", work: { start: "09:00", end: "18:00" } }); syncJson(); drawForm(); };
    $("add-event").onclick = () => { state.events.push({ id: "e" + (state.events.length + 1), title: "", duration_minutes: 60, required: [] }); syncJson(); drawEvents(); };
    $("sample").onclick = () => run(loadSample);
    $("to-form").onclick = () => run(async () => setState(readReq()));
    $("plan").onclick = () => run(async () => { const req = readReq(); render(await call("/api/plan", { request: req }), req); });
    $("verify").onclick = () => run(async () => {
      const req = readReq();
      let a; try { a = JSON.parse($("assign").value || "{}"); } catch (e) { throw new Error("배정 JSON 을 못 읽는다"); }
      render(await call("/api/verify", { request: req, assignments: a }), req);
    });
    $("slots").onclick = () => run(async () => {
      const req = readReq();
      const who = $("who").value.split(",").map((s) => s.trim()).filter(Boolean);
      const body = { participants: req.participants, horizon: req.horizon, slot_minutes: req.slot_minutes,
        duration_minutes: Number($("dur").value), limit: 10 };
      if (who.length) body.required = who;
      render(await call("/api/slots", body), null);
    });
    $("ledger").onclick = () => run(async () => render(await call("/api/ledger"), null));
    $("ask").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const q = $("ask-q").value.trim();
      if (!q) return;
      $("ask-q").value = "";
      run(() => ask(q), $("ask-log"));
    });
  });
})();
