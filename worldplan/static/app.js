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
      else n.setAttribute(k, v);
    }
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return n;
  }

  async function loadSample() {
    const r = await fetch("/sample.json");
    $("req").value = JSON.stringify(await r.json(), null, 2);
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

  function readReq() {
    try { return JSON.parse($("req").value); }
    catch (e) { throw new Error("요청 JSON 을 못 읽는다: " + e.message); }
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

  async function run(btn, fn) {
    const all = document.querySelectorAll("button");
    all.forEach((b) => (b.disabled = true));
    try { await fn(); }
    catch (e) { $("out").replaceChildren(el("p", {}, el("span", { class: "badge bad" }, "오류"), " ", e.message)); }
    finally { all.forEach((b) => (b.disabled = false)); }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const t = store.get("worldplan.token");
    if (t) $("token").value = t;
    $("token").addEventListener("change", () => store.set("worldplan.token", $("token").value));
    loadSample().catch(() => {});
    $("sample").onclick = () => run(null, loadSample);
    $("plan").onclick = () => run(null, async () => { const req = readReq(); render(await call("/api/plan", { request: req }), req); });
    $("verify").onclick = () => run(null, async () => {
      const req = readReq();
      let a; try { a = JSON.parse($("assign").value || "{}"); } catch (e) { throw new Error("배정 JSON 을 못 읽는다"); }
      render(await call("/api/verify", { request: req, assignments: a }), req);
    });
    $("slots").onclick = () => run(null, async () => {
      const req = readReq();
      const who = $("who").value.split(",").map((s) => s.trim()).filter(Boolean);
      const body = { participants: req.participants, horizon: req.horizon, slot_minutes: req.slot_minutes,
        duration_minutes: Number($("dur").value), limit: 10 };
      if (who.length) body.required = who;
      render(await call("/api/slots", body), null);
    });
    $("ledger").onclick = () => run(null, async () => render(await call("/api/ledger"), null));
  });
})();
