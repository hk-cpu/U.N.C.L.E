/* cardiag UI. Vanilla JS, no build step - it ships inside a pip package.
   All rendering goes through text nodes or explicit element creation; nothing
   from the vehicle or the code database is ever interpolated as HTML. */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const token = new URLSearchParams(location.search).get("token");

  const state = {
    connected: false,
    sampling: false,
    liveTimer: null,
    codes: [],
    tiles: new Map(),
  };

  /* ---------------------------------------------------------------- api */

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    if (token) headers["X-Cardiag-Token"] = token;

    let response;
    try {
      response = await fetch(path, { headers, ...options });
    } catch {
      // The shell is cached, so the app can be open with nothing behind it.
      throw new Error(
        "Cannot reach cardiag. The app is running from its offline cache - "
        + "start it again on the machine with the adapter plugged in.");
    }

    let payload = {};
    try {
      payload = await response.json();
    } catch {
      throw new Error(`server returned ${response.status}`);
    }
    if (!response.ok || payload.error) {
      throw new Error(payload.error || `server returned ${response.status}`);
    }
    return payload;
  }

  const get = (path) => api(path);
  const post = (path, body) =>
    api(path, { method: "POST", body: JSON.stringify(body || {}) });

  /* ------------------------------------------------------------ helpers */

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  const SEVERITY_ICON = {
    critical: "◆",   // filled diamond
    serious: "▲",    // triangle
    moderate: "●",   // circle
    advisory: "■",   // square
    info: "✓",       // check
  };

  // Status colour never travels alone: every badge carries an icon and a word.
  function badge(severity) {
    const node = el("span", `badge sev-${severity}`);
    node.appendChild(el("span", "badge-icon", SEVERITY_ICON[severity] || "●"));
    node.appendChild(el("span", null, severity));
    return node;
  }

  let toastTimer = null;
  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 3200);
  }

  function busy(button, on, label) {
    button.disabled = on;
    if (on) {
      button.dataset.label = button.textContent;
      clear(button);
      button.appendChild(el("span", "spinner"));
      button.appendChild(el("span", null, label || "Working"));
    } else if (button.dataset.label) {
      button.textContent = button.dataset.label;
    }
  }

  /* -------------------------------------------------------------- theme */

  const THEME_KEY = "cardiag-theme";
  function applyTheme(theme) {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
  }
  try {
    applyTheme(localStorage.getItem(THEME_KEY));
  } catch { /* private mode; the OS setting still applies */ }

  $("theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = current ? (current === "dark" ? "light" : "dark")
                         : (dark ? "light" : "dark");
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
  });

  /* --------------------------------------------------------------- tabs */

  function showView(name) {
    document.querySelectorAll(".view").forEach((view) => {
      view.classList.toggle("is-active", view.id === `view-${name}`);
    });
    document.querySelectorAll(".tab").forEach((tab) => {
      const active = tab.dataset.view === name;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    // Sampling only makes sense while its view is on screen.
    if (name !== "assistant" && assistant.watching) stopWatching();
    if (name !== "live" && state.sampling && !gauges.running
        && !assistant.watching) stopLive();
    if (name !== "gauges" && gauges.running) stopGauges();
    if (name === "vehicle") loadVehicle();
    if (name === "baselines") loadBaselines();
  }

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (!tab.disabled) showView(tab.dataset.view);
    });
  });

  /* --------------------------------------------------------- connection */

  async function refreshStatus() {
    const status = await get("/api/status");
    state.connected = status.connected;

    $("link-dot").className = `dot ${status.connected ? "dot-on" : "dot-off"}`;
    const bits = [];
    if (status.connected) {
      bits.push(status.protocol || "connected");
      if (status.battery_voltage != null) {
        bits.push(`${status.battery_voltage.toFixed(1)} V`);
      }
      if (status.profile) bits.push(status.profile);
    } else {
      bits.push("Not connected");
    }
    $("link-text").textContent = bits.join("  ·  ");
    $("disconnect").hidden = !status.connected;

    document.querySelectorAll("[data-needs-car]").forEach((tab) => {
      tab.disabled = !status.connected;
    });

    const select = $("vehicle-select");
    if (select.options.length <= 2 && status.profiles) {
      status.profiles.forEach((key) => {
        const option = el("option", null, key);
        option.value = key;
        select.appendChild(option);
      });
    }
    return status;
  }

  async function loadPorts() {
    const select = $("port-select");
    clear(select);

    let ports = [];
    try {
      ports = (await get("/api/ports")).ports;
    } catch { /* fall through to the simulator options */ }

    const likely = ports.filter((p) => p.likely);
    ports.forEach((port) => {
      const option = el("option", null,
        `${port.device} — ${port.description}${port.likely ? "  (likely adapter)" : ""}`);
      option.value = port.device;
      select.appendChild(option);
    });

    const manual = el("option", null, "Enter manually…");
    manual.value = "__manual__";
    select.appendChild(manual);

    const hint = $("port-hint");
    if (!ports.length) {
      hint.textContent = "No serial ports found. Plug the adapter in and press Refresh, "
        + "or try a simulated car below.";
    } else if (!likely.length) {
      hint.textContent = "None of these look like an OBD adapter, but if yours is "
        + "listed, pick it anyway.";
    } else {
      hint.textContent = "";
      select.value = likely[0].device;
    }
    onPortChange();
  }

  function onPortChange() {
    $("manual-field").hidden = $("port-select").value !== "__manual__";
  }
  $("port-select").addEventListener("change", onPortChange);
  $("refresh-ports").addEventListener("click", loadPorts);

  async function connect(url) {
    const button = $("connect");
    const error = $("connect-error");
    error.hidden = true;
    busy(button, true, "Connecting");

    try {
      await post("/api/connect", { url, vehicle: $("vehicle-select").value });
      await refreshStatus();
      toast("Connected");
      // Land on the plain-language read of the car rather than the raw scan.
      // Both run the same underlying report; this one leads with what it means.
      showView("assistant");
      explainCar();
    } catch (exc) {
      error.textContent = exc.message;
      error.hidden = false;
    } finally {
      busy(button, false);
    }
  }

  $("connect").addEventListener("click", () => {
    const choice = $("port-select").value;
    connect(choice === "__manual__" ? $("manual-url").value.trim() : choice);
  });

  $("disconnect").addEventListener("click", async () => {
    stopLive();
    await post("/api/disconnect");
    await refreshStatus();
    showView("connect");
    toast("Disconnected");
  });

  const SIMS = [
    ["Healthy car", "sim://"],
    ["Check-engine light", "sim://?profile=faulty"],
    ["Monitors not ready", "sim://?profile=emissions"],
    ["Charger R/T — healthy", "sim://?profile=charger"],
    ["Charger R/T — misfire", "sim://?profile=charger-misfire"],
  ];
  SIMS.forEach(([label, url]) => {
    const chip = el("button", "chip", label);
    chip.type = "button";
    chip.addEventListener("click", () => connect(url));
    $("sim-chips").appendChild(chip);
  });

  /* --------------------------------------------------------- health scan */

  async function runScan() {
    const button = $("run-scan");
    const body = $("scan-body");
    busy(button, true, "Scanning");
    $("scan-meta").textContent = "";

    try {
      const report = await renderScan(await get("/api/scan"));
      $("scan-meta").textContent = `Scanned ${new Date().toLocaleTimeString()}`;
      return report;
    } catch (exc) {
      clear(body).appendChild(alertBox(exc.message));
    } finally {
      busy(button, false);
    }
  }
  $("run-scan").addEventListener("click", runScan);

  function alertBox(message) {
    return el("div", "alert alert-critical", message);
  }

  async function renderScan(report) {
    const body = clear($("scan-body"));

    // The hero: exactly one big number/statement per view.
    const hero = el("div", `hero hero-${report.worst_severity}`);
    hero.appendChild(el("h2", "hero-headline", report.headline));
    const sub = [];
    if (report.vehicle.vin) sub.push(`VIN ${report.vehicle.vin}`);
    if (report.vehicle.protocol) sub.push(report.vehicle.protocol);
    if (report.profile) sub.push(report.profile.name);
    hero.appendChild(el("p", "hero-sub", sub.join("  ·  ")));
    body.appendChild(hero);

    // Findings
    const findings = el("div", "section");
    findings.appendChild(el("h3", null, `Findings (${report.findings.length})`));
    report.findings.forEach((finding) => {
      const card = el("div", "finding");
      const head = el("div", "finding-head");
      head.appendChild(badge(finding.severity));
      head.appendChild(el("div", "finding-title", finding.title));
      card.appendChild(head);
      card.appendChild(el("p", "finding-detail", finding.detail));
      if (finding.suggestion) {
        card.appendChild(el("p", "finding-suggest", finding.suggestion));
      }
      findings.appendChild(card);
    });
    body.appendChild(findings);

    if (report.status) body.appendChild(readinessSection(report.status));
    if (Object.keys(report.readings).length) {
      body.appendChild(tableSection("Live data", Object.values(report.readings)
        .map((r) => [r.description, r.formatted])));
    }
    if (Object.keys(report.freeze_frame).length) {
      body.appendChild(tableSection(
        "Freeze frame — conditions when the fault was stored",
        Object.entries(report.freeze_frame).map(([key, entry]) =>
          [key === "trigger_code" ? "Triggered by" : entry.description,
           entry.formatted])));
    }
    return report;
  }

  function readinessSection(status) {
    const section = el("div", "section");
    section.appendChild(el("h3", null, "Emissions readiness"));

    const rows = Object.entries(status.monitors)
      .map(([name, value]) => [name.replace(/_/g, " "), value]);
    rows.unshift(["Warning light", status.mil_on ? "ON" : "off"]);
    rows.push([
      "Pre-check",
      status.emissions_ready ? "would pass" : "would fail — monitors incomplete",
    ]);
    section.appendChild(tableBody(rows));
    return section;
  }

  function tableSection(title, rows) {
    const section = el("div", "section");
    section.appendChild(el("h3", null, title));
    section.appendChild(tableBody(rows));
    return section;
  }

  function tableBody(rows) {
    const wrap = el("div", "table-wrap");
    const table = el("table", "data");
    rows.forEach(([label, value]) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, label));
      tr.appendChild(el("td", null, value));
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    return wrap;
  }

  /* ----------------------------------------------------------- live view */

  async function startLive() {
    try {
      await post("/api/live/start", {});
      state.sampling = true;
      $("live-toggle").textContent = "Stop";
      state.tiles.clear();
      clear($("live-tiles"));
      pollLive();
      state.liveTimer = setInterval(pollLive, 400);
    } catch (exc) {
      clear($("live-tiles")).appendChild(alertBox(exc.message));
    }
  }

  async function stopLive() {
    clearInterval(state.liveTimer);
    state.liveTimer = null;
    if (state.sampling) {
      state.sampling = false;
      $("live-toggle").textContent = "Start";
      try { await post("/api/live/stop"); } catch { /* already gone */ }
    }
  }

  $("live-toggle").addEventListener("click", () => {
    state.sampling ? stopLive() : startLive();
  });

  async function pollLive() {
    let snapshot;
    try {
      snapshot = await get("/api/live");
    } catch {
      return;                       // a dropped poll is not worth a banner
    }
    $("live-meta").textContent =
      `${snapshot.samples} samples  ·  ${snapshot.channels.length} channels`;
    snapshot.channels.forEach(renderTile);
  }

  function renderTile(channel) {
    let tile = state.tiles.get(channel.name);
    if (!tile) {
      tile = buildTile(channel);
      state.tiles.set(channel.name, tile);
      $("live-tiles").appendChild(tile.root);
    }

    const hasValue = channel.formatted != null;
    tile.root.classList.toggle("tile-stale", !hasValue);
    tile.value.textContent = hasValue ? channel.formatted : "—";

    const { value, range_low: low, range_high: high } = channel;
    if (value != null && low != null && high != null && high > low) {
      const pct = Math.max(0, Math.min(1, (value - low) / (high - low)));
      tile.fill.style.width = `${(pct * 100).toFixed(1)}%`;
      tile.meter.className = `meter meter-${channel.severity}`;
    }

    if (channel.min != null && channel.max != null && channel.max > channel.min) {
      tile.extremes.textContent =
        `${format(channel.min)} – ${format(channel.max)}`;
    }
    drawSpark(tile.spark, channel.history);
  }

  function format(number) {
    if (!Number.isFinite(number)) return "—";
    return Math.abs(number) >= 100 ? number.toFixed(0) : number.toFixed(1);
  }

  function buildTile(channel) {
    const root = el("div", "tile");
    const label = el("div", "tile-label", channel.description);
    const value = el("div", "tile-value", "—");

    const meter = el("div", "meter");
    const fill = el("div", "meter-fill");
    fill.style.width = "0%";
    meter.appendChild(fill);

    const spark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    spark.setAttribute("class", "spark");
    spark.setAttribute("preserveAspectRatio", "none");
    spark.setAttribute("viewBox", "0 0 100 26");
    spark.setAttribute("aria-hidden", "true");

    const foot = el("div", "tile-foot");
    const extremes = el("span", null, "");
    foot.appendChild(extremes);
    foot.appendChild(el("span", null, channel.unit || ""));

    root.append(label, value, meter, spark, foot);
    return { root, value, meter, fill, spark, extremes };
  }

  // 2px line, de-emphasised, no axes - a sparkline shows shape, not values.
  function drawSpark(svg, history) {
    clear(svg);
    if (!history || history.length < 2) return;

    const low = Math.min(...history);
    const high = Math.max(...history);
    const span = high - low || 1;
    const step = 100 / (history.length - 1);

    const points = history
      .map((value, index) =>
        `${(index * step).toFixed(2)},${(24 - ((value - low) / span) * 22).toFixed(2)}`)
      .join(" ");

    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    line.setAttribute("points", points);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "currentColor");
    line.setAttribute("stroke-width", "2");
    line.setAttribute("stroke-linejoin", "round");
    line.setAttribute("stroke-linecap", "round");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    line.setAttribute("opacity", "0.55");
    svg.appendChild(line);
  }

  /* --------------------------------------------------------------- codes */

  async function readCodes() {
    const button = $("read-codes");
    const body = $("codes-body");
    busy(button, true, "Reading");

    try {
      const payload = await get("/api/codes");
      state.codes = payload.codes;
      renderCodes(payload);
    } catch (exc) {
      clear(body).appendChild(alertBox(exc.message));
    } finally {
      busy(button, false);
    }
  }
  $("read-codes").addEventListener("click", readCodes);

  function renderCodes(payload) {
    const body = clear($("codes-body"));
    const codes = payload.codes;
    $("clear-codes").hidden = codes.length === 0;

    if (!codes.length) {
      $("codes-meta").textContent = "";
      const ok = el("div", "hero hero-info");
      ok.appendChild(el("h2", "hero-headline", "No trouble codes stored"));
      body.appendChild(ok);
      return;
    }

    $("codes-meta").textContent = `${codes.length} code(s)`;
    codes.forEach((code) => {
      const card = el("div", "code-card");
      const head = el("div", "code-head");
      head.appendChild(badge(code.severity));
      head.appendChild(el("span", "code-id", code.code));
      head.appendChild(el("span", "code-desc", code.description));
      card.appendChild(head);
      card.appendChild(el("p", "code-meta",
        `${code.status} · ${code.system} · ${code.origin}`));

      const notes = payload.profile_notes[code.code];
      if (notes) {
        const parts = [notes.located, notes.note].filter(Boolean);
        if (notes.issues && notes.issues.length) {
          parts.push(`Known issue on this model: ${notes.issues.join("; ")}.`);
        }
        if (parts.length) card.appendChild(el("p", "note", parts.join(" ")));
      }

      if (code.causes.length) {
        const list = el("ul", "causes");
        code.causes.forEach((cause) => list.appendChild(el("li", null, cause)));
        card.appendChild(list);
      }
      body.appendChild(card);
    });
  }

  $("clear-codes").addEventListener("click", () => {
    const list = clear($("modal-codes"));
    state.codes.forEach((code) => {
      list.appendChild(el("li", null, `${code.code} — ${code.description}`));
    });
    $("modal").hidden = false;
  });

  $("modal-cancel").addEventListener("click", () => { $("modal").hidden = true; });
  $("modal").addEventListener("click", (event) => {
    if (event.target === $("modal")) $("modal").hidden = true;
  });

  $("modal-confirm").addEventListener("click", async () => {
    const button = $("modal-confirm");
    busy(button, true, "Erasing");
    try {
      await post("/api/clear", { confirm: "clear" });
      $("modal").hidden = true;
      toast("Codes cleared");
      readCodes();
    } catch (exc) {
      toast(exc.message);
    } finally {
      busy(button, false);
    }
  });

  /* ------------------------------------------------------ gauge cluster */

  const SVG_NS = "http://www.w3.org/2000/svg";
  const DIAL_START = 140;          // degrees; sweeps clockwise from lower left
  const DIAL_SWEEP = 260;

  const gauges = {
    running: false,
    timer: null,
    wakeLock: null,
    engine: {},
    dials: {},
  };

  function svg(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) =>
      node.setAttribute(key, String(value)));
    return node;
  }

  // Polar helper: 0 at the dial's start, 1 at its end.
  function dialPoint(fraction, radius) {
    const angle = ((DIAL_START + fraction * DIAL_SWEEP) * Math.PI) / 180;
    return [100 + Math.cos(angle) * radius, 100 + Math.sin(angle) * radius];
  }

  function arcPath(from, to, radius) {
    const [x1, y1] = dialPoint(from, radius);
    const [x2, y2] = dialPoint(to, radius);
    const large = (to - from) * DIAL_SWEEP > 180 ? 1 : 0;
    return `M ${x1} ${y1} A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2}`;
  }

  function buildDial(element, { max, redline, step, per = 1 }) {
    clear(element);
    // A narrow band pushed to the edge leaves the numbers room to sit on their
    // own radius. Adjacent labels crowd each other otherwise - 1000 and 2000
    // are only 32 degrees apart at the top of the sweep.
    const radius = 84;
    const width = 11;

    element.appendChild(svg("path", {
      d: arcPath(0, 1, radius), class: "dial-track",
      "stroke-width": width, "stroke-linecap": "round",
    }));

    // The red zone is drawn once, underneath, so its position is fixed the way
    // it is on a real dial rather than appearing only when you reach it.
    if (redline && redline < max) {
      element.appendChild(svg("path", {
        d: arcPath(redline / max, 1, radius), class: "dial-redline",
        "stroke-width": width, "stroke-linecap": "round",
      }));
    }

    const arc = svg("path", {
      d: arcPath(0, 0.0001, radius), class: "dial-arc",
      "stroke-width": width, "stroke-linecap": "round",
    });
    element.appendChild(arc);

    for (let value = 0; value <= max; value += step) {
      const fraction = value / max;
      const [ox, oy] = dialPoint(fraction, radius - width / 2 - 3);
      const [ix, iy] = dialPoint(fraction, radius - width / 2 - 9);
      element.appendChild(svg("line", {
        x1: ox, y1: oy, x2: ix, y2: iy, class: "dial-tick", "stroke-width": 2,
      }));

      // Tachometers count in thousands for a reason: "8" fits where "8000"
      // runs into the band beside it.
      const text = String(value / per);
      // A label out at 3 o'clock reaches sideways towards the band, so the
      // wider it is the further in it has to sit. Vertically it never does.
      const inset = text.length * 3.4;
      const labelRadius = radius - width - 12;
      const [, ly] = dialPoint(fraction, labelRadius);
      const [lx] = dialPoint(fraction, labelRadius - inset);

      const label = svg("text", { x: lx, y: ly + 4, class: "dial-label" });
      label.textContent = text;
      element.appendChild(label);
    }

    // The centre readout is the true number, so the dial has to say what its
    // own numbers are counting in.
    if (per !== 1) {
      const scale = svg("text", { x: 100, y: 180, class: "dial-scale" });
      scale.textContent = `×${per}`;
      element.appendChild(scale);
    }

    return { arc, max, radius };
  }

  function setDial(dial, value, severity) {
    if (!dial) return;
    const fraction = Math.max(0, Math.min(1, (value || 0) / dial.max));
    dial.arc.setAttribute("d", arcPath(0, Math.max(fraction, 0.0001), dial.radius));
    dial.arc.classList.toggle("is-warning", severity === "warning");
    dial.arc.classList.toggle("is-critical", severity === "critical");
  }

  async function startGauges() {
    const button = $("gauge-toggle");
    busy(button, true, "Starting");
    try {
      const started = await post("/api/gauges/start", {});
      gauges.engine = started.engine || {};
      gauges.running = true;
      state.sampling = true;

      const redline = gauges.engine.redline_rpm;
      // Round the dial up to a whole thousand past the red zone.
      const rpmMax = Math.ceil(((redline || 7000) + 700) / 1000) * 1000;

      gauges.dials.rpm = buildDial($("dial-rpm"),
        { max: rpmMax, redline, step: 1000, per: 1000 });
      gauges.dials.speed = buildDial($("dial-speed"),
        { max: 220, redline: null, step: 40 });

      Object.assign(sprint,
        { armed: false, startedAt: null, previous: null, best: null });
      $("sprint-value").textContent = "—";

      $("cluster").hidden = false;
      $("gauge-hint").hidden = true;
      $("gauge-toggle").textContent = "Stop";
      await requestWakeLock();

      pollGauges();
      gauges.timer = setInterval(pollGauges, 120);
    } catch (exc) {
      toast(exc.message);
    } finally {
      busy(button, false);
      if (gauges.running) $("gauge-toggle").textContent = "Stop";
    }
  }

  async function stopGauges() {
    clearInterval(gauges.timer);
    gauges.timer = null;
    if (!gauges.running) return;

    gauges.running = false;
    state.sampling = false;
    $("gauge-toggle").textContent = "Start";
    releaseWakeLock();
    try { await post("/api/live/stop"); } catch { /* already stopped */ }
  }

  $("gauge-toggle").addEventListener("click", () => {
    gauges.running ? stopGauges() : startGauges();
  });

  $("gauge-fullscreen").addEventListener("click", () => {
    const cluster = $("cluster");
    if (document.fullscreenElement) document.exitFullscreen();
    else cluster.requestFullscreen?.().catch(() => toast("Full screen was refused"));
  });

  // A dashboard that dims after 30 seconds is not a dashboard.
  async function requestWakeLock() {
    try {
      gauges.wakeLock = await navigator.wakeLock?.request("screen");
    } catch { /* unsupported, or denied on an unfocused tab */ }
  }

  function releaseWakeLock() {
    try { gauges.wakeLock?.release(); } catch { /* already gone */ }
    gauges.wakeLock = null;
  }

  // The lock is dropped whenever the tab is hidden; take it back on return.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && gauges.running) requestWakeLock();
  });

  const READOUTS = [
    ["COOLANT_TEMP", "Coolant"],
    ["OIL_TEMP", "Oil"],
    ["CONTROL_MODULE_VOLTAGE", "Volts"],
    ["INTAKE_TEMP", "Intake"],
  ];

  async function pollGauges() {
    let snapshot;
    try {
      snapshot = await get("/api/live");
    } catch {
      return;                      // a dropped poll is not worth a banner
    }

    const byName = {};
    snapshot.channels.forEach((channel) => { byName[channel.name] = channel; });

    const rpm = byName.RPM?.value;
    const speed = byName.SPEED?.value;

    setDial(gauges.dials.rpm, rpm, rpmSeverity(rpm));
    setDial(gauges.dials.speed, speed, "neutral");

    $("rpm-value").textContent = rpm == null ? "—" : Math.round(rpm).toLocaleString();
    $("speed-value").textContent = speed == null ? "—" : Math.round(speed);

    updateShiftLight(rpm);
    updateSprint(speed, snapshot.at);
    renderReadouts(byName);

    $("gauge-meta").textContent = `${snapshot.samples} samples`;
  }

  /* The 0-100 timer the build guide pairs with the shift light. It runs off the
     same poll stream, so it is honest about being indicative: an ELM327 answers
     a speed request roughly every 50 ms at best, and the browser sees every
     other one. Interpolating across the 100 km/h crossing recovers some of
     that, but this is not a drag box and the caption says so. */
  const SPRINT_TARGET = 100;
  const sprint = { armed: false, startedAt: null, previous: null, best: null };

  function updateSprint(speed, at) {
    if (speed == null || at == null) return;
    const previous = sprint.previous;
    sprint.previous = { speed, at };

    if (speed === 0) {
      sprint.armed = true;                 // stopped: ready for the next run
      sprint.startedAt = at;
      return;
    }
    if (!sprint.armed) return;

    if (sprint.startedAt === null) return;
    if (speed < SPRINT_TARGET) return;

    // Cross the line between the last sample below the target and this one.
    let crossedAt = at;
    if (previous && previous.speed < SPRINT_TARGET && at > previous.at) {
      const span = speed - previous.speed;
      const share = span > 0 ? (SPRINT_TARGET - previous.speed) / span : 0;
      crossedAt = previous.at + (at - previous.at) * share;
    }

    const seconds = crossedAt - sprint.startedAt;
    sprint.armed = false;                  // one result per launch
    if (!(seconds > 0)) return;
    if (sprint.best === null || seconds < sprint.best) sprint.best = seconds;

    $("sprint-value").textContent = `${seconds.toFixed(2)} s`;
    $("sprint-note").textContent = sprint.best < seconds
      ? `Best this session ${sprint.best.toFixed(2)} s. Indicative only.`
      : "Indicative only - timed from the poll stream, not a drag box.";
  }

  function rpmSeverity(rpm) {
    const { redline, shift } = { redline: gauges.engine.redline_rpm,
                                 shift: gauges.engine.shift_rpm };
    if (rpm == null) return "neutral";
    if (redline && rpm >= redline) return "critical";
    if (shift && rpm >= shift) return "warning";
    return "neutral";
  }

  function updateShiftLight(rpm) {
    const light = $("shift-light");
    const { redline_rpm: redline, shift_rpm: shift } = gauges.engine;
    light.classList.toggle("is-armed",
      Boolean(shift && rpm != null && rpm >= shift && (!redline || rpm < redline)));
    light.classList.toggle("is-lit",
      Boolean(redline && rpm != null && rpm >= redline));
  }

  function renderReadouts(byName) {
    const host = $("readouts");
    if (!host.childElementCount) {
      READOUTS.forEach(([name, label]) => {
        const cell = el("div", "readout");
        cell.dataset.name = name;
        cell.appendChild(el("div", "readout-label", label));
        cell.appendChild(el("div", "readout-value", "—"));
        host.appendChild(cell);
      });
    }

    READOUTS.forEach(([name]) => {
      const cell = host.querySelector(`[data-name="${name}"]`);
      const channel = byName[name];
      const value = cell.querySelector(".readout-value");
      value.textContent = channel?.formatted ?? "—";

      const severity = channel?.severity || "neutral";
      cell.classList.toggle("is-warning", severity === "warning");
      cell.classList.toggle("is-critical", severity === "critical");
    });
  }

  /* ------------------------------------------------------------ assistant */

  // "good" means actively fine; "info" means there is nothing to judge yet - a
  // cold engine's trims, for instance. A green tick on the second would claim
  // more than the data supports, so info gets a neutral ring of its own.
  const CONDITION_ICON = { ...SEVERITY_ICON, good: "✓", info: "○" };

  // How the adapter is attached. Said in words as well as drawn, because the
  // symbol alone does not distinguish a cable from a pairing.
  const LINK_ICON = {
    usb: "⭘", bluetooth: "✳", wifi: "◈", serial: "⭘", simulated: "◌",
  };

  const SEVERITY_RANK = ["critical", "serious", "moderate", "advisory",
                         "info", "good"];
  const worseOf = (a, b) =>
    SEVERITY_RANK.indexOf(a) <= SEVERITY_RANK.indexOf(b) ? a : b;

  const assistant = { watching: false, timer: null, scan: null };

  async function explainCar() {
    const button = $("explain");
    busy(button, true, "Scanning");
    try {
      const payload = await get("/api/assistant");
      assistant.scan = payload;
      renderVerdict(payload);
      $("assistant-meta").textContent = "full scan";
    } catch (exc) {
      toast(exc.message);
    } finally {
      busy(button, false);
    }
  }

  /* A live pass only sees what the sampler is reading. Stored codes, mode 06
     wear and readiness came from the scan and are still true a second later,
     so they are carried forward rather than dropped - otherwise starting the
     commentary would silently retract a misfire warning. */
  function mergeVerdict(live) {
    const scan = assistant.scan;
    if (!scan) return live;

    const fresh = new Set((live.conditions || []).map((c) => c.topic));
    const carried = (scan.conditions || []).filter((c) => !fresh.has(c.topic));
    const conditions = [...(live.conditions || []), ...carried];

    const severity = conditions.reduce(
      (worst, c) => worseOf(worst, c.severity), "good");
    // The scan's summary is the better sentence - it names the car and weighs
    // codes and wear as well as live readings. The live one only takes over
    // when the engine has done something worse since the scan ran.
    const summary = SEVERITY_RANK.indexOf(live.severity)
      < SEVERITY_RANK.indexOf(scan.severity) ? live.summary : scan.summary;

    return { ...live, conditions, severity, summary, actions: scan.actions };
  }
  $("explain").addEventListener("click", explainCar);

  function renderVerdict(payload) {
    const verdict = $("verdict");
    verdict.hidden = false;
    verdict.className = `verdict sev-${payload.severity}`;
    $("verdict-icon").textContent = CONDITION_ICON[payload.severity] || "●";
    $("verdict-summary").textContent = payload.summary;

    const link = payload.link || {};
    $("verdict-link").textContent = link.label
      ? `${LINK_ICON[link.kind] || "⭘"}  Connected over ${link.label} — ${link.detail}`
      : "";

    const host = clear($("conditions"));
    (payload.conditions || []).forEach((condition) => {
      const card = el("div", `condition sev-${condition.severity}`);

      const head = el("div", "condition-head");
      head.appendChild(el("span", "condition-icon",
        CONDITION_ICON[condition.severity] || "●"));
      head.appendChild(el("span", "condition-topic", condition.topic));
      head.appendChild(el("span", "condition-state", condition.state));
      card.appendChild(head);

      card.appendChild(el("p", "condition-detail", condition.detail));

      if (condition.evidence?.length) {
        const evidence = el("div", "evidence");
        condition.evidence.slice(0, 8).forEach((item) =>
          evidence.appendChild(el("span", "evidence-item", item)));
        card.appendChild(evidence);
      }
      host.appendChild(card);
    });

    const actions = payload.actions || [];
    $("actions-card").hidden = actions.length === 0;
    const list = clear($("actions"));
    actions.forEach((action) => list.appendChild(el("li", null, action)));

    $("assistant-hint").hidden = true;
  }

  async function startWatching() {
    // The commentary needs the sampler running to have anything to read.
    if (!state.sampling) {
      try {
        await post("/api/live/start", {});
        state.sampling = true;
      } catch (exc) {
        toast(exc.message);
        return;
      }
    }
    assistant.watching = true;
    $("watch-toggle").textContent = "Stop watching";
    pollCommentary();
    assistant.timer = setInterval(pollCommentary, 2000);
  }

  async function stopWatching() {
    clearInterval(assistant.timer);
    assistant.timer = null;
    if (!assistant.watching) return;

    assistant.watching = false;
    $("watch-toggle").textContent = "Watch live";
    state.sampling = false;
    try { await post("/api/live/stop"); } catch { /* already stopped */ }
  }

  $("watch-toggle").addEventListener("click", () => {
    assistant.watching ? stopWatching() : startWatching();
  });

  async function pollCommentary() {
    try {
      const payload = await get("/api/assistant/live");
      renderVerdict(mergeVerdict(payload));
      $("assistant-meta").textContent = assistant.scan
        ? "watching live · scan carried forward" : "watching live";
    } catch {
      /* a dropped poll is not worth a banner */
    }
  }

  /* -------------------------------------------------------- mode 06 tests */

  async function readTests() {
    const button = $("read-tests");
    const body = $("tests-body");
    busy(button, true, "Reading");

    try {
      const payload = await get("/api/tests");
      renderTests(payload);
    } catch (exc) {
      clear(body).appendChild(alertBox(exc.message));
    } finally {
      busy(button, false);
    }
  }
  $("read-tests").addEventListener("click", readTests);

  function renderTests(payload) {
    const body = clear($("tests-body"));
    const tests = payload.tests || [];
    const counts = payload.misfire_counts || {};
    const deactivated = new Set(payload.deactivated_cylinders || []);

    $("tests-meta").textContent = `${tests.length} monitors`;

    if (!tests.length) {
      body.appendChild(el("p", "muted",
        "This ECU returned no mode 06 results. Some cars only answer once a "
        + "drive cycle has run the monitors."));
      return;
    }

    const cylinders = Object.keys(counts).map(Number).sort((a, b) => a - b);
    if (cylinders.length) {
      const section = el("div", "section");
      section.appendChild(el("h3", null, "Misfire counts by cylinder"));

      const highest = Math.max(...cylinders.map((c) => counts[c]), 1);
      cylinders.forEach((cylinder) => {
        const count = counts[cylinder];
        const row = el("div", "bar-row");
        const label = el("div", "bar-label", `Cylinder ${cylinder}`);
        if (deactivated.has(cylinder)) {
          label.appendChild(el("span", "bar-tag", "deactivated at cruise"));
        }
        row.appendChild(label);

        const track = el("div", "bar-track");
        const fill = el("div",
          `bar-fill${deactivated.has(cylinder) ? " bar-fill-group" : ""}`);
        fill.style.width = `${(count / highest) * 100}%`;
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el("div", "bar-value", String(count)));
        section.appendChild(row);
      });

      section.appendChild(el("p", "hint",
        "Counts only mean something against each other. One cylinder well above "
        + "the rest is a fault on that cylinder; a whole group standing out "
        + "points at whatever that group shares."));
      body.appendChild(section);
    }

    const others = tests.filter((t) => t.cylinder === null);
    if (others.length) {
      const section = el("div", "section");
      section.appendChild(el("h3", null, "Other monitors"));

      const wrap = el("div", "table-wrap");
      const table = el("table", "data");
      const head = el("tr");
      ["monitor", "measured", "limits", "of limit"].forEach((title) => {
        const th = el("th", null, title);
        head.appendChild(th);
      });
      table.appendChild(head);

      others.forEach((test) => {
        const tr = el("tr");
        tr.appendChild(el("td", null, test.monitor));
        tr.appendChild(el("td", null, test.formatted));
        tr.appendChild(el("td", null, test.limits));

        const cell = el("td", null,
          test.headroom === null ? "—" : `${(test.headroom * 100).toFixed(0)} %`);
        if (test.passed === false) cell.className = "cell-critical";
        else if (test.headroom !== null && test.headroom >= 0.85) {
          cell.className = "cell-warning";
        }
        tr.appendChild(cell);
        table.appendChild(tr);
      });
      wrap.appendChild(table);
      section.appendChild(wrap);
      body.appendChild(section);
    }
  }

  /* ----------------------------------------------------------- baselines */

  async function loadBaselines() {
    const body = clear($("baselines-body"));
    let payload;
    try {
      payload = await get("/api/baselines");
    } catch (exc) {
      body.appendChild(alertBox(exc.message));
      return;
    }

    const snapshots = payload.snapshots || [];
    if (!snapshots.length) {
      body.appendChild(el("p", "muted",
        "No snapshots yet. Save one before you change anything."));
      return;
    }

    const section = el("div", "section");
    section.appendChild(el("h3", null, `${snapshots.length} saved snapshots`));

    snapshots.forEach((snapshot) => {
      const card = el("div", "finding");
      const head = el("div", "finding-head");
      head.appendChild(el("div", "finding-title", snapshot.label));
      head.appendChild(el("span", "muted", snapshot.when));
      card.appendChild(head);
      card.appendChild(el("p", "finding-detail",
        `${snapshot.headline || "no summary"}${snapshot.vin ? "  ·  VIN " + snapshot.vin : ""}`));

      const row = el("div", "row");
      const compare = el("button", "btn btn-primary", "Compare against now");
      compare.type = "button";
      compare.addEventListener("click", () => compareBaseline(snapshot.id, compare));
      row.appendChild(compare);

      const remove = el("button", "btn btn-quiet", "Delete");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        try {
          await post("/api/baselines/delete", { id: snapshot.id });
          toast("Snapshot deleted");
          loadBaselines();
        } catch (exc) {
          toast(exc.message);
        }
      });
      row.appendChild(remove);
      card.appendChild(row);
      section.appendChild(card);
    });
    body.appendChild(section);
  }

  $("save-baseline").addEventListener("click", async () => {
    const button = $("save-baseline");
    const label = $("baseline-label").value.trim()
      || new Date().toISOString().slice(0, 16).replace("T", " ");
    busy(button, true, "Scanning");
    try {
      await post("/api/baselines/save", { label });
      $("baseline-label").value = "";
      toast(`Saved "${label}"`);
      loadBaselines();
    } catch (exc) {
      toast(exc.message);
    } finally {
      busy(button, false);
    }
  });

  async function compareBaseline(id, button) {
    const body = clear($("compare-body"));
    busy(button, true, "Comparing");
    try {
      const payload = await post("/api/baselines/compare", { id });
      renderComparison(payload);
    } catch (exc) {
      body.appendChild(alertBox(exc.message));
    } finally {
      busy(button, false);
    }
  }

  function renderComparison(payload) {
    const body = clear($("compare-body"));
    const section = el("div", "section");
    section.appendChild(el("h3", null,
      `${payload.before.label} (${payload.before.when})  →  now`));
    section.appendChild(el("p", "muted", payload.summary));

    const changes = payload.changes || [];
    if (!changes.length) {
      body.appendChild(section);
      return;
    }

    const arrows = { worse: "↑", better: "↓", neutral: "·" };
    changes.forEach((change) => {
      const card = el("div", `finding change-${change.direction}`);
      const head = el("div", "finding-head");
      head.appendChild(el("span", `change-mark change-${change.direction}`,
        arrows[change.direction] || "·"));
      head.appendChild(el("div", "finding-title", change.label));
      card.appendChild(head);
      card.appendChild(el("p", "finding-detail",
        `${change.before}  →  ${change.after}`));
      if (change.note) card.appendChild(el("p", "finding-suggest", change.note));
      section.appendChild(card);
    });
    body.appendChild(section);
  }

  /* ------------------------------------------------------------- vehicle */

  async function loadVehicle() {
    const body = clear($("vehicle-body"));
    let payload;
    try {
      payload = await get("/api/vehicle");
    } catch (exc) {
      body.appendChild(alertBox(exc.message));
      return;
    }

    if (payload.vin) {
      const vin = payload.vin;
      const rows = [["VIN", vin.vin]];
      if (vin.manufacturer) rows.push(["Manufacturer", vin.manufacturer]);
      if (vin.model_year) rows.push(["Model year", String(vin.model_year)]);
      if (vin.engine) rows.push(["Engine", vin.engine]);
      if (vin.plant) rows.push(["Built at", vin.plant]);
      rows.push(["Check digit", vin.check_digit_ok ? "valid" : "INVALID"]);
      body.appendChild(tableSection("Identity", rows));
    }

    const profile = payload.profile;
    if (!profile) {
      body.appendChild(el("p", "muted",
        "No vehicle profile applies to this car, so findings stay generic."));
      return;
    }

    const engine = profile.engine;
    const rows = [
      ["Engine", engine.name],
      ["Cylinders", String(engine.cylinders)],
      ["Spark plugs", `${engine.total_plugs} (${engine.plugs_per_cylinder} per cylinder)`],
    ];
    if (engine.firing_order.length) {
      rows.push(["Firing order", engine.firing_order.join("-")]);
    }
    Object.entries(engine.bank_side).forEach(([bank, side]) => {
      rows.push([`Bank ${bank}`, side]);
    });
    if (engine.deactivated_cylinders.length) {
      rows.push(["Deactivated at cruise", engine.deactivated_cylinders.join(", ")]);
    }
    if (profile.transmission) rows.push(["Transmission", profile.transmission]);
    body.appendChild(tableSection(profile.name, rows));

    if (profile.notes.length) {
      const section = el("div", "section");
      section.appendChild(el("h3", null, "Worth knowing"));
      profile.notes.forEach((note) => {
        section.appendChild(el("p", "finding-suggest", note));
      });
      body.appendChild(section);
    }

    if (profile.known_issues.length) {
      const section = el("div", "section");
      section.appendChild(el("h3", null, "Known issues on this model"));
      profile.known_issues.forEach((issue) => {
        const card = el("div", "finding");
        const head = el("div", "finding-head");
        head.appendChild(badge(issue.severity));
        head.appendChild(el("div", "finding-title", issue.title));
        card.appendChild(head);
        card.appendChild(el("p", "finding-detail", issue.detail));
        if (issue.checks.length) {
          const list = el("ul", "causes");
          issue.checks.forEach((check) => list.appendChild(el("li", null, check)));
          card.appendChild(list);
        }
        section.appendChild(card);
      });
      body.appendChild(section);
    }
  }

  /* ------------------------------------------------------ install as app */

  // Chrome fires this instead of showing its own prompt, so the offer lives in
  // the toolbar where it is visible but not in the way.
  let installPrompt = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    $("install").hidden = false;
  });

  $("install").addEventListener("click", async () => {
    if (!installPrompt) return;
    $("install").hidden = true;
    installPrompt.prompt();
    await installPrompt.userChoice;
    installPrompt = null;
  });

  window.addEventListener("appinstalled", () => {
    $("install").hidden = true;
    installPrompt = null;
    toast("Installed — you can open cardiag from your home screen now");
  });

  // A service worker needs a secure context. localhost counts as one; a plain
  // http:// address on the network does not, so over the LAN the page still
  // works but cannot be installed. Failing here must not break the app.
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* not a secure context, or the browser declined; nothing to do */
      });
    });
  }

  /* ---------------------------------------------------------------- boot */

  window.addEventListener("beforeunload", () => {
    if (state.sampling) navigator.sendBeacon?.("/api/live/stop");
  });

  const VIEWS = ["connect", "assistant", "scan", "gauges", "live", "codes",
                 "tests", "baselines", "vehicle"];

  (async () => {
    await loadPorts();
    const status = await refreshStatus();
    if (!status.connected) return;

    // App shortcuts arrive as ?view=live and friends.
    const wanted = new URLSearchParams(location.search).get("view");
    showView(VIEWS.includes(wanted) ? wanted : "assistant");
  })();
})();
