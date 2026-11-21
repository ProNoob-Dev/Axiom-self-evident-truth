/* Axiom console — vanilla JS, no framework. One fetch wrapper, one
   renderer per view, all data from the live API (via /api/*, proxied to
   the FastAPI backend by site/serve.py). Nothing on this page is invented:
   every number is read from an API response. */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>\"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtInt(n) { return Number(n).toLocaleString("en-IN"); }
  function fmtPct(x) { return x == null ? "—" : (x * 100).toFixed(1) + "%"; }
  function fmtMoneyPaise(p) { return "₹" + (Number(p) / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 }); }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = { "Content-Type": "application/json" };
    return fetch("/api" + path, opts).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        if (!res.ok) throw new Error((data && data.detail) || ("Request failed: " + res.status));
        return data;
      });
    });
  }

  var state = { lastAsk: null, lastAction: null, tickets: [], disputes: [] };

  /* ---- views ---- */
  var TITLES = {
    ask: ["Ask workspace", "One question, one validated answer."],
    tickets: ["Tickets", "Plan, code read, test, review diff — human approval last."],
    disputes: ["Disputes", "Precedent search, citations, draft resolution."],
    traces: ["Traces", "Router to domain agents to validation to human review."],
    router: ["Router dashboard", "Confidence bands, false-positive rate, routing counts."],
    schema: ["Schema", "Canonical metrics, synonyms, tables and columns."],
    metrics: ["Metrics", "Autonomy rate, routing accuracy, counts."]
  };
  var loaded = {};
  function showView(name) {
    $$(".view").forEach(function (v) { v.hidden = v.id !== "view-" + name; });
    $$(".nav-btn").forEach(function (b) { b.setAttribute("aria-selected", String(b.dataset.view === name)); });
    $("#view-title").textContent = TITLES[name][0];
    $("#view-sub").textContent = TITLES[name][1];
    if (!loaded[name]) { loaded[name] = true; loadView(name); }
    window.scrollTo(0, 0);
  }
  function loadView(name) {
    if (name === "tickets") loadTickets();
    if (name === "disputes") loadDisputes();
    if (name === "router") loadRouter();
    if (name === "schema") loadSchema();
    if (name === "metrics") loadMetrics();
    if (name === "traces") renderTrace();
  }
  $$(".nav-btn").forEach(function (b) { b.addEventListener("click", function () { showView(b.dataset.view); }); });

  /* ---- health ---- */
  api("/health").then(function (h) {
    $("#health-line").textContent = "API live · " + h.llm_provider + " · " + h.retrieval;
    var sp = $("#sync-pill");
    if (sp) { sp.textContent = "API LIVE · LLM " + String(h.llm_provider).toUpperCase(); sp.classList.remove("down"); }
  }).catch(function () {
    $("#health-line").textContent = "API unreachable — start it on port 8125";
    var sp = $("#sync-pill");
    if (sp) { sp.textContent = "API UNREACHABLE"; sp.classList.add("down"); }
  });

  /* ---- ask ---- */
  function bandBadge(band) {
    if (band === "high") return '<span class="badge badge--green">high confidence</span>';
    if (band === "medium") return '<span class="badge badge--amber">medium confidence</span>';
    return '<span class="badge badge--amber">low confidence</span>';
  }
  function renderAsk(data, question) {
    state.lastAsk = { question: question, data: data };
    var empty = $("#ask-empty");
    if (empty) empty.hidden = true;
    var box = $("#ask-result");
    var h = '<div class="answer panel">';
    h += '<p class="answer__text">' + esc(data.answer || data.message || "") + "</p>";
    h += '<p class="answer__meta">request ' + esc(data.request_id) + " · route " + esc(data.route) +
      " · confidence " + Number(data.confidence).toFixed(2) + " " + bandBadge(data.band) + "</p>";
    if (data.route === "clarified" || data.route === "needs_verification" || data.route === "escalated") {
      h += '<p class="notice">Not answered yet. ' + esc(data.message || "") + "</p>";
      h += '<div class="chips"><button type="button" class="chip" data-example="What were total refunds in Mumbai last month?">Try: total refunds in Mumbai last month</button>' +
        '<button type="button" class="chip" data-example="Show me revenue by city">Try: revenue by city</button></div>';
    }
    if (data.sql) {
      h += '<p class="block-label">Generated SQL — validated before execution</p><pre>' + esc(data.sql) + "</pre>";
    }
    if (data.rows && data.rows.length) {
      h += '<p class="block-label">Result · ' + data.rows.length + " row" + (data.rows.length > 1 ? "s" : "") + "</p>";
      var keys = Object.keys(data.rows[0]);
      h += "<table><thead><tr>" + keys.map(function (k) { return "<th>" + esc(k) + "</th>"; }).join("") + "</tr></thead><tbody>";
      data.rows.forEach(function (r) {
        h += "<tr>" + keys.map(function (k) {
          var v = r[k];
          return "<td class=\"mono\">" + esc(typeof v === "number" ? fmtInt(v) : v) + "</td>";
        }).join("") + "</tr>";
      });
      h += "</tbody></table>";
    }
    if (data.validation) {
      h += '<p class="block-label">Result validation</p><div class="kv"><div><dt>Empty result</dt><dd>' +
        (data.validation.empty_result ? "yes" : "no") + "</dd></div><div><dt>Outliers</dt><dd>" +
        (data.validation.outliers && data.validation.outliers.length ? esc(data.validation.outliers.join(", ")) : "none") +
        "</dd></div><div><dt>Schema conformant</dt><dd>" + (data.validation.schema_conformant ? "yes" : "no") + "</dd></div></div>";
    }
    if (data.sources && (data.sources.schema.length || data.sources.past_queries.length)) {
      h += '<p class="block-label">Sources</p>';
      data.sources.schema.forEach(function (s) {
        h += '<div class="src"><b>' + esc(s.table) + "</b> — " + esc(s.description) +
          '<br><span class="mono">columns: ' + esc(s.columns.join(", ")) + " · match " + Number(s.score).toFixed(2) + "</span></div>";
      });
      data.sources.past_queries.forEach(function (p) {
        h += '<div class="src">Past verified query — “' + esc(p.question) + '”<br><span class="mono">match ' +
          Number(p.score).toFixed(2) + "</span></div>";
      });
    }
    if (data.matched_ticket || data.matched_dispute) {
      var m = data.matched_ticket, d = data.matched_dispute;
      h += '<p class="block-label">Routed to a domain</p>';
      if (m) h += '<p class="notice notice--green">Dev work — ticket <b>' + esc(m.id) + "</b>: " + esc(m.title) +
        ' <button type="button" class="btn btn--sm" data-goto-ticket="' + esc(m.id) + '">Open ticket</button></p>';
      if (d) h += '<p class="notice notice--green">Dispute <b>DSP-' + d.id + "</b> — " + esc(d.reason) + " (" + esc(d.customer.name) + ")" +
        ' <button type="button" class="btn btn--sm" data-goto-dispute="' + d.id + '">Open dispute</button></p>';
    }
    h += '<div class="btn-row"><button type="button" class="btn btn--sm" id="ask-trace">View trace</button>' +
      '<button type="button" class="btn btn--sm" data-feedback="regenerate">Regenerate</button>' +
      '<button type="button" class="btn btn--sm" data-feedback="reject">Reject answer</button>' +
      '<button type="button" class="btn btn--sm btn--danger" data-feedback="escalate">Escalate to a human</button></div>';
    h += '<p class="feedback" id="ask-feedback" role="status"></p></div>';
    box.innerHTML = h;
    $$("[data-example]", box).forEach(function (c) {
      c.addEventListener("click", function () { $("#ask-q").value = c.dataset.example; submitAsk(); });
    });
    $$("[data-goto-ticket]", box).forEach(function (b) {
      b.addEventListener("click", function () { showView("tickets"); openTicket(b.dataset.gotoTicket); });
    });
    $$("[data-goto-dispute]", box).forEach(function (b) {
      b.addEventListener("click", function () { showView("disputes"); openDispute(Number(b.dataset.gotoDispute)); });
    });
    $("#ask-trace").addEventListener("click", function () { showView("traces"); renderTrace(); });
    $$("[data-feedback]", box).forEach(function (b) {
      b.addEventListener("click", function () {
        var f = b.dataset.feedback, note = $("#ask-feedback");
        if (f === "regenerate") { submitAsk(); return; }
        if (f === "reject") note.textContent = "Answer marked rejected. It stays attached to request " + data.request_id + " — re-ask with a different wording, or escalate.";
        if (f === "escalate") note.textContent = "Escalated to a human reviewer with the question, SQL and sources attached. No further action is automated.";
      });
    });
  }
  function submitAsk() {
    var q = $("#ask-q").value.trim();
    if (!q) return;
    var box = $("#ask-result");
    box.innerHTML = '<p class="loading">Classifying, retrieving schema, generating SQL…</p>';
    api("/ask", { method: "POST", body: JSON.stringify({ question: q }) })
      .then(function (d) { renderAsk(d, q); })
      .catch(function (e) { box.innerHTML = '<p class="app-error">Ask failed: ' + esc(e.message) + "</p>"; });
  }
  $("#ask-form").addEventListener("submit", function (e) { e.preventDefault(); submitAsk(); });
  $("#topbar-ask").addEventListener("click", function () { showView("ask"); $("#ask-q").focus(); });
  $$(".chip[data-example]").forEach(function (c) {
    c.addEventListener("click", function () { $("#ask-q").value = c.dataset.example; submitAsk(); });
  });

  /* ---- tickets ---- */
  function statusBadge(s) {
    if (s === "approved") return '<span class="badge badge--green">approved</span>';
    if (s === "awaiting_review") return '<span class="badge badge--amber">awaiting review</span>';
    if (s === "rejected") return '<span class="badge badge--red">rejected</span>';
    return '<span class="badge">open</span>';
  }
  function loadTickets() {
    api("/tickets").then(function (d) {
      state.tickets = d.tickets;
      var h = "<table><thead><tr><th>Ticket</th><th>Title</th><th>Area</th><th>Priority</th><th>Status</th></tr></thead><tbody>";
      d.tickets.forEach(function (t) {
        h += '<tr data-ticket="' + esc(t.id) + '" tabindex="0"><td class="mono">' + esc(t.id) + "</td><td>" + esc(t.title) +
          "</td><td>" + esc(t.repo_area) + "</td><td>" + esc(t.priority) + "</td><td>" + statusBadge(t.status) + "</td></tr>";
      });
      $("#ticket-list").innerHTML = h + "</tbody></table>";
      $$("#ticket-list tr[data-ticket]").forEach(function (tr) {
        tr.addEventListener("click", function () { openTicket(tr.dataset.ticket); });
        tr.addEventListener("keydown", function (e) { if (e.key === "Enter") openTicket(tr.dataset.ticket); });
      });
    }).catch(function (e) { $("#ticket-list").innerHTML = '<p class="app-error">Could not load tickets: ' + esc(e.message) + "</p>"; });
  }
  function openTicket(id) {
    $$("#ticket-list tr").forEach(function (tr) { tr.setAttribute("aria-selected", String(tr.dataset.ticket === id)); });
    var box = $("#ticket-detail");
    box.innerHTML = '<p class="loading">Loading ' + esc(id) + "…</p>";
    api("/tickets/" + encodeURIComponent(id)).then(function (t) { renderTicket(t); })
      .catch(function (e) { box.innerHTML = '<p class="app-error">' + esc(e.message) + "</p>"; });
  }
  function renderTicket(t) {
    var box = $("#ticket-detail");
    var h = '<div class="panel"><h3>' + esc(t.id) + " — " + esc(t.title) + "</h3>" +
      '<p class="sub">' + esc(t.repo_area) + " · priority " + esc(t.priority) + " · " + statusBadge(t.status) + "</p>" +
      "<p>" + esc(t.description) + "</p>";
    if (t.plan) {
      h += '<p class="block-label">Plan · ' + t.plan.length + " steps</p><ol class=\"steps\">" +
        t.plan.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ol>";
    }
    if (t.code_read) {
      h += '<p class="block-label">Code read</p><table><thead><tr><th>File</th><th>Lines</th><th>Match</th><th>Excerpt</th></tr></thead><tbody>';
      t.code_read.forEach(function (c) {
        h += '<tr><td class="mono">' + esc(c.file) + '</td><td class="mono">' + esc(c.lines.join(", ")) +
          '</td><td class="mono">' + Number(c.score).toFixed(3) + "</td><td>" + esc(c.excerpt) + "</td></tr>";
      });
      h += "</tbody></table>";
    }
    if (t.proposed_test) {
      h += '<p class="block-label">Proposed test</p><pre>' + esc(t.proposed_test) + "</pre>";
    }
    if (t.diff) {
      var diff = esc(t.diff).replace(/^-(.*)$/gm, '<span class="del">-$1</span>').replace(/^\+(.*)$/gm, '<span class="add">+$1</span>');
      h += '<p class="block-label">Review diff</p><div class="diff">' + diff + "</div>";
    }
    if (t.review_summary) h += '<p class="notice notice--green" style="margin-top:16px">' + esc(t.review_summary) + "</p>";
    if (t.review_note) h += '<p class="notice">Reviewer note: ' + esc(t.review_note) + "</p>";
    h += '<div class="btn-row">';
    if (t.status === "open") h += '<button type="button" class="btn btn--primary" id="ticket-work">Work ticket</button>';
    if (t.status === "awaiting_review") {
      h += '<button type="button" class="btn btn--primary" data-review="approve">Approve</button>' +
        '<button type="button" class="btn btn--danger" data-review="reject">Reject</button>' +
        '<input type="text" id="review-note" placeholder="Review note (optional)" aria-label="Review note" style="flex:1;min-width:200px;background:var(--surface);border:1px solid var(--border2);border-radius:8px;color:var(--ink);padding:10px 12px;font:400 14px/1.4 var(--sans)">';
    }
    h += '</div><p class="feedback" id="ticket-feedback" role="status"></p></div>';
    box.innerHTML = h;
    var work = $("#ticket-work");
    if (work) work.addEventListener("click", function () {
      work.disabled = true; work.textContent = "Working…";
      api("/tickets/" + encodeURIComponent(t.id) + "/work", { method: "POST" })
        .then(function (nt) { state.lastAction = { kind: "ticket", id: nt.id, status: nt.status }; renderTicket(nt); loadTickets(); })
        .catch(function (e) { $("#ticket-feedback").textContent = "Work failed: " + e.message; work.disabled = false; work.textContent = "Work ticket"; });
    });
    $$("[data-review]", box).forEach(function (b) {
      b.addEventListener("click", function () {
        var note = ($("#review-note") || {}).value || null;
        api("/tickets/" + encodeURIComponent(t.id) + "/review", {
          method: "POST", body: JSON.stringify({ decision: b.dataset.review, note: note })
        }).then(function (nt) { state.lastAction = { kind: "ticket", id: nt.id, status: nt.status }; renderTicket(nt); loadTickets(); })
          .catch(function (e) { $("#ticket-feedback").textContent = "Review failed: " + e.message; });
      });
    });
  }

  /* ---- disputes ---- */
  function loadDisputes() {
    api("/disputes").then(function (d) {
      state.disputes = d.disputes;
      var clusters = d.reason_clusters || {};
      var ch = '<div class="stats">' + Object.keys(clusters).sort().map(function (k) {
        return '<div class="stat"><span>' + esc(k) + "</span><strong>" + clusters[k] + "</strong><small>reason cluster</small></div>";
      }).join("") + "</div>";
      $("#dispute-clusters").innerHTML = ch;
      var h = "<table><thead><tr><th>Dispute</th><th>Reason</th><th>Customer</th><th>Invoice</th><th>Status</th><th>Opened</th></tr></thead><tbody>";
      d.disputes.forEach(function (x) {
        h += '<tr data-dispute="' + x.id + '" tabindex="0"><td class="mono">DSP-' + x.id + "</td><td>" + esc(x.reason) +
          "</td><td>" + esc(x.customer.name) + " · " + esc(x.customer.city) + "</td><td class=\"mono\">" + fmtMoneyPaise(x.invoice.amount_paise) +
          "</td><td>" + statusBadge(x.status === "open" ? "open" : x.status) + "</td><td class=\"mono\">" + esc(x.opened_on) + "</td></tr>";
      });
      $("#dispute-list").innerHTML = h + "</tbody></table>";
      $$("#dispute-list tr[data-dispute]").forEach(function (tr) {
        tr.addEventListener("click", function () { openDispute(Number(tr.dataset.dispute)); });
        tr.addEventListener("keydown", function (e) { if (e.key === "Enter") openDispute(Number(tr.dataset.dispute)); });
      });
    }).catch(function (e) { $("#dispute-list").innerHTML = '<p class="app-error">Could not load disputes: ' + esc(e.message) + "</p>"; });
  }
  function openDispute(id) {
    $$("#dispute-list tr").forEach(function (tr) { tr.setAttribute("aria-selected", String(Number(tr.dataset.dispute) === id)); });
    var box = $("#dispute-detail");
    box.innerHTML = '<p class="loading">Loading DSP-' + id + "…</p>";
    api("/disputes/" + id).then(function (x) {
      box.innerHTML = '<div class="panel"><h3>DSP-' + x.id + " — " + esc(x.reason) + "</h3>" +
        '<p class="sub">' + esc(x.reason_cluster_label) + " · " + statusBadge(x.status) + " · opened " + esc(x.opened_on) +
        (x.resolved_on ? " · resolved " + esc(x.resolved_on) : "") + "</p>" +
        "<p>Customer " + esc(x.customer.name) + " (" + esc(x.customer.city) + ") · invoice " + x.invoice.id +
        " · " + fmtMoneyPaise(x.invoice.amount_paise) +
        (x.resolution ? " · resolution: " + esc(x.resolution) : "") + "</p>" +
        '<div class="btn-row"><button type="button" class="btn btn--primary" id="dispute-resolve">Resolve from precedent</button></div>' +
        '<p class="feedback" id="dispute-feedback" role="status"></p></div>';
      $("#dispute-resolve").addEventListener("click", function () { resolveDispute(id); });
    }).catch(function (e) { box.innerHTML = '<p class="app-error">' + esc(e.message) + "</p>"; });
  }
  function resolveDispute(id) {
    var fb = $("#dispute-feedback");
    if (fb) fb.textContent = "Searching precedent corpus…";
    api("/disputes/" + id + "/resolve", { method: "POST" }).then(function (r) {
      state.lastAction = { kind: "dispute", id: id, human_review: r.human_review };
      var h = '<div class="panel"><h3>Draft resolution · DSP-' + r.dispute.id + "</h3>" +
        '<p class="sub">Reason cluster: ' + esc(r.reason_cluster) + " · citations: " +
        (r.citations.length ? r.citations.map(esc).join(", ") : "none") + "</p>";
      if (r.human_review) {
        h += '<p class="notice">No precedent above the match threshold — human review required. No resolution was drafted.</p>';
      }
      if (r.precedents.length) {
        h += '<p class="block-label">Precedents</p>';
        r.precedents.forEach(function (p) {
          h += '<div class="src"><b>' + esc(p.id) + "</b> — " + esc(p.reason) + "<br>" + esc(p.resolution) +
            '<br><span class="mono">match score ' + Number(p.score).toFixed(3) + "</span></div>";
        });
      }
      h += '<p class="block-label">Draft resolution</p><p class="answer__text">' + esc(r.draft_resolution) + "</p>" +
        '<div class="btn-row"><button type="button" class="btn btn--sm" data-feedback="accept">Accept draft</button>' +
        '<button type="button" class="btn btn--sm" data-feedback="edit">Send back for human review</button></div>' +
        '<p class="feedback" id="resolve-feedback" role="status"></p></div>';
      $("#dispute-detail").innerHTML = h;
      $$("#dispute-detail [data-feedback]").forEach(function (b) {
        b.addEventListener("click", function () {
          $("#resolve-feedback").textContent = b.dataset.feedback === "accept"
            ? "Draft accepted as the resolution for DSP-" + id + ". Recorded against precedent " + (r.citations.join(", ") || "none") + "."
            : "Sent back for human review — no resolution was applied automatically.";
        });
      });
    }).catch(function (e) { if (fb) fb.textContent = "Resolve failed: " + e.message; });
  }

  /* ---- traces ---- */
  function traceStages(data) {
    var route = data.route, band = data.band;
    var stages = [
      { n: 1, t: "Intake", s: "done", d: "PII masked, entities extracted" },
      { n: 2, t: "Classify", s: "done", d: "confidence " + Number(data.confidence).toFixed(2) + " · band " + band },
      { n: 3, t: "Verify", s: band === "medium" ? "done" : "skipped", d: band === "medium" ? "medium confidence — verification" : "high/low band — no verification step" },
      { n: 4, t: "Domain route", s: route === "dev" || route === "disputes" ? "done" : "skipped", d: route === "dev" ? "dev ticket matched" : route === "disputes" ? "live dispute matched" : "analytics — no domain hop" },
      { n: 5, t: "Retrieve schema", s: data.sql || route === "answered" ? "done" : "skipped", d: data.sources && data.sources.schema.length ? data.sources.schema.length + " schema hit(s)" : "no schema hits" },
      { n: 6, t: "Generate SQL", s: data.sql ? "done" : "skipped", d: data.metric ? "metric " + data.metric : "no metric resolved" },
      { n: 7, t: "Validate SQL", s: data.sql ? "done" : "skipped", d: data.sql ? "sqlglot parse, whitelist, PII rules" : "no SQL to validate" },
      { n: 8, t: "Execute", s: route === "answered" ? "done" : "skipped", d: route === "answered" ? "read-only proxy, row limit applied" : "not executed" },
      { n: 9, t: "Validate result", s: data.validation ? "done" : "skipped", d: data.validation ? "empty " + (data.validation.empty_result ? "yes" : "no") + " · outliers " + (data.validation.outliers.length || "none") : "no result to validate" },
      { n: 10, t: "Respond", s: "done", d: "route " + route }
    ];
    if (route === "clarified" || route === "needs_verification" || route === "escalated") {
      stages[9] = { n: 10, t: "Respond", s: "active", d: "route " + route + " — clarification / escalation, not an answer" };
    }
    return stages;
  }
  function renderTrace() {
    var box = $("#trace-body");
    if (!state.lastAsk) {
      box.innerHTML = '<p class="loading">Ask a question first — its trace appears here. Ticket work and dispute resolutions also record their last action below once run.</p>';
      return;
    }
    var d = state.lastAsk.data;
    var h = '<div class="panel"><h3>Request ' + esc(d.request_id) + "</h3>" +
      '<p class="sub">“' + esc(state.lastAsk.question) + '” · route ' + esc(d.route) + " · confidence " + Number(d.confidence).toFixed(2) + "</p>" +
      '<ol class="pipe">' + traceStages(d).map(function (s) {
        return '<li class="' + s.s + '"><span class="p-n">' + String(s.n).padStart(2, "0") + '</span><span class="p-t">' + esc(s.t) +
          '</span><span class="p-s">' + esc(s.d) + "</span></li>";
      }).join("") + "</ol>" +
      '<p class="muted" style="font-size:13px">Per-stage latency is not reported by the API, so no latency numbers are shown — stages are marked completed or skipped only.</p></div>';
    if (state.lastAction) {
      h += '<div class="panel"><h3>Last domain action</h3><p class="sub">' + esc(state.lastAction.kind) + " " + esc(String(state.lastAction.id)) +
        " · " + esc(state.lastAction.status || (state.lastAction.human_review ? "human review required" : "resolved from precedent")) + "</p>" +
        '<p class="muted" style="font-size:13px;margin:0">Ticket and dispute work is audit-logged by the API (ticket_planned, ticket_code_read, ticket_reviewed, dispute_resolved); the log itself has no list endpoint, so the console shows the last action run in this session.</p></div>';
    }
    box.innerHTML = h;
  }

  /* ---- router ---- */
  function donutSvg(dist, total) {
    var C = 2 * Math.PI * 54, off = 0, parts = "";
    [["high", "#00e676"], ["medium", "#ffb800"], ["low", "#ff5c7a"]].forEach(function (p) {
      var v = dist[p[0]] || 0, len = total ? (v / total) * C : 0;
      if (len > 0) parts += '<circle cx="70" cy="70" r="54" fill="none" stroke="' + p[1] + '" stroke-width="16" stroke-dasharray="' +
        Math.max(len - 2, 0).toFixed(1) + " " + (C - Math.max(len - 2, 0)).toFixed(1) + '" stroke-dashoffset="' + (-off).toFixed(1) +
        '" transform="rotate(-90 70 70)"/>';
      off += len;
    });
    return '<svg width="140" height="140" viewBox="0 0 140 140" role="img" aria-label="Confidence distribution donut">' +
      '<circle cx="70" cy="70" r="54" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="16"/>' + parts +
      '<text x="70" y="66" text-anchor="middle" fill="#e2e4e9" font-size="24" font-weight="800">' + total + '</text>' +
      '<text x="70" y="86" text-anchor="middle" fill="#9aa3b2" font-size="11">decisions</text></svg>';
  }
  function bandPill(value, good, warn) {
    if (value == null) return '<span class="badge kpi-pill">NO DATA</span>';
    if (value >= good) return '<span class="badge badge--green kpi-pill">EXCELLENT</span>';
    if (value >= warn) return '<span class="badge badge--amber kpi-pill">WATCH</span>';
    return '<span class="badge badge--red kpi-pill">REVIEW</span>';
  }
  function loadRouter() {
    api("/router").then(function (r) {
      var dist = r.confidence_distribution || {}, dom = r.domains || {};
      var total = Object.keys(dist).reduce(function (a, k) { return a + dist[k]; }, 0) || 1;
      var h = '<div class="charts">';
      h += '<div class="panel"><h3>Confidence distribution</h3><p class="sub">' + total + " routing decisions in the audit log</p>" +
        '<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">' + donutSvg(dist, total) + '<div style="flex:1;min-width:140px">';
      [["high", "#00e676"], ["medium", "#ffb800"], ["low", "#ff5c7a"]].forEach(function (p) {
        var v = dist[p[0]] || 0;
        h += '<div style="display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.07);font-size:13px">' +
          '<span><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + p[1] + ';margin-right:8px"></span>' + p[0] +
          '</span><span class="mono">' + v + " · " + Math.round(v / total * 100) + "%</span></div>";
      });
      h += "</div></div></div>";
      h += '<div class="panel"><h3>False-positive rate — golden set</h3><p class="sub">Measured on labelled questions only; live traffic has no labels.</p>' +
        (r.golden_false_positive_rate === 0 ? '<span class="badge badge--green">ZERO</span>' : '<span class="badge badge--amber">NON-ZERO</span>') +
        '<div class="kpi" style="margin-top:10px">' + fmtPct(r.golden_false_positive_rate) + '</div>' +
        '<p class="sub" style="margin:8px 0 0">' + (r.golden_runs || 0) + " golden runs</p></div>";
      h += '<div class="panel"><h3>Router accuracy — golden set</h3><p class="sub">Correct route on labelled questions.</p>' +
        bandPill(r.golden_router_accuracy, .8, .5) +
        '<div class="kpi" style="margin-top:10px">' + fmtPct(r.golden_router_accuracy) + '</div>' +
        '<p class="sub" style="margin:8px 0 0">' + (r.golden_runs || 0) + " golden runs</p></div>";
      h += '<div class="panel"><h3>Routing by domain</h3><p class="sub">Decisions in the audit log</p>';
      Object.keys(dom).sort().forEach(function (k) {
        h += '<div class="bar-row"><span>' + esc(k) + '</span><span class="bar"><i style="width:' + Math.round(dom[k] / total * 100) + '%"></i></span><span class="mono">' + dom[k] + "</span></div>";
      });
      h += "</div></div>";
      $("#router-body").innerHTML = h;
    }).catch(function (e) { $("#router-body").innerHTML = '<p class="app-error">Could not load router data: ' + esc(e.message) + "</p>"; });
  }

  /* ---- schema ---- */
  function loadSchema() {
    api("/schema").then(function (s) {
      var h = '<p class="block-label">Canonical metrics · ' + Object.keys(s.metrics).length + "</p><div class=\"grid2\">";
      Object.keys(s.metrics).sort().forEach(function (k) {
        var m = s.metrics[k];
        h += '<div class="panel"><h3>' + esc(k) + ' <span class="badge">' + esc(m.unit) + "</span></h3>" +
          "<p>" + esc(m.definition) + "</p>" +
          '<p class="mono" style="color:var(--muted)">' + esc(m.formula) + "</p>" +
          '<p style="font-size:13px;color:var(--muted);margin:0">Synonyms: ' + m.synonyms.map(esc).join(", ") + "</p></div>";
      });
      h += '</div><p class="block-label">Tables · ' + Object.keys(s.tables).length + "</p>";
      Object.keys(s.tables).sort().forEach(function (t) {
        var tb = s.tables[t];
        h += '<div class="panel"><h3>' + esc(t) + "</h3><p class=\"sub\">" + esc(tb.description) + "</p><table><thead><tr><th>Column</th><th>Type</th><th>Description</th></tr></thead><tbody>";
        tb.columns.forEach(function (c) {
          h += "<tr><td class=\"mono\">" + esc(c.name) + '</td><td class="mono">' + esc(c.type) + "</td><td>" + esc(c.description) + "</td></tr>";
        });
        h += "</tbody></table></div>";
      });
      $("#schema-body").innerHTML = h;
    }).catch(function (e) { $("#schema-body").innerHTML = '<p class="app-error">Could not load schema: ' + esc(e.message) + "</p>"; });
  }

  /* ---- metrics ---- */
  function loadMetrics() {
    Promise.all([api("/metrics"), api("/router")]).then(function (res) {
      var m = res[0], r = res[1];
      var h = '<div class="stats">' +
        '<div class="stat">' + bandPill(m.autonomy_rate, .8, .5) + '<span>Autonomy rate</span><strong>' + fmtPct(m.autonomy_rate) + '</strong><small>' + m.answered + " of " + m.total_questions + " questions answered</small></div>" +
        '<div class="stat">' + bandPill(m.routing_accuracy_golden, .8, .5) + '<span>Routing accuracy</span><strong>' + fmtPct(m.routing_accuracy_golden) + '</strong><small>golden set</small></div>' +
        '<div class="stat">' + (r.golden_false_positive_rate === 0 ? '<span class="badge badge--green kpi-pill">ZERO</span>' : '<span class="badge badge--amber kpi-pill">NON-ZERO</span>') + '<span>False-positive rate</span><strong>' + fmtPct(r.golden_false_positive_rate) + '</strong><small>golden set, ' + (r.golden_runs || 0) + ' runs</small></div>' +
        '<div class="stat"><span class="badge badge--accent kpi-pill">LIVE</span><span>Questions asked</span><strong>' + m.total_questions + '</strong><small>live audit log</small></div></div>' +
        '<div class="panel"><h3>Golden-set detail</h3><p class="sub">' + (r.golden_runs || 0) + " runs · false-positive rate " + fmtPct(r.golden_false_positive_rate) + "</p>" +
        '<div class="stats" style="grid-template-columns:repeat(3,1fr)">' +
        '<div class="stat"><span>Routed elsewhere</span><strong>' + m.routed_elsewhere + '</strong><small>clarified, dev or disputes</small></div>' +
        '<div class="stat"><span>Analytics correct</span><strong>' + (m.analytics_correct_golden == null ? "—" : m.analytics_correct_golden) + '</strong><small>golden set</small></div>' +
        '<div class="stat"><span>SQL validation correct</span><strong>' + (m.sql_validation_correct_golden == null ? "—" : m.sql_validation_correct_golden) + '</strong><small>golden set</small></div></div>' +
        '<p style="margin:0">Autonomy rate is live (answered / asked). Routing accuracy and the false-positive rate come from the labelled golden set, because live traffic has no labels to score against.</p></div>';
      $("#metrics-body").innerHTML = h;
    }).catch(function (e) { $("#metrics-body").innerHTML = '<p class="app-error">Could not load metrics: ' + esc(e.message) + "</p>"; });
  }

  /* ---- command palette (Cmd/Ctrl+K) ---- */
  var COMMANDS = [
    { label: "Ask workspace", hint: "view", run: function () { showView("ask"); } },
    { label: "Tickets", hint: "view", run: function () { showView("tickets"); } },
    { label: "Disputes", hint: "view", run: function () { showView("disputes"); } },
    { label: "Traces", hint: "view", run: function () { showView("traces"); } },
    { label: "Router dashboard", hint: "view", run: function () { showView("router"); } },
    { label: "Schema", hint: "view", run: function () { showView("schema"); } },
    { label: "Metrics", hint: "view", run: function () { showView("metrics"); } },
    { label: "Ask: total refunds in Mumbai last month", hint: "ask", run: function () { showView("ask"); $("#ask-q").value = "What were total refunds in Mumbai last month?"; submitAsk(); } },
    { label: "Ask: revenue by city", hint: "ask", run: function () { showView("ask"); $("#ask-q").value = "Show me revenue by city"; submitAsk(); } },
    { label: "Work ticket TKT-101", hint: "ticket", run: function () { showView("tickets"); openTicket("TKT-101"); } }
  ];
  var pal = $("#palette"), palInput = $("#palette-input"), palList = $("#palette-list");
  function renderPalette(q) {
    var items = COMMANDS.filter(function (c) { return c.label.toLowerCase().indexOf(q.toLowerCase()) !== -1; });
    palList.innerHTML = items.map(function (c, i) {
      return '<li><button type="button" data-cmd="' + i + '" aria-selected="' + (i === 0) + '">' + esc(c.label) + '<span class="mono">' + esc(c.hint) + "</span></button></li>";
    }).join("") || '<li><p class="empty" style="padding:12px">No matching command.</p></li>';
    $$("[data-cmd]", palList).forEach(function (b) {
      b.addEventListener("click", function () { closePalette(); items[Number(b.dataset.cmd)].run(); });
    });
  }
  function openPalette() { pal.hidden = false; palInput.value = ""; renderPalette(""); palInput.focus(); }
  function closePalette() { pal.hidden = true; }
  $("#palette-open").addEventListener("click", openPalette);
  pal.addEventListener("click", function (e) { if (e.target === pal) closePalette(); });
  palInput.addEventListener("input", function () { renderPalette(palInput.value); });
  palInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { var first = $("[data-cmd]", palList); if (first) first.click(); }
    if (e.key === "Escape") closePalette();
  });
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); pal.hidden ? openPalette() : closePalette(); }
    if (e.key === "Escape" && !pal.hidden) closePalette();
    if (!pal.hidden || e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    var map = { a: "ask", t: "tickets", d: "disputes", g: "traces", r: "router", s: "schema", m: "metrics" };
    if (map[e.key.toLowerCase()]) showView(map[e.key.toLowerCase()]);
  });
})();
