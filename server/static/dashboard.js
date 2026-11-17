const STATUS_REFRESH_MS = 5000;
const HISTORY_REFRESH_MS = 30000;

let selectedHours = 24;
let savedFullLevel = null;
let sliderTouched = false;
let lastHistory = null;
let lastStatus = null;

function byId(id) { return document.getElementById(id); }

async function getJson(url, options) {
  const reply = await fetch(url, options);
  if (reply.status === 401) { window.location = "/login"; throw new Error("logged out"); }
  const data = await reply.json();
  if (!reply.ok) throw new Error(data.error || "request failed");
  return data;
}

function timeText(unixSeconds, withDay) {
  const date = new Date(unixSeconds * 1000);
  const options = { hour: "2-digit", minute: "2-digit" };
  if (withDay) options.weekday = "short";
  return date.toLocaleString([], options);
}

function agoText(unixSeconds) {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - unixSeconds));
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function svgElement(name, attributes, parent) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (parent) parent.appendChild(element);
  return element;
}

function makeTooltip(container) {
  let tip = container.querySelector(".tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "tooltip";
    container.appendChild(tip);
  }
  return tip;
}

function placeTooltip(tip, container, x, y) {
  tip.style.display = "block";
  const width = tip.offsetWidth;
  let left = x + 12;
  if (left + width > container.clientWidth) left = x - width - 12;
  tip.style.left = `${Math.max(0, left)}px`;
  tip.style.top = `${Math.max(0, y - 40)}px`;
}

function drawLineChart(container, points, settings) {
  const width = container.clientWidth;
  const height = container.clientHeight;
  const pad = { left: 36, right: 12, top: 12, bottom: 24 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const xStart = settings.xStart;
  const xEnd = settings.xEnd;
  const yMax = settings.yMax;

  container.querySelectorAll("svg").forEach((old) => old.remove());
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": settings.label });
  container.prepend(svg);

  const xOf = (t) => pad.left + ((t - xStart) / (xEnd - xStart)) * plotWidth;
  const yOf = (v) => pad.top + plotHeight - (Math.min(v, yMax) / yMax) * plotHeight;

  for (const tick of settings.yTicks) {
    const y = yOf(tick);
    svgElement("line", { x1: pad.left, x2: width - pad.right, y1: y, y2: y, class: tick === 0 ? "base-line" : "grid-line" }, svg);
    const label = svgElement("text", { x: pad.left - 6, y: y + 4, "text-anchor": "end", class: "tick" }, svg);
    label.textContent = tick + (settings.tickUnit || "");
  }
  drawTimeTicks(svg, xStart, xEnd, xOf, height - 6);

  if (points.length === 0) {
    const empty = svgElement("text", { x: pad.left + plotWidth / 2, y: pad.top + plotHeight / 2, "text-anchor": "middle", class: "empty" }, svg);
    empty.textContent = "No readings in this time range yet";
  } else {

    const spacing = points.length > 1 ? (points[points.length - 1].t - points[0].t) / (points.length - 1) : 10;
    const gapLimit = Math.max(90, spacing * 5);
    const pieces = [[]];
    points.forEach((point, index) => {
      if (index > 0 && point.t - points[index - 1].t > gapLimit) pieces.push([]);
      pieces[pieces.length - 1].push(point);
    });
    for (const piece of pieces) {
      const line = piece.map((p, i) => `${i ? "L" : "M"}${xOf(p.t).toFixed(1)},${yOf(p.v).toFixed(1)}`).join("");
      if (settings.area && piece.length > 1) {
        const bottom = yOf(0).toFixed(1);
        svgElement("path", { d: `${line}L${xOf(piece[piece.length - 1].t).toFixed(1)},${bottom}L${xOf(piece[0].t).toFixed(1)},${bottom}Z`, class: "series-area" }, svg);
      }
      svgElement("path", { d: piece.length > 1 ? line : `${line}l0.1,0`, class: "series-line" }, svg);
    }
  }

  if (settings.reference != null) {
    const y = yOf(settings.reference);
    svgElement("line", { x1: pad.left, x2: width - pad.right, y1: y, y2: y, class: "reference" }, svg);
    const label = svgElement("text", { x: width - pad.right, y: y - 5, "text-anchor": "end", class: "reference-label" }, svg);
    label.textContent = `Full at ${settings.reference}%`;
  }

  const crosshair = svgElement("line", { y1: pad.top, y2: pad.top + plotHeight, class: "crosshair", visibility: "hidden" }, svg);
  const dot = svgElement("circle", { r: 5, class: "hover-dot", visibility: "hidden" }, svg);
  const target = svgElement("rect", { x: pad.left, y: 0, width: Math.max(0, plotWidth), height, fill: "transparent" }, svg);
  const tip = makeTooltip(container);

  function show(event) {
    if (points.length === 0) return;
    const box = svg.getBoundingClientRect();
    const clientX = event.touches ? event.touches[0].clientX : event.clientX;
    const t = xStart + ((clientX - box.left - pad.left) / plotWidth) * (xEnd - xStart);
    let nearest = points[0];
    for (const point of points) if (Math.abs(point.t - t) < Math.abs(nearest.t - t)) nearest = point;
    const x = xOf(nearest.t);
    const y = yOf(nearest.v);
    crosshair.setAttribute("x1", x); crosshair.setAttribute("x2", x); crosshair.setAttribute("visibility", "visible");
    dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("visibility", "visible");
    tip.innerHTML = "";
    tip.append(`${timeText(nearest.t, selectedHours > 24)}  `);
    const value = document.createElement("strong");
    value.textContent = settings.format(nearest.v);
    tip.append(value);
    placeTooltip(tip, container, x, y);
  }
  function hide() {
    crosshair.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tip.style.display = "none";
  }
  target.addEventListener("mousemove", show);
  target.addEventListener("touchmove", show, { passive: true });
  target.addEventListener("touchstart", show, { passive: true });
  target.addEventListener("mouseleave", hide);
  target.addEventListener("touchend", hide);
}

function drawTimeTicks(svg, xStart, xEnd, xOf, y) {
  const span = xEnd - xStart;
  const stepChoices = [600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600];
  const step = stepChoices.find((s) => span / s <= 6) || 24 * 3600;
  const offset = new Date().getTimezoneOffset() * 60;
  let t = Math.ceil((xStart - offset) / step) * step + offset;
  for (; t <= xEnd; t += step) {
    const label = svgElement("text", { x: xOf(t), y, "text-anchor": "middle", class: "tick" }, svg);
    label.textContent = timeText(t, span > 24 * 3600 && step >= 6 * 3600);
  }
}

function drawBarChart(container, counts, bucketSeconds, xStart) {
  const width = container.clientWidth;
  const height = container.clientHeight;
  const pad = { left: 36, right: 12, top: 12, bottom: 24 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const xEnd = xStart + counts.length * bucketSeconds;
  const most = Math.max(...counts, 0);
  const yMax = Math.max(4, Math.ceil(most / 4) * 4);

  container.querySelectorAll("svg").forEach((old) => old.remove());
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Lid openings over time" });
  container.prepend(svg);

  const xOf = (t) => pad.left + ((t - xStart) / (xEnd - xStart)) * plotWidth;
  const yOf = (v) => pad.top + plotHeight - (v / yMax) * plotHeight;

  for (let tick = 0; tick <= yMax; tick += yMax / 4) {
    const y = yOf(tick);
    svgElement("line", { x1: pad.left, x2: width - pad.right, y1: y, y2: y, class: tick === 0 ? "base-line" : "grid-line" }, svg);
    const label = svgElement("text", { x: pad.left - 6, y: y + 4, "text-anchor": "end", class: "tick" }, svg);
    label.textContent = tick;
  }
  drawTimeTicks(svg, xStart, xEnd, xOf, height - 6);

  const slot = plotWidth / counts.length;
  const barWidth = Math.max(2, slot - 2);
  const tip = makeTooltip(container);

  counts.forEach((count, index) => {
    const x = pad.left + index * slot + (slot - barWidth) / 2;
    const top = yOf(count);
    const bottom = yOf(0);
    if (count > 0) {
      svgElement("rect", { class: "bar", x, y: top, width: barWidth, height: bottom - top }, svg);
    }

    const hit = svgElement("rect", { x: pad.left + index * slot, y: pad.top, width: slot, height: plotHeight, fill: "transparent" }, svg);
    const showBar = () => {
      const start = xStart + index * bucketSeconds;
      tip.innerHTML = "";
      tip.append(`${timeText(start, selectedHours > 24)} to ${timeText(start + bucketSeconds)}  `);
      const value = document.createElement("strong");
      value.textContent = `${count} opening${count === 1 ? "" : "s"}`;
      tip.append(value);
      placeTooltip(tip, container, x + barWidth / 2, top);
    };
    hit.addEventListener("mouseenter", showBar);
    hit.addEventListener("touchstart", showBar, { passive: true });
    hit.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  });
}

async function refreshStatus() {
  let status;
  try { status = await getJson("/api/status"); } catch (error) { return; }
  lastStatus = status;

  byId("online-dot").classList.toggle("online", status.online);
  byId("online-text").textContent = status.last_message_at
    ? `${status.online ? "Online" : "Offline"} · last message ${agoText(status.last_message_at)}`
    : "No messages from the bin yet";

  const latest = status.latest;
  const fill = latest && latest.fill_percent != null ? Math.round(latest.fill_percent) : null;
  byId("fill-number").textContent = fill == null ? "--%" : `${fill}%`;
  byId("fill-meter").style.width = `${fill || 0}%`;
  byId("fill-mark").style.left = `calc(${status.full_level_percent}% - 1px)`;
  byId("fill-sub").textContent = status.empty_distance_cm
    ? `Empty bin depth ${status.empty_distance_cm} cm · marker = full level`
    : "Marker = full level";

  const badge = byId("full-badge");
  badge.innerHTML = "";
  if (latest) {
    const pill = document.createElement("span");
    const icon = document.createElement("span");
    icon.className = "icon";
    icon.setAttribute("aria-hidden", "true");
    if (latest.bin_full) {
      pill.className = "badge full";
      icon.textContent = "▲";
      pill.append(icon, "Bin full: please empty it");
    } else {
      pill.className = "badge ok";
      icon.textContent = "●";
      pill.append(icon, "Not full");
    }
    badge.appendChild(pill);
  }

  savedFullLevel = status.full_level_percent;
  if (!sliderTouched) {
    byId("level-slider").value = savedFullLevel;
    byId("level-value").textContent = `${savedFullLevel}%`;
  }
  const applied = byId("level-applied");
  if (status.level_on_bin == null) {
    applied.textContent = "";
  } else if (status.level_on_bin === savedFullLevel) {
    applied.textContent = `✓ Bin is using ${status.level_on_bin}%`;
  } else {
    applied.textContent = `Bin still using ${status.level_on_bin}% (updates on its next message)`;
  }

  if (lastHistory) drawHistory(lastHistory);
}

function setupLevelControl() {
  const slider = byId("level-slider");
  const save = byId("level-save");
  slider.addEventListener("input", () => {
    sliderTouched = true;
    byId("level-value").textContent = `${slider.value}%`;
    save.disabled = Number(slider.value) === savedFullLevel;
  });
  save.addEventListener("click", async () => {
    save.disabled = true;
    byId("save-note").textContent = "Saving...";
    try {
      const data = await getJson("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ full_level_percent: Number(slider.value) }),
      });
      sliderTouched = false;
      savedFullLevel = data.full_level_percent;
      byId("save-note").textContent = `Saved ${data.full_level_percent}%. The bin picks it up within about 10 seconds.`;
      refreshStatus();
    } catch (error) {
      byId("save-note").textContent = `Could not save: ${error.message}`;
      save.disabled = false;
    }
  });
}

function drawHistory(history) {
  const xEnd = Date.now() / 1000;
  const xStart = xEnd - history.hours * 3600;
  const level = lastStatus ? lastStatus.full_level_percent : history.full_level_percent;

  const fillPoints = history.points.filter((p) => p.fill_percent != null).map((p) => ({ t: p.created_at, v: p.fill_percent }));
  drawLineChart(byId("fill-chart"), fillPoints, {
    xStart, xEnd, yMax: 100, yTicks: [0, 25, 50, 75, 100], tickUnit: "%", area: true,
    reference: level, label: "Fill level over time", format: (v) => `${Math.round(v)}% full`,
  });

  const handPoints = history.points.filter((p) => p.hand_cm != null).map((p) => ({ t: p.created_at, v: p.hand_cm }));
  const handMax = Math.max(50, Math.ceil(Math.max(0, ...handPoints.map((p) => p.v)) / 50) * 50);
  drawLineChart(byId("hand-chart"), handPoints, {
    xStart, xEnd, yMax: handMax, yTicks: [0, handMax / 2, handMax], tickUnit: "",
    label: "Hand sensor distance over time", format: (v) => `${Math.round(v)} cm`,
  });

  drawBarChart(byId("opens-chart"), history.opens.counts, history.opens.bucket_seconds, history.since);
  const minutes = history.opens.bucket_seconds / 60;
  byId("opens-bucket-note").textContent = minutes >= 60 ? `Per ${minutes / 60} h` : `Per ${minutes} min`;

  const rows = history.points.slice(-30).reverse();
  const table = byId("history-table");
  table.innerHTML = "";
  for (const p of rows) {
    const row = table.insertRow();
    row.insertCell().textContent = new Date(p.created_at * 1000).toLocaleString();
    const fillCell = row.insertCell(); fillCell.className = "num";
    fillCell.textContent = p.fill_percent == null ? "--" : `${Math.round(p.fill_percent)}%`;
    const handCell = row.insertCell(); handCell.className = "num";
    handCell.textContent = p.hand_cm == null ? "--" : `${Math.round(p.hand_cm)} cm`;
    row.insertCell().textContent = p.lid_open ? "Open" : "Closed";
  }
}

async function refreshHistory() {
  try {
    lastHistory = await getJson(`/api/history?hours=${selectedHours}`);
    drawHistory(lastHistory);
  } catch (error) { }
}

function setupRangePicker() {
  byId("range-picker").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    selectedHours = Number(button.dataset.hours);
    byId("range-picker").querySelectorAll("button").forEach((b) => b.classList.toggle("selected", b === button));
    refreshHistory();
  });
}

setupLevelControl();
setupRangePicker();
refreshStatus();
refreshHistory();
setInterval(refreshStatus, STATUS_REFRESH_MS);
setInterval(refreshHistory, HISTORY_REFRESH_MS);
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => lastHistory && drawHistory(lastHistory), 150);
});
