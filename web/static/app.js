/* Shot Clock console.
 *
 * Two feeds drive this page and nothing else does:
 *
 *   GET /api/shots   polled  — the render farm's own state (the shot board)
 *   GET /api/events  SSE     — the crew's journal (the trace, report, captions)
 *
 * Journal events arrive as {kind, actor, offset, payload, seq}. The payload
 * keys this file reads are listed in web/scripted_demo.py; every read here has
 * a fallback, because a live agent run is allowed to be untidy and the console
 * must never go blank because one key was missing.
 */
(function () {
  "use strict";

  var BOARD_POLL_MS = 2000;
  var CLOCK_TICK_MS = 250;
  var MAX_TRACE_ROWS = 400;
  var DEFAULT_CAPTION_MS = 4800;

  var CREW = {
    scout:    { name: "Scout",     tag: "SCOUT",    job: "watches the whole farm" },
    gaffer:   { name: "Gaffer",    tag: "GAFFER",   job: "finds the fault" },
    producer: { name: "Producer",  tag: "PRODUCER", job: "prices the delay" },
    first_ad: { name: "First AD",  tag: "FIRST AD", job: "writes it back" },
    system:   { name: "System",    tag: "SYSTEM",   job: "" }
  };
  var CREW_ORDER = ["scout", "gaffer", "producer", "first_ad"];

  var RISKS = [
    { key: "on-track", label: "On track", css: "var(--cool)" },
    { key: "at-risk",  label: "At risk",  css: "var(--tungsten)" },
    { key: "late",     label: "Late",     css: "var(--hot)" },
    { key: "failed",   label: "Failed",   css: "var(--dead)" }
  ];
  var RISK_RANK = { failed: 0, late: 1, "at-risk": 2, "on-track": 3 };

  var $ = function (id) { return document.getElementById(id); };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function num(value) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    return Number(value).toLocaleString("en-US");
  }

  /* Production time, not wall time: a shot's ETA is quoted in hours of farm
     time, which is what a supervisor thinks in. */
  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "stalled";
    if (seconds <= 0) return "done";
    var d = Math.floor(seconds / 86400);
    var h = Math.floor((seconds % 86400) / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return d + "d " + pad(h) + "h";
    if (h > 0) return h + "h " + pad(m) + "m";
    return m + "m";
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function clockFace(seconds) {
    if (seconds === null || seconds === undefined) return "--d --:--";
    var late = seconds < 0;
    var s = Math.abs(seconds);
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    return (late ? "+" : "") + d + "d " + pad(h) + ":" + pad(m);
  }

  /* Journal offsets are seconds since the process journal opened. The trace
     wants seconds since *this run* started, so the clock in the gutter reads
     from zero however long the console has been up. */
  var runOrigin = 0;
  var runSeq = 0;

  function offsetFace(offset) {
    var s = Math.max(0, Math.floor((offset || 0) - runOrigin));
    return pad(Math.floor(s / 60)) + ":" + pad(s % 60);
  }

  // ---------------------------------------------------------------- board --

  var board = {
    data: null,
    fetchedAt: 0,
    cards: {},          // shot_id -> element
    order: "",          // last rendered card order, so we only reshuffle on change
    seqRows: {}
  };

  function pollBoard() {
    fetch("/api/shots", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        board.data = data;
        board.fetchedAt = performance.now();
        renderBoard(data);
      })
      .catch(function () { /* a dropped poll is not worth a banner */ });
  }

  function renderBoard(data) {
    $("film-title").textContent = data.film.title;
    $("film-sub").textContent =
      num(data.film.shots_total) + " shots · " + num(data.film.frames_total) +
      " frames · delivery " + data.film.delivery_date;

    renderVitals(data.farm, data.totals);
    renderLegend(data.in_flight_counts);
    renderShots(data.shots);
    renderSequences(data.sequences);

    $("board-note").textContent =
      data.totals.in_flight + " in flight · " +
      num(data.totals.backlog) + " in backlog · " +
      num(data.totals.complete) + " delivered";
    $("backlog-note").textContent = num(data.totals.at_risk) + " of " +
      num(data.totals.shots_total) + " projected past the date";
  }

  function renderVitals(farm, totals) {
    var host = $("vitals");
    var items = [
      { label: "Nodes rendering", value: farm.nodes_busy + "/" + farm.nodes_total,
        warn: farm.nodes_offline > 0 },
      { label: "Licences free", value: farm.licences_available + "/" + farm.licences_total,
        warn: farm.licences_available < farm.licences_total * 0.15 },
      { label: "Texture cache", value: Math.round(farm.texture_cache_hit_ratio * 100) + "%",
        warn: farm.texture_cache_hit_ratio < 0.85, bad: farm.texture_cache_hit_ratio < 0.6 },
      { label: "Queue depth", value: String(farm.queue_depth), warn: farm.queue_depth > 3 },
      { label: "Frames / hour", value: num(Math.round(farm.frames_per_hour)) },
      { label: "Shots at risk", value: num(totals.at_risk),
        warn: totals.at_risk > 0, bad: totals.at_risk > 60 }
    ];

    if (!host.childElementCount) {
      items.forEach(function () {
        var cell = el("div", "vital");
        cell.appendChild(el("span", "eyebrow"));
        cell.appendChild(el("div", "vital__value"));
        host.appendChild(cell);
      });
    }
    items.forEach(function (item, i) {
      var cell = host.children[i];
      cell.className = "vital" + (item.bad ? " is-bad" : item.warn ? " is-warn" : "");
      cell.children[0].textContent = item.label;
      cell.children[1].textContent = item.value;
    });
  }

  function renderLegend(counts) {
    var host = $("legend");
    if (!host.childElementCount) {
      RISKS.forEach(function (risk) {
        var item = el("div", "legend__item");
        var swatch = el("span", "legend__swatch");
        swatch.style.background = risk.css;
        item.appendChild(swatch);
        item.appendChild(el("span", null, risk.label));
        item.appendChild(el("span", "legend__count"));
        host.appendChild(item);
      });
    }
    RISKS.forEach(function (risk, i) {
      host.children[i].children[2].textContent = counts[risk.key] || 0;
    });
  }

  /* Only the ~40 in-flight shots get a card. Cards are kept and mutated rather
     than rebuilt, so progress bars animate instead of flickering. */
  function renderShots(shots) {
    var grid = $("shot-grid");
    var seen = {};

    shots.forEach(function (shot) {
      seen[shot.shot_id] = true;
      var card = board.cards[shot.shot_id];
      if (!card) {
        card = buildShotCard(shot);
        board.cards[shot.shot_id] = card;
        grid.appendChild(card);
      }
      updateShotCard(card, shot);
    });

    Object.keys(board.cards).forEach(function (id) {
      if (!seen[id]) {
        var gone = board.cards[id];
        if (gone.parentNode) gone.parentNode.removeChild(gone);
        delete board.cards[id];
      }
    });

    // Worst first. Reordering only when the order actually changes keeps the
    // board from twitching on every poll.
    var sorted = shots.slice().sort(function (a, b) {
      var d = RISK_RANK[a.risk] - RISK_RANK[b.risk];
      return d !== 0 ? d : (a.shot_id < b.shot_id ? -1 : 1);
    });
    var key = sorted.map(function (s) { return s.shot_id; }).join(",");
    if (key !== board.order) {
      board.order = key;
      sorted.forEach(function (shot) { grid.appendChild(board.cards[shot.shot_id]); });
    }
  }

  function buildShotCard(shot) {
    var card = el("article", "shot");
    var top = el("div", "shot__top");
    top.appendChild(el("span", "shot__id", shot.shot_id));
    top.appendChild(el("span", "shot__flag"));
    card.appendChild(top);
    card.appendChild(el("span", "shot__seq"));

    var bar = el("div", "shot__bar");
    bar.appendChild(el("i"));
    card.appendChild(bar);

    var nums = el("div", "shot__nums");
    nums.appendChild(el("span", "shot__frames"));
    nums.appendChild(el("span", "shot__eta"));
    card.appendChild(nums);

    var foot = el("div", "shot__foot");
    foot.appendChild(el("span", "shot__artist"));
    foot.appendChild(el("span", "mono shot__node"));
    card.appendChild(foot);
    return card;
  }

  function updateShotCard(card, shot) {
    card.dataset.risk = shot.risk;
    card.querySelector(".shot__flag").textContent =
      shot.attempt > 1 ? "retry " + shot.attempt : (shot.risk === "on-track" ? "" : shot.risk);
    // Sequence names arrive as SEQ_0400_ROOFTOP_CHASE; the words are what a
    // supervisor says out loud.
    card.querySelector(".shot__seq").textContent =
      shot.sequence.split("_").slice(2).join(" ") || shot.sequence;
    card.querySelector(".shot__bar i").style.width =
      Math.round(shot.progress * 100) + "%";
    card.querySelector(".shot__frames").textContent =
      String(shot.frames_done).padStart(3, "0") + " / " + shot.frames_total + " fr";
    card.querySelector(".shot__eta").textContent = duration(shot.eta_seconds);
    card.querySelector(".shot__artist").textContent = shot.artist;
    card.querySelector(".shot__node").textContent =
      shot.renderer + (shot.node ? " · " + shot.node : "");
  }

  /* The 1,160-shot backlog as eight stacked bars: enough to feel the weight of
     the film without putting 1,200 nodes in the document. */
  function renderSequences(sequences) {
    var host = $("seq-list");
    sequences.forEach(function (seq) {
      var row = board.seqRows[seq.code];
      if (!row) {
        row = el("div", "seq");
        row.appendChild(el("span", "seq__code", seq.code));
        row.appendChild(el("span", "seq__name", seq.description));
        var bar = el("div", "seq__bar");
        ["is-complete", "is-render", "is-risk", "is-failed"].forEach(function (cls) {
          bar.appendChild(el("i", cls));
        });
        row.appendChild(bar);
        row.appendChild(el("span", "seq__count"));
        host.appendChild(row);
        board.seqRows[seq.code] = row;
      }
      var total = seq.total || 1;
      var risk = Math.min(seq.at_risk, seq.pending);
      var parts = [seq.complete, seq.rendering, risk, seq.failed];
      var bars = row.querySelectorAll(".seq__bar i");
      parts.forEach(function (value, i) {
        bars[i].style.width = (value / total * 100).toFixed(2) + "%";
      });
      row.querySelector(".seq__count").textContent =
        seq.complete + "/" + seq.total;
    });
  }

  // ------------------------------------------------------------ countdown --

  function tickClock() {
    if (!board.data) return;
    var elapsed = (performance.now() - board.fetchedAt) / 1000;
    // Production time runs faster than wall time; interpolate between polls so
    // the clock never visibly stalls.
    var left = board.data.seconds_to_delivery - elapsed * (board.data.sim_rate || 1);
    var face = $("countdown");
    face.textContent = clockFace(left);
    face.classList.toggle("is-tight", left < 86400 && left >= 0);
    face.classList.toggle("is-blown", left < 0);

    var simMs = Date.parse(board.data.sim_time) + elapsed * (board.data.sim_rate || 1) * 1000;
    var sim = new Date(simMs);
    $("simclock").textContent =
      "production clock " +
      sim.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " +
      pad(sim.getHours()) + ":" + pad(sim.getMinutes()) +
      " · running at " + Math.round((board.data.sim_rate || 60) / 60) + " min/s";
  }

  // ---------------------------------------------------------------- trace --

  var trace = {
    node: null,
    stuck: true,
    rows: 0
  };

  function initCrewBar() {
    var host = $("crewbar");
    CREW_ORDER.forEach(function (key) {
      var cell = el("div", "crew");
      cell.dataset.actor = key;
      cell.style.setProperty("--actor", actorColour(key));
      cell.appendChild(el("span", "crew__name", CREW[key].name));
      cell.appendChild(el("div", "crew__job", CREW[key].job));
      host.appendChild(cell);
    });
  }

  function actorColour(actor) {
    return {
      scout: "var(--cool)", gaffer: "var(--tungsten)",
      producer: "var(--brass)", first_ad: "var(--paper)"
    }[actor] || "var(--text-mute)";
  }

  function setActiveCrew(actor) {
    var cells = $("crewbar").children;
    for (var i = 0; i < cells.length; i++) {
      cells[i].classList.toggle("is-active", cells[i].dataset.actor === actor);
    }
  }

  function addRow(event, kindClass, build) {
    var empty = $("trace-empty");
    if (empty && empty.parentNode) empty.parentNode.removeChild(empty);

    var row = el("div", "ev " + kindClass);
    row.dataset.actor = event.actor;

    var gutter = el("div", "ev__gutter");
    gutter.appendChild(el("span", "ev__actor", (CREW[event.actor] || {}).tag || event.actor));
    gutter.appendChild(el("span", "ev__time mono", offsetFace(event.offset)));
    row.appendChild(gutter);

    var body = el("div", "ev__body");
    build(body, event.payload || {});
    row.appendChild(body);

    trace.node.appendChild(row);
    trace.rows += 1;
    if (trace.rows > MAX_TRACE_ROWS) {
      trace.node.removeChild(trace.node.firstElementChild);
      trace.rows -= 1;
    }
    if (trace.stuck) {
      trace.node.scrollTop = trace.node.scrollHeight;
    } else {
      $("jump").hidden = false;
    }
  }

  function argsBlock(args) {
    var pre = el("pre", "args");
    Object.keys(args || {}).forEach(function (key) {
      var value = args[key];
      if (value && typeof value === "object") value = JSON.stringify(value);
      var label = el("b", null, key + ": ");
      pre.appendChild(label);
      pre.appendChild(document.createTextNode(String(value) + "\n"));
    });
    return pre;
  }

  function rowsBlock(rows) {
    var box = el("div", "rows");
    rows.slice(0, 6).forEach(function (row) {
      var text;
      if (typeof row === "string") {
        text = row;
      } else if (row.line) {
        text = row.line;
      } else {
        text = Object.keys(row).map(function (k) { return k + "=" + row[k]; }).join("  ");
      }
      box.appendChild(el("div", null, text));
    });
    if (rows.length > 6) box.appendChild(el("div", null, "… " + (rows.length - 6) + " more"));
    return box;
  }

  var RENDERERS = {
    run_start: function (event) {
      runOrigin = event.offset || 0;
      runSeq = event.seq || 0;
      resetRun();
      addRow(event, "ev--start", function (body, p) {
        body.appendChild(el("div", "ev__role", "Run " + (p.mode === "demo" ? "— demo replay" : "— live")));
        body.appendChild(el("div", "ev__goal",
          (p.scenario || "") + (p.film ? " · " + p.film : "")));
      });
      $("trace-note").textContent = "run in progress";
      $("demo-btn").disabled = true;
      $("demo-btn").textContent = "Replaying";
    },

    agent_start: function (event) {
      setActiveCrew(event.actor);
      addRow(event, "ev--start", function (body, p) {
        body.appendChild(el("div", "ev__role", p.role || (CREW[event.actor] || {}).name || event.actor));
        body.appendChild(el("div", "ev__goal", p.goal || p.text || ""));
      });
    },

    agent_thought: function (event) {
      setActiveCrew(event.actor);
      addRow(event, "ev--thought", function (body, p) {
        body.appendChild(el("p", "ev__text", p.text || p.thought || p.message || ""));
      });
    },

    tool_call: function (event) {
      addRow(event, "ev--tool", function (body, p) {
        var head = el("div", "tool");
        head.appendChild(el("span", "tool__verb", "CALL"));
        head.appendChild(el("span", "tool__name", p.tool || p.name || "tool"));
        body.appendChild(head);
        if (p.args) body.appendChild(argsBlock(p.args));
        if (p.why) body.appendChild(el("p", "why", p.why));
      });
    },

    tool_result: function (event) {
      addRow(event, "ev--result", function (body, p) {
        var head = el("div", "tool");
        head.appendChild(el("span", "tool__verb", "RETURN"));
        head.appendChild(el("span", "tool__name", p.tool || p.name || "tool"));
        head.appendChild(el("span", p.ok === false ? "tool__fail" : "tool__ok",
          p.ok === false ? "ERROR" : "OK"));
        if (p.latency_ms) head.appendChild(el("span", "tool__ms", p.latency_ms + " ms"));
        body.appendChild(head);
        var summary = p.summary || p.text || p.error;
        if (summary) body.appendChild(el("p", "ev__summary", summary));
        if (p.rows && p.rows.length) body.appendChild(rowsBlock(p.rows));
      });
    },

    vision_verdict: function (event) {
      addRow(event, "ev--vision", function (body, p) {
        var head = el("div", "tool");
        head.appendChild(el("span", "tool__verb", "LOOK"));
        head.appendChild(el("span", "tool__name",
          (p.shot_id || "shot") + (p.frame_number ? " frame " + String(p.frame_number).padStart(4, "0") : "")));
        body.appendChild(head);

        var wrap = el("div", "vision");
        if (p.frame) {
          var img = el("img");
          img.src = p.frame;
          img.alt = (p.shot_id || "") + " rendered frame";
          // The plates are gitignored; if this clone has none, drop the
          // thumbnail rather than showing a broken image.
          img.onerror = function () { img.parentNode.removeChild(img); };
          wrap.appendChild(img);
        }
        var side = el("div");
        side.appendChild(el("div", "vision__verdict",
          (p.verdict || "verdict") + (p.defect ? " · " + p.defect : "")));
        if (p.note) side.appendChild(el("p", "vision__note", p.note));
        var meta = [];
        if (p.confidence !== undefined) meta.push("confidence " + p.confidence);
        if (p.metrics_clean) meta.push("every metric on this shot reads healthy");
        if (meta.length) side.appendChild(el("div", "vision__meta", meta.join("  ·  ")));
        wrap.appendChild(side);
        body.appendChild(wrap);
      });
    },

    costing: function (event) {
      renderCosting(event.payload || {});
      addRow(event, "ev--result", function (body, p) {
        var head = el("div", "tool");
        head.appendChild(el("span", "tool__verb", "COSTED"));
        head.appendChild(el("span", "tool__name", "delivery exposure"));
        body.appendChild(head);
        body.appendChild(el("p", "ev__summary",
          num(p.shots_at_risk) + " shots at risk · " +
          (p.delay_hours ? p.delay_hours + " h past delivery · " : "") +
          (p.cost_usd ? "$" + num(p.cost_usd) + " exposed" : "")));
      });
    },

    write_back: function (event) {
      var p = event.payload || {};
      addRow(event, "ev--write", function (body) {
        body.appendChild(el("div", "write__kind",
          "wrote to grafana · " + writeKind(p)));
        body.appendChild(el("div", "write__title", p.title || p.target || ""));
      });
      addDeeplink(p);
      if (p.note) renderNote(p.note);
    },

    caption: function (event) {
      showCaption((event.payload || {}).text, (event.payload || {}).duration_ms);
    },

    run_end: function (event) {
      addRow(event, "ev--end", function (body, p) {
        body.appendChild(el("p", "ev__text", p.headline || p.status || "run complete"));
      });
      setActiveCrew(null);
      $("trace-note").textContent = "run complete";
      $("demo-btn").disabled = false;
      $("demo-btn").textContent = "Run demo";
    }
  };

  function handleEvent(event) {
    // A browser that connects mid-run is sent the whole journal at once, which
    // can still be draining when a new run starts. Journal seq numbers are
    // monotonic, so anything older than the current run_start belongs to the
    // previous run and would otherwise repopulate the report behind our backs.
    if (event.kind !== "run_start" && event.seq && event.seq < runSeq) return;

    var render = RENDERERS[event.kind];
    if (render) {
      render(event);
      return;
    }
    // Unknown kinds still show up rather than vanishing: a judge should be
    // able to see everything the crew emitted.
    addRow(event, "ev--result", function (body, p) {
      body.appendChild(el("div", "tool")).appendChild(el("span", "tool__name", event.kind));
      body.appendChild(el("p", "ev__summary", JSON.stringify(p)));
    });
  }

  // --------------------------------------------------------------- report --

  function resetRun() {
    trace.node.innerHTML = "";
    trace.rows = 0;
    trace.stuck = true;
    $("jump").hidden = true;
    setActiveCrew(null);

    // The report always reads in the same order regardless of the order the
    // events arrive in: the numbers, then the note, then the way back into
    // Grafana. Empty slots take no space.
    $("report").innerHTML = "";
    report.costing = el("div", "costing");
    report.note = null;
    report.noteSlot = el("div");
    report.links = el("div", "deeplinks");
    // Numbers, then the proof it was written back, then the long read.
    $("report").appendChild(report.costing);
    $("report").appendChild(report.links);
    $("report").appendChild(report.noteSlot);
  }

  var report = { costing: null, note: null, noteSlot: null, links: null };

  /* An event can arrive before any run_start (a journal that opens mid-run),
     so the report slots are built on demand as well as on reset. */
  function resetReportSlots() {
    $("report").innerHTML = "";
    report.costing = el("div", "costing");
    report.note = null;
    report.noteSlot = el("div");
    report.links = el("div", "deeplinks");
    // Numbers, then the proof it was written back, then the long read.
    $("report").appendChild(report.costing);
    $("report").appendChild(report.links);
    $("report").appendChild(report.noteSlot);
  }

  function renderCosting(p) {
    if (!report.costing) resetReportSlots();
    report.costing.innerHTML = "";

    var lines = p.lines;
    if (!lines || !lines.length) {
      lines = [
        { label: "Shots at risk", value: num(p.shots_at_risk) },
        { label: "Delay", value: (p.delay_hours || 0) + " h" },
        { label: "Cost exposure", value: "$" + num(p.cost_usd) }
      ];
    }
    lines.forEach(function (line) {
      var cell = el("div", "cost");
      cell.appendChild(el("span", "eyebrow", line.label));
      cell.appendChild(el("div", "cost__value", line.value));
      report.costing.appendChild(cell);
    });
    $("report-note").textContent = "filed by Producer";
  }

  function renderNote(text) {
    if (!report.noteSlot) resetReportSlots();
    if (!report.note) {
      report.note = el("div", "note");
      report.note.appendChild(el("h3", null, "First AD"));
      report.noteSlot.appendChild(report.note);
    }
    // Keep only the heading, then re-lay the paragraphs.
    while (report.note.childElementCount > 1) report.note.removeChild(report.note.lastChild);
    String(text).split(/\n{2,}/).forEach(function (para) {
      report.note.appendChild(el("p", null, para.replace(/\n/g, " ")));
    });
    $("report-note").textContent = "note filed";
  }

  /* A write_back names what it wrote: "incident", "annotation", "dashboard".
     The key is `resource` — `kind` is taken by the journal event itself. */
  function writeKind(p) {
    return p.resource || p.kind_ || p.target_kind || "resource";
  }

  function addDeeplink(p) {
    if (!p.url) return;
    if (!report.links) resetReportSlots();
    var link = el("a", "deeplink");
    link.href = p.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    var left = el("div");
    left.appendChild(el("div", "deeplink__kind",
      "open in grafana · " + writeKind(p)));
    left.appendChild(el("div", "deeplink__title",
      p.title || p.target || p.url));
    link.appendChild(left);
    link.appendChild(el("span", "deeplink__go", (p.id ? "#" + p.id + " " : "") + "open"));
    report.links.appendChild(link);
  }

  // -------------------------------------------------------------- caption --

  var captionTimer = null;

  function showCaption(text, ms) {
    if (!text) return;
    var box = $("caption");
    $("caption-text").textContent = text;
    box.classList.add("is-on");
    if (captionTimer) clearTimeout(captionTimer);
    captionTimer = setTimeout(function () {
      box.classList.remove("is-on");
    }, ms || DEFAULT_CAPTION_MS);
  }

  // ------------------------------------------------------------------ SSE --

  function connect() {
    var source = new EventSource("/api/events");

    source.onopen = function () {
      $("feed-dot").className = "dot is-live";
      $("feed-label").textContent = "feed live";
    };
    source.onerror = function () {
      $("feed-dot").className = "dot is-down";
      $("feed-label").textContent = "reconnecting";
      // EventSource reconnects on its own; nothing to do but say so.
    };
    source.onmessage = function (message) {
      var event;
      try { event = JSON.parse(message.data); } catch (err) { return; }
      handleEvent(event);
    };
  }

  // ----------------------------------------------------------------- boot --

  function boot() {
    trace.node = $("trace");
    initCrewBar();

    // The feed follows the run unless the reader has deliberately scrolled up.
    // Only a real gesture unsticks it: a plain scroll event also fires while
    // the panel is smooth-scrolling itself, which would unstick it constantly.
    function atBottom() {
      return trace.node.scrollHeight - trace.node.scrollTop
        - trace.node.clientHeight < 48;
    }
    trace.node.addEventListener("scroll", function () {
      if (atBottom()) {
        trace.stuck = true;
        $("jump").hidden = true;
      }
    });
    ["wheel", "touchmove", "keydown"].forEach(function (name) {
      trace.node.addEventListener(name, function () {
        if (!atBottom()) {
          trace.stuck = false;
          $("jump").hidden = false;
        }
      }, { passive: true });
    });

    $("jump").addEventListener("click", function () {
      trace.stuck = true;
      $("jump").hidden = true;
      trace.node.scrollTop = trace.node.scrollHeight;
    });

    $("demo-btn").addEventListener("click", function () {
      $("demo-btn").disabled = true;
      $("demo-btn").textContent = "Starting";
      fetch("/api/demo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      })
        .then(function (r) { return r.json(); })
        .then(function (info) {
          $("trace-note").textContent =
            (info.synthetic ? "scripted replay · " : "replaying · ") + info.journal;
        })
        .catch(function () {
          $("demo-btn").disabled = false;
          $("demo-btn").textContent = "Run demo";
        });
    });

    pollBoard();
    setInterval(pollBoard, BOARD_POLL_MS);
    setInterval(tickClock, CLOCK_TICK_MS);
    connect();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();


/* --- live tech check -----------------------------------------------------
   Everything else on this page is a replay of a recorded run. This button is
   the exception: it sends a rendered frame to Gemini on Vertex AI and waits
   for a real verdict. It is capped server-side, so it cannot be hammered. */
(function () {
  const btn = document.getElementById('check-btn');
  const report = document.getElementById('report');
  if (!btn || !report) return;

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'Gemini is looking…';
    try {
      const res = await fetch('/api/tech-check', { method: 'POST' });
      const data = await res.json();
      render(data);
    } catch (err) {
      render({ ok: false, error: String(err) });
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  function render(d) {
    const empty = report.querySelector('.empty');
    if (empty) empty.remove();

    const card = document.createElement('div');
    card.className = 'tcheck';

    if (!d.ok) {
      card.innerHTML =
        '<div></div><div><p class="tcheck__verdict is-defect">Tech check unavailable</p>' +
        '<p class="tcheck__evidence"></p></div>';
      card.querySelector('.tcheck__evidence').textContent = d.error || 'unknown error';
      report.prepend(card);
      return;
    }

    const defect = d.verdict !== 'clean';
    const shot = document.createElement('img');
    shot.src = d.image;
    shot.alt = d.shot_id + ' frame ' + d.frame;

    const body = document.createElement('div');
    const h = document.createElement('p');
    h.className = 'tcheck__verdict ' + (defect ? 'is-defect' : 'is-clean');
    h.textContent = (defect ? 'Reject · ' : 'Pass · ') + d.verdict.replace(/_/g, ' ');
    if (d.live) {
      const tag = document.createElement('span');
      tag.className = 'tcheck__live';
      tag.textContent = 'live · ' + (d.via || 'gemini');
      h.appendChild(tag);
    }

    const meta = document.createElement('p');
    meta.className = 'tcheck__meta';
    meta.textContent =
      d.shot_id + ' f' + String(d.frame).padStart(4, '0') +
      ' · ' + Math.round((d.confidence || 0) * 100) + '% confidence' +
      ' · ' + (d.model || '') +
      (d.cached ? ' · cached (daily cap reached)' : '') +
      (typeof d.remaining === 'number' ? ' · ' + d.remaining + ' checks left today' : '');

    const ev = document.createElement('p');
    ev.className = 'tcheck__evidence';
    ev.textContent = d.evidence || '';

    body.append(h, meta, ev);
    card.append(shot, body);
    report.prepend(card);
  }
})();
