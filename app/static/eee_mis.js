// EEE-Taxi MIS: the corporate trip log. Dispatchers book trips (open), closers fill in
// fare, drop time and kms (closed). Uses $, $$, api, rcEsc, rcModal from index.html.

// [key, label, kind]; kind is date, time, num, wide, company, auto, or list:<datalist id>.
const MIS_SECTIONS = [
  ["dispatch", "Booking", [["date", "Date *", "date"], ["company_name", "Company Name *", "company"], ["route_no", "DS no/Route No"], ["dispatcher_name", "Dispatcher Name", "list:misDispatchers"], ["guest_name", "Guest Name", "wide list:misGuests"]]],
  ["dispatch", "Driver and cab", [["driver_id", "Driver ID (Truein)", "list:misDriverIds"], ["driver_name", "Driver Name", "list:misDriverNames"], ["driver_contact", "Driver Cont No"], ["cab_no", "Cab No", "list:misCabs"]]],
  ["dispatch", "Route", [["pickup_address", "Pickup Address", "wide list:misPickups"], ["pickup_zone", "Pickup Zone", "list:misZones"], ["pickup_time", "Pick up Time", "time"], ["drop_location", "Drop Location", "wide list:misDrops"], ["drop_zone", "Drop Zone", "list:misZones"], ["hub_out", "Hub Out", "list:misHubs"]]],
  ["closing", "Timing and distance", [["drop_time", "Drop Time", "time"], ["trip_end_date", "Trip End Date", "date"], ["trip_duration", "Trip Duration", "auto"], ["driver_hours", "Driver Hours", "num"], ["start_odometer", "Start Odometer", "num"], ["end_odometer", "End Odometer", "num"], ["total_kms", "Total kms", "num"], ["dead_run_kms", "Dead Run Kms", "num"], ["hub_in", "Hub In", "list:misHubIns"]]],
  ["closing", "Charges", [["package", "Package (name or base price)", "list:misPackages"], ["extra_km_charges", "Km/Extra/Km Charges", "num"], ["extra_time_charges", "Time Extra/Time Charges", "num"], ["night_charge", "Night Charge 11pm to 5am", "num"], ["trip_fare", "Trip Fare", "num"], ["gst", "GST", "num"], ["mcd", "MCD", "num"], ["parking", "Parking", "num"], ["toll", "Toll", "num"], ["total_trip_fare", "Total Trip Fare", "auto"], ["cash_to_receive", "Cash to be Received", "num"], ["mmt_base_cost", "MMT Base Cost", "num"], ["mmt_prepaid_amount", "MMT Prepaid Amt", "num"]]],
  ["closing", "Wrap-up", [["remarks", "Remarks", "wide list:misRemarks"], ["duty_closed_by", "Duty closed by"], ["b2c_payment_status", "B2C payment Status", "list:misPayments"]]],
];
// Every column of the team's month sheet, in the sheet's order (Full sheet layout).
const MIS_SHEET = [["date", "Date", "date"], ["dispatcher_name", "Dispatcher Name"], ["driver_id", "Driver ID"], ["driver_name", "Driver Name"], ["driver_contact", "Driver Cont No"], ["cab_no", "Cab No"], ["route_no", "DS no/Route No"], ["company_name", "Company Name"], ["guest_name", "Guest Name", "long"], ["pickup_address", "Pickup Address", "long"], ["pickup_zone", "Pickup Zone"], ["pickup_time", "Pick up Time"], ["drop_location", "Drop Location", "long"], ["drop_zone", "Drop Zone"], ["drop_time", "Drop Time"], ["trip_end_date", "Trip End Date", "date"], ["trip_duration", "Trip Duration"], ["hub_out", "Hub Out"], ["hub_in", "Hub In"], ["start_odometer", "Start Odometer", "num"], ["end_odometer", "End Odometer", "num"], ["total_kms", "Total kms", "num"], ["dead_run_kms", "Dead Run Kms", "num"], ["package", "Package"], ["extra_km_charges", "Km/Extra/Km Charges", "num"], ["extra_time_charges", "Time Extra/Time Charges", "num"], ["night_charge", "Night Charge 11pm to 5am", "num"], ["trip_fare", "Trip Fare", "num"], ["gst", "GST", "num"], ["mcd", "MCD", "num"], ["parking", "Parking", "num"], ["toll", "Toll", "num"], ["total_trip_fare", "Total Trip Fare", "num"], ["driver_hours", "Driver Hours", "num"], ["cash_to_receive", "Cash to be Recieved", "num"], ["remarks", "Remarks", "long"], ["mmt_base_cost", "MMT Base Cost", "num"], ["mmt_prepaid_amount", "MMT Peripaid Amt", "num"], ["duty_closed_by", "Duty closed by"], ["b2c_payment_status", "B2C payment Status"]];
const MIS_VIEWS = [["all", "All"], ["open", "Open"], ["closed", "Closed"], ["fix", "Needs fixing"]];
const MIS_GROUPS = [["company_name", "Client"], ["date", "Date"], ["dispatcher_name", "Dispatcher"], ["driver_name", "Driver"], ["cab_no", "Cab"], ["pickup_zone", "Pickup zone"]];
const MIS_FARE_PARTS = ["package", "extra_km_charges", "extra_time_charges", "night_charge"];
const MIS_TOTAL_PARTS = ["trip_fare", "gst", "mcd", "parking", "toll"];
const MIS_DISPATCH_KEYS = MIS_SECTIONS.filter(s => s[0] === "dispatch").flatMap(s => s[2].map(f => f[0]));
// Kept for "Save & add another": the next booking is usually the same day and client.
const MIS_KEEP = ["date", "company_name", "dispatcher_name", "drop_location", "drop_zone"];
const MIS_PAGE = 250;

let _mis = { trips: [], lookups: null, selected: new Set(), truncated: false, started: false,
  view: "all", layout: "desk", summary: false, groupBy: "company_name", limit: MIS_PAGE };

// ── small helpers ──
function misMessage(text, ok = false) {
  $("#misMsg").className = text ? "extraction-note " + (ok ? "ok" : "err") : "hidden";
  $("#misMsg").textContent = text;
}
function misIso(d) { return new Date(d.getTime() + 330 * 60000).toISOString().slice(0, 10); }
function misIstToday() { return misIso(new Date()); }
function misShift(iso, days) { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); }
function misDmy(iso) { return iso ? iso.split("-").reverse().join("/") : ""; }
function misDay(iso) { return iso ? new Date(iso + "T00:00:00Z").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }) : ""; }
function misNum(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
function misMoney(n) { return "₹" + misNum(n).toLocaleString("en-IN", { maximumFractionDigits: 0 }); }
function misPlural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }
function misDigits(s) { return (s || "").replace(/\D/g, "").slice(-10); }
function misTrim(n) { return String(Math.round(n * 100) / 100); }
function misStore(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key) || "";
    localStorage.setItem(key, value);
  } catch (_) { /* private window: preferences just do not persist */ }
  return "";
}
function misMe() { return $("#misDispatcher").value.trim() || (currentUser && currentUser.full_name) || ""; }

// ── loading ──
async function misInit() {
  if (!_mis.started) {
    _mis.started = true;
    try { Object.assign(_mis, JSON.parse(misStore("misPrefs") || "{}")); } catch (_) { /* ignore bad prefs */ }
    $("#misFrom").value = $("#misTo").value = misIstToday();
    $("#misDispatcher").value = misStore("misDispatcher") || (currentUser && currentUser.full_name) || "";
    $("#misDispatcher").addEventListener("change", e => misStore("misDispatcher", e.target.value.trim()));
    $("#misRows").addEventListener("click", misRowClick);
    $("#misSummaryBody").addEventListener("click", misSummaryClick);
    document.addEventListener("keydown", misShortcut);
  }
  await Promise.all([misLoadLookups(), misLoad()]);
}
function misSavePrefs() {
  misStore("misPrefs", JSON.stringify({ layout: _mis.layout, summary: _mis.summary, groupBy: _mis.groupBy }));
}
function misOptions(sel, values) {
  $(sel).innerHTML = [...new Set(values.map(v => (v || "").trim()).filter(Boolean))].slice(0, 400).map(v => `<option value="${rcEsc(v)}">`).join("");
}
async function misLoadLookups() {
  try {
    _mis.lookups = await api("/api/eee-taxi/mis/lookups");
    $("#misDriverIds").innerHTML = _mis.lookups.drivers.filter(d => d.truein_id).map(d => `<option value="${rcEsc(d.truein_id)}">${rcEsc(d.name)}</option>`).join("");
    misOptions("#misDriverNames", _mis.lookups.drivers.map(d => d.name));
    misOptions("#misCabs", _mis.lookups.cabs);
    const chosen = $("#misCompany").value;
    $("#misCompany").innerHTML = '<option value="">All clients</option>' + _mis.lookups.companies.map(c => `<option>${rcEsc(c)}</option>`).join("");
    $("#misCompany").value = chosen;
  } catch (error) { misMessage("Could not load the driver and vehicle masters: " + error.message); }
}
function misQuery() {
  const params = new URLSearchParams();
  [["date_from", "#misFrom"], ["date_to", "#misTo"], ["company", "#misCompany"]].forEach(([key, sel]) => { if ($(sel).value) params.set(key, $(sel).value); });
  return params;
}
async function misLoad() {
  // A slow reply to an earlier date range must not replace a newer one.
  const request = _mis.loadSeq = (_mis.loadSeq || 0) + 1;
  try {
    const data = await api("/api/eee-taxi/mis?" + misQuery());
    if (request !== _mis.loadSeq) return;
    _mis.trips = data.trips;
    _mis.truncated = data.truncated;
    _mis.limit = MIS_PAGE;
    const t = data.trips, zones = (_mis.lookups && _mis.lookups.zones) || [];
    misOptions("#misZones", [...zones, ...t.flatMap(x => [x.pickup_zone, x.drop_zone])]);
    misOptions("#misPackages", ["City to Airport", "Airport to City", ...t.map(x => x.package)]);
    misOptions("#misGuests", t.map(x => x.guest_name));
    misOptions("#misPickups", t.map(x => x.pickup_address));
    misOptions("#misDrops", t.map(x => x.drop_location));
    misOptions("#misDispatchers", [misMe(), ...t.map(x => x.dispatcher_name)]);
    misOptions("#misHubs", ["Vasant Kunj", "Mall Of India", "Select City", "Decathlon", "Huda City Center", ...t.map(x => x.hub_out)]);
    misOptions("#misHubIns", ["20km hub to hub", ...t.map(x => x.hub_in)]);
    misRender();
  } catch (error) { misMessage("Could not load trips: " + error.message); }
}

// ── date range ──
function misSetRange(from, to) { $("#misFrom").value = from; $("#misTo").value = to; misLoad(); }
function misPreset(name) {
  const today = misIstToday(), monthStart = today.slice(0, 8) + "01";
  if (name === "today") misSetRange(today, today);
  if (name === "yesterday") misSetRange(misShift(today, -1), misShift(today, -1));
  if (name === "tomorrow") misSetRange(misShift(today, 1), misShift(today, 1));
  if (name === "month") misSetRange(monthStart, today);
  if (name === "lastMonth") { const end = misShift(monthStart, -1); misSetRange(end.slice(0, 8) + "01", end); }
}
// Step the whole range back or forward by its own length (one day when it is a single day).
function misStep(direction) {
  const from = $("#misFrom").value || misIstToday(), to = $("#misTo").value || from;
  const days = Math.round((new Date(to) - new Date(from)) / 86400000) + 1;
  misSetRange(misShift(from, direction * days), misShift(to, direction * days));
}
function misRangeLabel() {
  const from = $("#misFrom").value, to = $("#misTo").value, today = misIstToday();
  if (from && from === to) return (from === today ? "Today, " : "") + misDay(from);
  return `${from ? misDay(from) : "Start"} to ${to ? misDay(to) : "latest"}`;
}

// ── filtering and rendering ──
function misSearched() {
  const query = $("#misSearch").value.trim().toLowerCase();
  if (!query) return _mis.trips;
  return _mis.trips.filter(t => [t.guest_name, t.driver_name, t.driver_id, t.cab_no, t.route_no, t.pickup_address, t.drop_location, t.dispatcher_name, t.pickup_zone, t.drop_zone].join(" ").toLowerCase().includes(query));
}
function misInView(trip, view) {
  return view === "open" ? trip.status === "Open" : view === "closed" ? trip.status === "Closed" : view === "fix" ? trip.issues.length > 0 : true;
}
function misVisible() { return misSearched().filter(t => misInView(t, _mis.view)); }
function misFare(trip) { return misNum(trip.total_trip_fare || trip.trip_fare); }

function misRender() {
  const searched = misSearched(), trips = searched.filter(t => misInView(t, _mis.view));
  const shown = new Set(trips.map(t => t.id));
  _mis.selected = new Set([..._mis.selected].filter(id => shown.has(id)));
  const count = view => searched.filter(t => misInView(t, view)).length;
  $("#misRange").textContent = misRangeLabel();
  $("#misViews").innerHTML = MIS_VIEWS.map(([id, label]) => `<button class="seg-btn${_mis.view === id ? " active" : ""}" onclick="misSetView('${id}')">${label} <span class="mis-n${id === "open" || id === "fix" ? " warn" : ""}">${count(id)}</span></button>`).join("");
  $$("#misLayout .seg-btn").forEach(b => b.classList.toggle("active", b.dataset.layout === _mis.layout));
  misRenderStats(searched);
  misRenderSummary(trips);
  $("#misCount").textContent = `${misPlural(trips.length, "trip")}` + (trips.length !== _mis.trips.length ? ` of ${_mis.trips.length} loaded` : "") +
    (_mis.truncated ? ". Only the latest 8000 are loaded; narrow the dates." : "");
  const desk = _mis.layout === "desk", oneDay = $("#misFrom").value && $("#misFrom").value === $("#misTo").value;
  $("#misHead").innerHTML = `<th><input type="checkbox" id="misAll" aria-label="Select all shown trips" onchange="misSelectAll(this.checked)"></th><th>Status</th>` +
    (desk ? (oneDay ? "" : "<th>Date</th>") + "<th>Pick up</th><th>Client</th><th>Guest</th><th>Route</th><th>Driver and cab</th><th>Drop</th><th class='num'>Kms</th><th class='num'>Fare</th><th>Check</th><th class='mis-act'></th>"
      : MIS_SHEET.map(([, label, kind]) => `<th${kind === "num" ? ' class="num"' : ""}>${rcEsc(label)}</th>`).join("") + "<th>Check</th>");
  const page = trips.slice(0, _mis.limit);
  $("#misRows").innerHTML = page.map(t => desk ? misDeskRow(t, oneDay) : misSheetRow(t)).join("") ||
    `<tr><td colspan="${desk ? 13 : MIS_SHEET.length + 3}" class="mis-empty">${misEmptyText()}</td></tr>`;
  $("#misMore").classList.toggle("hidden", trips.length <= _mis.limit);
  $("#misMore").textContent = `Show ${Math.min(MIS_PAGE, trips.length - _mis.limit)} more (${trips.length - _mis.limit} not shown)`;
  misUpdateSelection();
}
function misEmptyText() {
  if (!_mis.trips.length) return "No trips booked for these dates. Add a trip, or upload the MMT file.";
  if (_mis.view === "open") return "Nothing open here. Every duty in this view is closed.";
  if (_mis.view === "fix") return "Every driver and cab here matches the masters.";
  return "No trips match the search.";
}
function misRenderStats(trips) {
  const open = trips.filter(t => t.status === "Open").length, closed = trips.length - open;
  const fix = trips.filter(t => t.issues.length).length;
  const fare = trips.reduce((s, t) => s + misFare(t), 0), kms = trips.reduce((s, t) => s + misNum(t.total_kms), 0);
  const pct = trips.length ? Math.round(closed * 100 / trips.length) : 0;
  $("#misStats").innerHTML = `
    <div class="mis-stat"><b>${trips.length.toLocaleString("en-IN")}</b><span>${trips.length === 1 ? "trip" : "trips"}</span></div>
    <div class="mis-stat grow"><b>${closed.toLocaleString("en-IN")} <small>closed</small> · <em class="${open ? "warn" : "ok"}">${open.toLocaleString("en-IN")}</em> <small>open</small></b>
      <div class="mis-progress" role="img" aria-label="${pct}% of duties closed"><i style="width:${pct}%"></i></div><span>${pct}% of duties closed</span></div>
    <div class="mis-stat"><b>${misMoney(fare)}</b><span>total fare of closed duties</span></div>
    <div class="mis-stat"><b>${Math.round(kms).toLocaleString("en-IN")}</b><span>km run</span></div>
    <div class="mis-stat ${fix ? "warn" : "ok"}"><b>${fix || "OK"}</b><span>${fix ? "driver or cab not in masters" : "drivers and cabs match masters"}</span></div>`;
}
function misStatusCell(t) {
  if (t.status === "Closed") return '<td><span class="badge generated">Closed</span></td>';
  const short = { "Trip Fare": "fare", "Drop Time": "drop time", "Total kms": "km" };
  return `<td><span class="badge draft">Open</span><div class="mis-miss">needs ${rcEsc(t.missing.map(m => short[m] || m).join(" · "))}</div></td>`;
}
function misCheckCell(t) {
  return `<td>${t.issues.length ? t.issues.map(i => `<span class="mis-issue">${rcEsc(i)}</span>`).join("") : '<span class="mis-ok">OK</span>'}</td>`;
}
function misPick(t) { return `<td><input type="checkbox" data-pick="${rcEsc(t.id)}" ${_mis.selected.has(t.id) ? "checked" : ""} aria-label="Select trip of ${rcEsc(t.guest_name || t.route_no)}"></td>`; }
function misDeskRow(t, oneDay) {
  const place = (address, zone) => `<span class="mis-place" title="${rcEsc(address)}">${rcEsc(address || "–")}</span>${zone ? `<span class="mis-zone">${rcEsc(zone)}</span>` : ""}`;
  return `<tr data-id="${rcEsc(t.id)}" class="${t.issues.length ? "mis-flag" : ""}">${misPick(t)}${misStatusCell(t)}
    ${oneDay ? "" : `<td>${rcEsc(misDay(t.date))}</td>`}<td class="mis-time">${rcEsc(t.pickup_time || "–")}</td>
    <td><b>${rcEsc(t.company_name)}</b></td>
    <td><div class="cut" title="${rcEsc(t.guest_name)}">${rcEsc(t.guest_name || "–")}</div><div class="meta">${rcEsc(t.route_no)}</div></td>
    <td><div class="mis-leg">${place(t.pickup_address, t.pickup_zone)}</div><div class="mis-leg to">${place(t.drop_location, t.drop_zone)}</div></td>
    <td>${rcEsc(t.driver_name || "–")}<div class="meta">${rcEsc([t.cab_no, t.driver_id].filter(Boolean).join(" · "))}</div></td>
    <td class="mis-time">${rcEsc(t.drop_time)}<div class="meta">${rcEsc(t.trip_duration && t.drop_time ? t.trip_duration + " h" : "")}</div></td>
    <td class="num">${rcEsc(t.total_kms)}</td><td class="num">${t.trip_fare ? misMoney(misFare(t)) : ""}</td>${misCheckCell(t)}
    <td class="mis-act">${t.status === "Open" ? `<button class="btn sm" data-close="${rcEsc(t.id)}">Close duty</button>` : ""}</td></tr>`;
}
function misSheetRow(t) {
  const cells = MIS_SHEET.map(([key, , kind]) => {
    const value = kind === "date" ? misDmy(t[key]) : t[key] || "";
    const cls = kind === "num" ? "num" : kind === "long" ? "cut" : "";
    return `<td class="${cls}"${kind === "long" ? ` title="${rcEsc(value)}"` : ""}>${rcEsc(value)}</td>`;
  }).join("");
  return `<tr data-id="${rcEsc(t.id)}" class="${t.issues.length ? "mis-flag" : ""}">${misPick(t)}${misStatusCell(t)}${cells}${misCheckCell(t)}</tr>`;
}
function misSetView(view) { _mis.view = view; _mis.limit = MIS_PAGE; misRender(); }
function misSetLayout(layout) { _mis.layout = layout; misSavePrefs(); misRender(); }
function misShowMore() { _mis.limit += MIS_PAGE; misRender(); }
function misOnSearch() { _mis.limit = MIS_PAGE; misRender(); }

// ── summary: the same trips grouped by client, date, dispatcher... ──
function misToggleSummary() { _mis.summary = !_mis.summary; misSavePrefs(); misRender(); }
function misSetGroup(key) { _mis.groupBy = key; misSavePrefs(); misRender(); }
function misRenderSummary(trips) {
  $("#misSummary").classList.toggle("hidden", !_mis.summary);
  $("#misSummaryBtn").classList.toggle("active", _mis.summary);
  if (!_mis.summary) return;
  $("#misGroups").innerHTML = MIS_GROUPS.map(([key, label]) => `<button class="seg-btn${_mis.groupBy === key ? " active" : ""}" onclick="misSetGroup('${key}')">${label}</button>`).join("");
  const groups = new Map();
  trips.forEach(t => {
    const name = t[_mis.groupBy] || "(blank)";
    const g = groups.get(name) || { name, trips: 0, open: 0, kms: 0, fare: 0 };
    g.trips += 1; g.open += t.status === "Open" ? 1 : 0; g.kms += misNum(t.total_kms); g.fare += misFare(t);
    groups.set(name, g);
  });
  const byDate = _mis.groupBy === "date";
  const rows = [...groups.values()].sort((a, b) => byDate ? a.name.localeCompare(b.name) : b.trips - a.trips);
  const top = rows.slice(0, 40), most = Math.max(1, ...top.map(g => g.trips));
  const total = { trips: trips.length, open: trips.filter(t => t.status === "Open").length, kms: rows.reduce((s, g) => s + g.kms, 0), fare: rows.reduce((s, g) => s + g.fare, 0) };
  const line = (g, name, attrs = "") => `<tr${attrs}><td>${name}</td><td class="num">${g.trips}</td>
    <td class="num">${g.open ? `<span class="mis-n warn">${g.open}</span>` : "0"}</td><td class="num">${Math.round(g.kms).toLocaleString("en-IN")}</td>
    <td class="num">${misMoney(g.fare)}</td><td class="num">${g.trips - g.open ? misMoney(g.fare / (g.trips - g.open)) : ""}</td>`;
  $("#misSummaryBody").innerHTML = top.map(g => line(g, `<div class="mis-bar-cell"><i style="width:${Math.round(g.trips * 100 / most)}%"></i><span>${rcEsc(byDate ? misDay(g.name) : g.name)}</span></div>`,
    ` data-group="${rcEsc(g.name)}" title="Click to show only these trips"`) + "</tr>").join("") +
    (rows.length > top.length ? `<tr><td colspan="6" class="meta">${rows.length - top.length} more not shown</td></tr>` : "") +
    line(total, "<b>Total</b>", ' class="mis-total"') + "</tr>";
}
function misSummaryClick(event) {
  const row = event.target.closest("tr[data-group]");
  if (!row || row.dataset.group === "(blank)") return;
  const value = row.dataset.group;
  if (_mis.groupBy === "company_name") { $("#misCompany").value = value; misLoad(); }
  else if (_mis.groupBy === "date") misSetRange(value, value);
  else { $("#misSearch").value = value; misOnSearch(); }
}

// ── selection, delete, copy ──
function misRowClick(event) {
  const box = event.target.closest("[data-pick]");
  if (box) {
    box.checked ? _mis.selected.add(box.dataset.pick) : _mis.selected.delete(box.dataset.pick);
    misUpdateSelection();
    return;
  }
  if (event.target.closest("td:first-child")) return;
  const row = event.target.closest("tr[data-id]");
  if (!row) return;
  const trip = _mis.trips.find(t => t.id === row.dataset.id);
  misOpenForm(trip, event.target.closest("[data-close]") ? "close" : "edit");
}
function misUpdateSelection() {
  const boxes = [...$$("#misRows [data-pick]")], n = _mis.selected.size;
  $("#misSelected").textContent = n ? `${n} selected` : "";
  $("#misDelete").disabled = $("#misCopy").disabled = !n;
  if ($("#misAll")) $("#misAll").checked = boxes.length > 0 && boxes.every(b => b.checked);
}
function misSelectAll(on) {
  [...$$("#misRows [data-pick]")].forEach(box => { box.checked = on; on ? _mis.selected.add(box.dataset.pick) : _mis.selected.delete(box.dataset.pick); });
  misUpdateSelection();
}
function misPost(path, body) {
  return api("/api/eee-taxi/mis" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
function misDeleteTrips(ids, after = () => {}) {
  rcModal({ title: `Delete ${misPlural(ids.length, "trip")}?`, sub: "Deleted trips are removed from the MIS at once and cannot be brought back.", okText: "Delete",
    onOk: async () => {
      const result = await misPost("/delete", { ids });
      _mis.selected.clear();
      after();
      await misLoad();
      misMessage(`Deleted ${misPlural(result.deleted, "trip")}.`, true);
    } });
}
function misDeleteSelected() { misDeleteTrips([..._mis.selected]); }
// Shuttle rosters repeat: book the selected runs again on another day as open trips.
function misCopySelected() {
  const ids = [..._mis.selected];
  rcModal({ title: `Book ${misPlural(ids.length, "trip")} again on another date`,
    sub: "Copies the client, guest, route, driver and cab as new open trips. Fare, kms and times of the old trip are not copied.",
    okText: "Copy trips", fields: [{ id: "date", label: "New date", type: "date", value: misShift($("#misTo").value || misIstToday(), 1) }],
    onOk: async ({ date }) => {
      if (!date) throw new Error("Choose the new date.");
      const result = await misPost("/copy", { ids, date });
      _mis.selected.clear();
      $("#misFrom").value = $("#misTo").value = date;
      await misLoad();
      misMessage(`Booked ${misPlural(result.added, "trip")} on ${misDmy(date)}.` + (result.skipped_existing ? ` ${result.skipped_existing} were already there.` : ""), true);
    } });
}

// ── MMT upload and Excel download ──
async function misUpload(input) {
  const file = input.files[0];
  input.value = "";
  if (!file) return;
  const dispatcher = $("#misDispatcher").value.trim();
  if (!dispatcher) { misMessage("Enter your name in the Dispatcher box, then upload the file again."); $("#misDispatcher").focus(); return; }
  misStore("misDispatcher", dispatcher);
  const form = new FormData();
  form.append("file", file);
  form.append("dispatcher_name", dispatcher);
  const button = $("#misUploadBtn");
  button.disabled = true;
  button.textContent = "Uploading…";
  try {
    const result = await api("/api/eee-taxi/mis/upload-mmt", { method: "POST", body: form });
    // Move the filters so the uploaded trips are on screen.
    if (result.first_date && (!$("#misFrom").value || result.first_date < $("#misFrom").value)) $("#misFrom").value = result.first_date;
    if (result.last_date && $("#misTo").value && result.last_date > $("#misTo").value) $("#misTo").value = result.last_date;
    if (result.added && !["", "MMT"].includes($("#misCompany").value)) $("#misCompany").value = "";
    await misLoad();
    const parts = [`Added ${misPlural(result.added, "trip")} from ${file.name}.`];
    if (result.skipped_existing) parts.push(`${result.skipped_existing} already in the MIS were skipped.`);
    if (result.need_fixing) parts.push(`${result.need_fixing} need fixing: driver or cab is not in the masters.`);
    if (result.errors.length) parts.push(`${misPlural(result.errors.length, "row")} could not be read: ${result.errors.slice(0, 5).join(" | ")}${result.errors.length > 5 ? " …" : ""}`);
    misMessage(parts.join(" "), !result.errors.length);
  } catch (error) { misMessage("Upload failed: " + error.message); }
  finally { button.disabled = false; button.textContent = "Upload MMT file"; }
}
async function misExport() {
  const params = misQuery();
  if ($("#misSearch").value.trim()) params.set("q", $("#misSearch").value.trim());
  if (_mis.view === "open") params.set("status", "Open");
  if (_mis.view === "closed") params.set("status", "Closed");
  const button = $("#misExport");
  button.disabled = true;
  try {
    const resp = await fetch("/api/eee-taxi/mis/export?" + params, { headers: { Authorization: `Bearer ${authToken}` } });
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.statusText);
    const url = URL.createObjectURL(await resp.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = ((resp.headers.get("content-disposition") || "").match(/filename="([^"]+)"/) || [])[1] || "MIS.xlsx";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { misMessage("Download failed: " + error.message); }
  finally { button.disabled = false; }
}

// ── master matching (same order as the server: Truein ID, phone, then name) ──
function misNameKey(name) { return (name || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }
function misFindByName(drivers, name) {
  const key = misNameKey(name);
  if (!key) return null;
  const same = drivers.filter(d => misNameKey(d.name) === key);
  if (same.length) return same.length === 1 ? same[0] : null;
  const starts = key.length >= 4 ? drivers.filter(d => misNameKey(d.name).startsWith(key)) : [];
  return starts.length === 1 ? starts[0] : null;
}
function misFindDriver(values) {
  const drivers = (_mis.lookups && _mis.lookups.drivers) || [];
  const id = (values.driver_id || "").toUpperCase(), phone = misDigits(values.driver_contact);
  return (id && drivers.find(d => d.truein_id && d.truein_id.toUpperCase() === id))
    || (phone.length === 10 && drivers.find(d => misDigits(d.mobile) === phone))
    || misFindByName(drivers, values.driver_name);
}

// ── trip form: new booking, full edit, or close duty ──
function misField([key, label, kind = ""], value) {
  const wide = kind.includes("wide") ? "wide" : "";
  if (kind === "company") {
    const companies = (_mis.lookups && _mis.lookups.companies) || [];
    const choices = value && !companies.includes(value) ? [value, ...companies] : companies;
    return `<label>${rcEsc(label)}<select data-m="${key}"><option value="">Choose client</option>${choices.map(c => `<option${c === value ? " selected" : ""}>${rcEsc(c)}</option>`).join("")}</select></label>`;
  }
  const type = kind === "date" || kind === "time" ? kind : "text";
  const list = (kind.match(/list:(\w+)/) || [])[1];
  const attrs = (list ? ` list="${list}"` : "") + (kind === "num" ? ' inputmode="decimal"' : "") + (kind === "auto" ? ' readonly tabindex="-1" placeholder="Worked out for you"' : "");
  const hinted = ["driver_name", "cab_no", "night_charge", "trip_fare", "total_kms"].includes(key) ? `<span class="field-hint" data-hint="${key}"></span>` : "";
  return `<label class="${wide}">${rcEsc(label)}<input type="${type}" data-m="${key}"${attrs} value="${rcEsc(value || "")}" autocomplete="off">${hinted}</label>`;
}
function misFormValues(form) {
  return Object.fromEntries([...form.querySelectorAll("[data-m]")].map(input => [input.dataset.m, input.value.trim()]));
}
function misHint(form, key, text, ok = true) {
  const hint = form.querySelector(`[data-hint="${key}"]`);
  if (!hint) return;
  hint.className = "field-hint " + (ok ? "mis-ok" : "warn");
  hint.textContent = text;
}
function misDuration(v) {
  if (!v.date || !v.pickup_time || !v.drop_time) return "";
  const start = new Date(`${v.date}T${v.pickup_time}:00Z`);
  let end = new Date(`${v.trip_end_date || v.date}T${v.drop_time}:00Z`);
  if (end < start && !v.trip_end_date) end = new Date(end.getTime() + 86400000);
  if (end < start) return "";
  const minutes = Math.round((end - start) / 60000);
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
// Everything the form works out while you type: driver/cab checks, duration, kms, fare, total, status.
function misRecalc(form, base) {
  const set = (key, value) => { const input = form.querySelector(`[data-m="${key}"]`); if (input) input.value = value; };
  const input = key => form.querySelector(`[data-m="${key}"]`);
  let v = { ...base, ...misFormValues(form) };
  if (input("start_odometer") && v.start_odometer && v.end_odometer) {
    const kms = misNum(v.end_odometer) - misNum(v.start_odometer);
    if (kms >= 0) set("total_kms", misTrim(kms));
    misHint(form, "total_kms", kms >= 0 ? "From the odometers" : "End odometer is lower than start", kms >= 0);
  } else misHint(form, "total_kms", "");
  const fareInput = input("trip_fare");
  if (fareInput) {
    const parts = MIS_FARE_PARTS.map(key => v[key]).filter(x => x !== "" && Number.isFinite(Number(x)));
    const sum = parts.reduce((s, x) => s + Number(x), 0);
    if (parts.length && sum > 0 && (!fareInput.value || fareInput.dataset.auto === "1")) { fareInput.value = misTrim(sum); fareInput.dataset.auto = "1"; }
    misHint(form, "trip_fare", fareInput.dataset.auto === "1" ? "Package + extras + night charge. Type over it to change." : "");
    const hour = v.pickup_time ? Number(v.pickup_time.slice(0, 2)) : -1;
    misHint(form, "night_charge", (hour >= 23 || (hour >= 0 && hour < 5)) && !v.night_charge ? "Pick-up is in night hours" : "", false);
  }
  v = { ...base, ...misFormValues(form) };
  set("trip_duration", misDuration(v));
  set("total_trip_fare", v.trip_fare ? misTrim(MIS_TOTAL_PARTS.reduce((s, key) => s + misNum(v[key]), 0)) : "");
  if (_mis.lookups && input("driver_name")) {
    const driver = misFindDriver(v);
    if (!v.driver_id && !v.driver_name && !v.driver_contact) misHint(form, "driver_name", "");
    else if (!driver) misHint(form, "driver_name", "Not in the driver master", false);
    else misHint(form, "driver_name", driver.status === "Deactive" ? "Deactive in the driver master" : "In the driver master", driver.status !== "Deactive");
    const plate = v.cab_no.toUpperCase().replace(/[^A-Z0-9]/g, "");
    misHint(form, "cab_no", !plate ? "" : _mis.lookups.cabs.includes(plate) ? "In the vehicle master" : "Not in the vehicle master", _mis.lookups.cabs.includes(plate));
  }
  const missing = [["trip_fare", "Trip Fare"], ["drop_time", "Drop Time"], ["total_kms", "Total kms"]].filter(([key]) => !v[key]).map(([, label]) => label);
  const state = form.querySelector("[data-state]");
  state.className = "mis-state " + (missing.length ? "open" : "closed");
  state.textContent = missing.length ? `Will stay Open: needs ${missing.join(", ")}` : "Will be saved as Closed";
}
function misFillDriver(form, changedKey) {
  const values = misFormValues(form);
  const driver = values[changedKey] && misFindDriver({ driver_id: "", driver_name: "", driver_contact: "", [changedKey]: values[changedKey] });
  if (!driver) return;
  form.querySelector('[data-m="driver_id"]').value = driver.truein_id;
  form.querySelector('[data-m="driver_name"]').value = driver.name;
  form.querySelector('[data-m="driver_contact"]').value = misDigits(driver.mobile);
}
// A returning guest usually takes the same run: fill the empty route fields from their last trip.
function misFillGuest(form) {
  const values = misFormValues(form), name = values.guest_name.toLowerCase();
  const last = name && _mis.trips.find(t => t.guest_name.toLowerCase() === name && (!values.company_name || t.company_name === values.company_name));
  if (!last) return;
  ["company_name", "pickup_address", "pickup_zone", "drop_location", "drop_zone"].forEach(key => {
    const input = form.querySelector(`[data-m="${key}"]`);
    if (input && !input.value) input.value = last[key];
  });
}
function misContext(t) {
  return `<div class="mis-context"><div><b>${rcEsc(t.company_name)}</b> · ${rcEsc(t.guest_name || "No guest name")}${t.route_no ? ` · ${rcEsc(t.route_no)}` : ""}</div>
    <div>${rcEsc(misDay(t.date))} at <b>${rcEsc(t.pickup_time || "–")}</b> · ${rcEsc(t.pickup_address || "–")} <span class="mis-arrow">to</span> ${rcEsc(t.drop_location || "–")}</div>
    <div class="meta">${rcEsc([t.driver_name, t.cab_no, t.dispatcher_name && "booked by " + t.dispatcher_name].filter(Boolean).join(" · "))}</div></div>`;
}
function misOpenForm(trip = null, mode = trip ? "edit" : "new", seed = null) {
  if (!trip && mode !== "new") return;
  const isNew = mode === "new", closing = mode === "close";
  const company = $("#misCompany").value, from = $("#misFrom").value;
  const base = trip || { date: from && from === $("#misTo").value ? from : misIstToday(), company_name: company, dispatcher_name: misMe(), ...(seed || {}) };
  const values = closing && !base.duty_closed_by ? { ...base, duty_closed_by: misMe() } : base;
  const sections = MIS_SECTIONS.filter(([part, , fields]) => !closing || part === "closing").map(([part, title, fields]) => {
    const shown = fields.filter(([key]) => !key.startsWith("mmt_") || values.company_name === "MMT")
      .filter(([key]) => key !== "b2c_payment_status" || !closing || values.company_name === "B2C");
    return { part, html: `<fieldset><legend>${title}</legend><div class="mis-grid">${shown.map(f => misField(f, values[f[0]])).join("")}</div></fieldset>` };
  });
  const dispatch = sections.filter(s => s.part === "dispatch").map(s => s.html).join("");
  const closeHtml = sections.filter(s => s.part === "closing").map(s => s.html).join("");
  const title = isNew ? "New trip" : closing ? "Close duty" : "Edit trip";
  const sub = isNew ? "Book it now with what you know. Whoever closes the duty adds fare, drop time and kms later."
    : closing ? "Fill what the duty slip shows. The trip closes once Trip Fare, Drop Time and Total kms are in."
    : `Last saved by ${base.updated_by || base.created_by}${base.source === "mmt_upload" ? " · uploaded from MMT" : base.source === "copy" ? " · copied from an earlier trip" : ""}`;
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML = `<form class="modal mis-form" role="dialog" aria-modal="true" aria-labelledby="misFormTitle" novalidate>
      <h3 id="misFormTitle">${title}</h3><p class="modal-sub">${rcEsc(sub)}</p>
      ${closing ? misContext(base) : dispatch}
      ${isNew ? `<details class="mis-later"><summary>Closing details (usually filled after the trip)</summary>${closeHtml}</details>` : closeHtml}
      <div class="mis-foot"><span data-state class="mis-state"></span><span class="text-err" data-err aria-live="assertive"></span></div>
      <div class="modal-actions">
        ${isNew ? "" : '<button type="button" class="btn danger" data-delete>Delete</button><button type="button" class="btn secondary" data-dup style="margin-right:auto;">Book again</button>'}
        <button type="button" class="btn secondary" data-cancel>Cancel</button>
        ${isNew ? '<button type="button" class="btn secondary" data-again>Save &amp; add another</button>' : ""}
        ${closing ? '<button type="button" class="btn secondary" data-next>Save &amp; next open</button>' : ""}
        <button type="submit" class="btn">Save <kbd>Ctrl+Enter</kbd></button>
      </div>
    </form>`;
  document.body.appendChild(wrap);
  const form = wrap.querySelector("form"), err = wrap.querySelector("[data-err]");
  const close = () => wrap.remove();
  const fare = form.querySelector('[data-m="trip_fare"]');
  if (fare && !fare.value) fare.dataset.auto = "1";
  const save = async after => {
    // Ctrl+Enter reaches here even while a save is running; one save at a time.
    if (form.querySelector('button[type="submit"]').disabled) return;
    // Close mode shows only the closing fields; the booking details ride along unchanged.
    const kept = Object.fromEntries(MIS_SHEET.map(([key]) => [key, base[key] || ""]));
    const body = { ...(isNew ? {} : kept), ...misFormValues(form) };
    if (!body.date || !body.company_name) { err.textContent = "Date and Company Name are required."; return; }
    err.textContent = "";
    form.querySelectorAll("button").forEach(b => { b.disabled = true; });
    try {
      await api("/api/eee-taxi/mis" + (isNew ? "" : "/" + encodeURIComponent(base.id)),
        { method: isNew ? "POST" : "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const order = misVisible().map(t => t.id), at = order.indexOf(base.id);
      await misLoad();
      misMessage(isNew ? "Trip booked." : "Trip saved.", true);
      if (after === "again") {
        form.querySelectorAll("[data-m]").forEach(input => { if (!MIS_KEEP.includes(input.dataset.m)) input.value = ""; });
        if (fare) fare.dataset.auto = "1";
        misRecalc(form, {});
        err.className = "mis-ok"; err.textContent = "Booked. Enter the next one.";
        form.querySelector('[data-m="guest_name"]').focus();
        return;
      }
      close();
      if (after === "next") {
        const open = _mis.trips.filter(t => t.status === "Open" && t.id !== base.id);
        const next = order.slice(at + 1).map(id => open.find(t => t.id === id)).find(Boolean) || open[0];
        if (next) misOpenForm(next, "close"); else misMessage("All duties in this view are closed.", true);
      }
    } catch (error) { err.className = "text-err"; err.textContent = error.message; }
    finally { form.querySelectorAll("button").forEach(b => { b.disabled = false; }); }
  };
  form.addEventListener("submit", e => { e.preventDefault(); save(); });
  form.addEventListener("input", e => {
    if (e.target.dataset.m === "trip_fare") e.target.dataset.auto = e.target.value ? "0" : "1";
    misRecalc(form, isNew ? {} : base);
  });
  form.addEventListener("change", e => {
    const key = e.target.dataset.m;
    if (["driver_id", "driver_name", "driver_contact"].includes(key)) misFillDriver(form, key);
    if (key === "cab_no") e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (key === "guest_name" && isNew) misFillGuest(form);
    misRecalc(form, isNew ? {} : base);
  });
  wrap.querySelector("[data-cancel]").onclick = close;
  if (isNew) wrap.querySelector("[data-again]").onclick = () => save("again");
  else {
    wrap.querySelector("[data-delete]").onclick = () => misDeleteTrips([base.id], close);
    wrap.querySelector("[data-dup]").onclick = () => { close(); misOpenForm(null, "new", { ...Object.fromEntries(MIS_DISPATCH_KEYS.map(key => [key, base[key]])), date: misIstToday(), route_no: /^\d{10}$/.test(base.route_no) ? base.route_no : "" }); };
  }
  if (closing) wrap.querySelector("[data-next]").onclick = () => save("next");
  wrap.addEventListener("keydown", e => {
    if (e.key === "Escape" && document.body.lastElementChild === wrap) close();
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(closing ? "next" : undefined); }
    // Plain Enter in a field would save half a trip; Ctrl+Enter is the save shortcut.
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && e.target.tagName === "INPUT") e.preventDefault();
  });
  misRecalc(form, isNew ? {} : base);
  const firstMissing = ["drop_time", "total_kms", "trip_fare"].find(key => !values[key]);
  const focusKey = isNew ? (values.company_name ? "guest_name" : "company_name") : closing ? (firstMissing === "total_kms" ? "start_odometer" : firstMissing || "drop_time") : firstMissing || "guest_name";
  (form.querySelector(`[data-m="${focusKey}"]`) || form.querySelector("[data-m]")).focus();
}

// N books a trip and / jumps to search, when the MIS page is open and you are not typing.
function misShortcut(event) {
  // Other pages keep hidden dialogs in the document, so only a visible one counts.
  const dialogOpen = [...document.querySelectorAll(".modal-backdrop")].some(m => m.getClientRects().length);
  if (!$("#pane-eee-mis").classList.contains("active") || dialogOpen) return;
  if (event.ctrlKey || event.metaKey || event.altKey || /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
  if (event.key === "n" || event.key === "N") { event.preventDefault(); misOpenForm(); }
  if (event.key === "/") { event.preventDefault(); $("#misSearch").focus(); }
}
