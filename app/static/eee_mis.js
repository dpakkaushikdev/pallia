// EEE-Taxi MIS: the corporate trip log. Dispatchers book trips (open), closers fill in
// fare, drop time and kms (closed). Uses $, $$, api, rcEsc, rcModal from index.html.

// [key, label, kind]; kind is date, time, num, wide, company, auto, or list:<datalist id>.
const MIS_SECTIONS = [
  ["dispatch", "Booking", [["date", "Date (pick-up date) *", "date"], ["company_name", "Company Name *", "company"], ["company_other", "Client name (for Others)"], ["route_no", "DS no/Route No"], ["dispatcher_name", "Dispatcher Name", "list:misDispatchers"], ["guest_name", "Guest Name", "wide"]]],
  ["dispatch", "Driver and cab", [["hired", "Cab and driver from", "hired"], ["driver_name", "Driver Name", "list:misDriverNames"], ["driver_id", "Driver ID (Truein)", "list:misDriverIds"], ["driver_contact", "Driver Cont No"], ["cab_no", "Cab No", "list:misCabs"]]],
  ["dispatch", "Route", [["pickup_address", "Pickup Address", "wide"], ["pickup_zone", "Pickup Zone", "list:misZones"], ["pickup_time", "Pick up Time", "time"], ["drop_location", "Drop Location", "wide"], ["drop_zone", "Drop Zone", "list:misZones"], ["hub_out", "Hub Out", "list:misHubs"]]],
  ["closing", "Timing and distance", [["drop_time", "Drop Time", "time"], ["trip_end_date", "Trip End Date", "date"], ["trip_duration", "Trip Duration", "auto"], ["driver_hours", "Driver Hours", "num"], ["start_odometer", "Start Odometer", "num"], ["end_odometer", "End Odometer", "num"], ["total_kms", "Total kms", "num"], ["dead_run_kms", "Dead Run Kms", "num"], ["hub_in", "Hub In", "list:misHubIns"]]],
  ["closing", "Charges", [["package", "Package (name or base price)", "list:misPackages"], ["extra_km_charges", "Km/Extra/Km Charges", "num"], ["extra_time_charges", "Time Extra/Time Charges", "num"], ["night_charge", "Night Charge 11pm to 5am", "num"], ["trip_fare", "Trip Fare", "num"], ["gst", "GST", "num"], ["mcd", "MCD", "num"], ["parking", "Parking", "num"], ["toll", "Toll", "num"], ["total_trip_fare", "Total Trip Fare", "auto"], ["cash_to_receive", "Cash to be Received", "num"], ["mmt_base_cost", "MMT Base Cost", "num"], ["mmt_prepaid_amount", "MMT Prepaid Amt", "num"]]],
  ["closing", "Wrap-up", [["remarks", "Remarks", "wide list:misRemarks"], ["duty_closed_by", "Duty closed by"], ["b2c_payment_status", "B2C payment Status", "list:misPayments"]]],
];
// Every column of the team's month sheet, in the sheet's order (Full sheet layout).
const MIS_SHEET = [["date", "Date", "date"], ["dispatcher_name", "Dispatcher Name"], ["driver_id", "Driver ID"], ["driver_name", "Driver Name"], ["driver_contact", "Driver Cont No"], ["cab_no", "Cab No"], ["route_no", "DS no/Route No"], ["company_name", "Company Name"], ["guest_name", "Guest Name", "long"], ["pickup_address", "Pickup Address", "long"], ["pickup_zone", "Pickup Zone"], ["pickup_time", "Pick up Time"], ["drop_location", "Drop Location", "long"], ["drop_zone", "Drop Zone"], ["drop_time", "Drop Time"], ["trip_end_date", "Trip End Date", "date"], ["trip_duration", "Trip Duration"], ["hub_out", "Hub Out"], ["hub_in", "Hub In"], ["start_odometer", "Start Odometer", "num"], ["end_odometer", "End Odometer", "num"], ["total_kms", "Total kms", "num"], ["dead_run_kms", "Dead Run Kms", "num"], ["package", "Package"], ["extra_km_charges", "Km/Extra/Km Charges", "num"], ["extra_time_charges", "Time Extra/Time Charges", "num"], ["night_charge", "Night Charge 11pm to 5am", "num"], ["trip_fare", "Trip Fare", "num"], ["gst", "GST", "num"], ["mcd", "MCD", "num"], ["parking", "Parking", "num"], ["toll", "Toll", "num"], ["total_trip_fare", "Total Trip Fare", "num"], ["driver_hours", "Driver Hours", "num"], ["cash_to_receive", "Cash to be Recieved", "num"], ["remarks", "Remarks", "long"], ["mmt_base_cost", "MMT Base Cost", "num"], ["mmt_prepaid_amount", "MMT Peripaid Amt", "num"], ["duty_closed_by", "Duty closed by"], ["b2c_payment_status", "B2C payment Status"]];
const MIS_VIEWS = [["all", "All"], ["open", "Open"], ["closed", "Closed"], ["fix", "Needs fixing"], ["pipeline", "Pipeline"]];
// [endpoint, button, button text, client] for each client file the page can read.
const MIS_UPLOADS = { mmt: ["/upload-mmt", "#misUploadBtn", "Upload MMT file", "MMT"], mongodb: ["/upload-mongodb", "#misMongoBtn", "Upload MongoDB file", "MongoDB"] };
const MIS_GROUPS = [["company_name", "Client"], ["date", "Date"], ["dispatcher_name", "Dispatcher"], ["driver_name", "Driver"], ["cab_no", "Cab"], ["pickup_zone", "Pickup zone"]];
const MIS_FARE_PARTS = ["package", "extra_km_charges", "extra_time_charges", "night_charge"];
const MIS_TOTAL_PARTS = ["trip_fare", "gst", "mcd", "parking", "toll"];
const MIS_DISPATCH_KEYS = MIS_SECTIONS.filter(s => s[0] === "dispatch").flatMap(s => s[2].map(f => f[0]));
// Kept for "Save & add another": the next booking is usually the same day and client.
const MIS_KEEP = ["date", "company_name", "dispatcher_name", "drop_location", "drop_zone"];
const MIS_PAGE_SIZES = [50, 100, 200];
const MIS_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const MIS_SEARCH_DELAY = 350;
const MIS_CALENDAR_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';

// trips is one page of the chosen tab; sum is the counts and totals of the dates on screen.
let _mis = { trips: [], total: 0, sum: {}, groups: [], lookups: null, selected: new Set(), started: false,
  view: "all", layout: "desk", summary: false, groupBy: "company_name", page: 1, pageSize: MIS_PAGE_SIZES[0], dashYear: "", dashMonth: "" };

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
function misCount(n) { return Math.round(misNum(n)).toLocaleString("en-IN"); }
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
// "5/11/26", "05-11-2026" and "05112026" as "2026-11-05"; "" stays "" (no date); anything else is null.
function misParseDmy(value) {
  const raw = value.trim();
  if (!raw) return "";
  const parts = raw.match(/^(\d{1,2})[\/.\- ](\d{1,2})[\/.\- ](\d{2}|\d{4})$/) || raw.match(/^(\d{2})(\d{2})(\d{4})$/);
  if (!parts) return null;
  const day = Number(parts[1]), month = Number(parts[2]), year = Number(parts[3].length === 2 ? "20" + parts[3] : parts[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  const real = date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  return real ? date.toISOString().slice(0, 10) : null;
}
// A browser's own date box shows MM/DD/YYYY when the browser is set to US English. So each date
// gets a typed DD/MM/YYYY box in front; the browser's box stays behind it, holding the value
// (YYYY-MM-DD) and supplying the calendar.
function misWrapDate(input) {
  const box = document.createElement("span"), text = document.createElement("input"), button = document.createElement("button");
  box.className = "mis-date";
  input.replaceWith(box);
  Object.assign(text, { type: "text", className: "mis-date-text", maxLength: 10, placeholder: "DD/MM/YYYY", autocomplete: "off", inputMode: "numeric", value: misDmy(input.value) });
  text.setAttribute("aria-label", (input.getAttribute("aria-label") || "Date") + ", day/month/year");
  Object.assign(button, { type: "button", className: "mis-date-btn", tabIndex: -1, innerHTML: MIS_CALENDAR_ICON });
  button.setAttribute("aria-label", "Open calendar");
  input.tabIndex = -1;
  input.setAttribute("aria-hidden", "true");
  box.append(text, button, input);
  button.addEventListener("click", () => { try { input.showPicker(); } catch (_) { text.focus(); } });
  input.addEventListener("change", () => { text.value = misDmy(input.value); text.classList.remove("bad"); });
  text.addEventListener("change", () => {
    const iso = misParseDmy(text.value);
    text.classList.toggle("bad", iso === null);
    if (iso === null) return;
    text.value = misDmy(iso);
    if (iso === input.value) return;
    input.value = iso;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
// After the page itself changes a date (Today, the arrows, an upload), show it in the typed box.
function misSyncDates(root) {
  root.querySelectorAll(".mis-date").forEach(box => {
    const text = box.querySelector(".mis-date-text");
    if (document.activeElement === text) return;
    text.value = misDmy(box.querySelector('input[type="date"]').value);
    text.classList.remove("bad");
  });
}
function misMe() { return $("#misDispatcher").value.trim() || (currentUser && currentUser.full_name) || ""; }

// ── loading ──
async function misInit() {
  if (!_mis.started) {
    _mis.started = true;
    try { Object.assign(_mis, JSON.parse(misStore("misPrefs") || "{}")); } catch (_) { /* ignore bad prefs */ }
    if (!MIS_PAGE_SIZES.includes(_mis.pageSize)) _mis.pageSize = MIS_PAGE_SIZES[0];
    // The dashboard opens on the month we are in.
    const today = misIstToday();
    _mis.dashYear = today.slice(0, 4);
    _mis.dashMonth = String(Number(today.slice(5, 7)));
    $("#misDashMonth").innerHTML = '<option value="">All months</option>' + MIS_MONTHS.map((month, i) => `<option value="${i + 1}">${month}</option>`).join("");
    $("#misFrom").value = $("#misTo").value = today;
    [$("#misFrom"), $("#misTo")].forEach(misWrapDate);
    $("#misDispatcher").value = misStore("misDispatcher") || (currentUser && currentUser.full_name) || "";
    $("#misDispatcher").addEventListener("change", e => misStore("misDispatcher", e.target.value.trim()));
    $("#misRows").addEventListener("click", misRowClick);
    $("#misSummaryBody").addEventListener("click", misSummaryClick);
    document.addEventListener("keydown", misShortcut);
  }
  await Promise.all([misLoadLookups(), misLoad(), misLoadDash()]);
}
function misSavePrefs() {
  misStore("misPrefs", JSON.stringify({ layout: _mis.layout, summary: _mis.summary, groupBy: _mis.groupBy, pageSize: _mis.pageSize }));
}
function misOptions(sel, values) {
  $(sel).innerHTML = [...new Set(values.map(v => (v || "").trim()).filter(Boolean))].slice(0, 400).map(v => `<option value="${rcEsc(v)}">`).join("");
}
// A page is 50 trips, so the suggestions in the form build up from every page viewed.
function misRemember(sel, values) { misOptions(sel, [...values, ...[...$(sel).options].map(o => o.value)]); }
async function misLoadLookups() {
  try {
    _mis.lookups = await api("/api/eee-taxi/mis/lookups");
    $("#misDriverIds").innerHTML = _mis.lookups.drivers.filter(d => d.truein_id).map(d => `<option value="${rcEsc(d.truein_id)}">${rcEsc(d.name)}</option>`).join("");
    // The name list shows each driver with their ID; picking one fills the Driver ID.
    misOptions("#misDriverNames", _mis.lookups.drivers.map(misDriverLabel));
    // Zones come from the fixed list only: the old sheets hold mistyped zones such as "8/80".
    misOptions("#misZones", _mis.lookups.zones);
    misOptions("#misCabs", _mis.lookups.cabs);
    const chosen = $("#misCompany").value;
    $("#misCompany").innerHTML = '<option value="">All clients</option>' + _mis.lookups.companies.map(c => `<option>${rcEsc(c)}</option>`).join("");
    $("#misCompany").value = chosen;
  } catch (error) { misMessage("Could not load the driver and vehicle masters: " + error.message); }
}
// Dates, client and search text: what every tab, the summary and the Excel download share.
function misQuery() {
  const params = new URLSearchParams();
  [["date_from", "#misFrom"], ["date_to", "#misTo"], ["company", "#misCompany"]].forEach(([key, sel]) => { if ($(sel).value) params.set(key, $(sel).value); });
  const search = $("#misSearch").value.trim();
  if (search) params.set("q", search);
  return params;
}
// Reads one page of the chosen tab. The server does the filtering, so a month is never loaded whole.
async function misLoad() {
  // A slow reply to an earlier request must not replace a newer one.
  const request = _mis.loadSeq = (_mis.loadSeq || 0) + 1;
  try {
    const params = misQuery();
    params.set("view", _mis.view);
    params.set("page", _mis.page);
    params.set("page_size", _mis.pageSize);
    const data = await api("/api/eee-taxi/mis?" + params);
    if (request !== _mis.loadSeq) return;
    const pages = Math.max(1, Math.ceil(data.total / _mis.pageSize));
    // The last trip of the last page was deleted or closed: show the page that is left.
    if (_mis.page > pages) { _mis.page = pages; await misLoad(); return; }
    Object.assign(_mis, { trips: data.trips, total: data.total, sum: data.summary });
    const t = data.trips;
    misRemember("#misPackages", ["City to Airport", "Airport to City", ...t.map(x => x.package)]);
    misRemember("#misDispatchers", [misMe(), ...t.map(x => x.dispatcher_name)]);
    misRemember("#misHubs", ["Vasant Kunj", "Mall Of India", "Select City", "Decathlon", "Huda City Center", ...t.map(x => x.hub_out)]);
    misRemember("#misHubIns", ["20km hub to hub", ...t.map(x => x.hub_in)]);
    misRender();
    if (_mis.summary) misLoadSummary();
  } catch (error) { misMessage("Could not load trips: " + error.message); }
}
// The latest open duty of the dates, client and search chosen, wherever in the pages it is.
async function misNextOpen() {
  if (!_mis.sum.open) return null;
  const params = misQuery();
  params.set("view", "open");
  params.set("page_size", 1);
  try { return (await api("/api/eee-taxi/mis?" + params)).trips[0] || null; }
  catch (_) { return null; }
}
// A changed filter starts again from the first page.
function misRefilter() { _mis.page = 1; return misLoad(); }
// After trips are added, changed or deleted the dashboard numbers move too.
function misReload() { return Promise.all([misLoad(), misLoadDash()]); }

// ── dashboard: totals of the year and month chosen ──
function misSetDash() {
  _mis.dashYear = $("#misDashYear").value;
  _mis.dashMonth = $("#misDashMonth").value;
  misLoadDash();
}
function misDashLabel() {
  const month = _mis.dashMonth ? MIS_MONTHS[_mis.dashMonth - 1] : "";
  if (month) return _mis.dashYear ? `${month} ${_mis.dashYear}` : `Every ${month}`;
  return _mis.dashYear ? `Year ${_mis.dashYear}` : "All trips so far";
}
function misDashCards(d) {
  const perKm = d.total_kms ? "₹" + (d.trip_fare / d.total_kms).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "–";
  return [
    ["lead", "Total trips", misCount(d.trips), `${misCount(d.closed)} closed · ${misCount(d.open)} open` + (d.pipeline ? ` (${misCount(d.pipeline)} in pipeline)` : "")],
    ["km", "Total kms", misCount(d.total_kms), d.closed ? `${misCount(d.total_kms / d.closed)} km per closed trip` : "No closed trips yet"],
    ["fare", "Fare", misMoney(d.trip_fare), "Trip Fare, before GST, MCD, parking and toll"],
    ["rate", "Fare per km", perKm, "Fare divided by total kms"],
    ["avg", "Average fare per trip", d.closed ? misMoney(d.trip_fare / d.closed) : "–", "Fare divided by closed trips"],
    ["billed", "Revenue", misMoney(d.total_trip_fare), "Fare with GST, MCD, parking and toll"],
  ].map(([tone, label, value, note]) => `<div class="mis-card ${tone}"><span>${label}</span><b>${value}</b><small>${note}</small></div>`).join("");
}
async function misLoadDash() {
  const request = _mis.dashSeq = (_mis.dashSeq || 0) + 1;
  $("#misDashTitle").textContent = misDashLabel();
  $("#misCards").classList.add("loading");
  if (!$("#misCards").children.length) $("#misCards").innerHTML = '<div class="mis-card lead"><span>Total trips</span><b>…</b><small>Adding up</small></div>';
  try {
    const params = new URLSearchParams();
    if (_mis.dashYear) params.set("year", _mis.dashYear);
    if (_mis.dashMonth) params.set("month", _mis.dashMonth);
    const data = await api("/api/eee-taxi/mis/dashboard?" + params);
    if (request !== _mis.dashSeq) return;
    const years = [...new Set([...data.years, Number(_mis.dashYear)].filter(Boolean))].sort((a, b) => b - a);
    $("#misDashYear").innerHTML = '<option value="">All years</option>' + years.map(year => `<option>${year}</option>`).join("");
    $("#misDashYear").value = _mis.dashYear;
    $("#misDashMonth").value = _mis.dashMonth;
    $("#misCards").innerHTML = misDashCards(data);
  } catch (error) { misMessage("Could not load the dashboard numbers: " + error.message); }
  finally { if (request === _mis.dashSeq) $("#misCards").classList.remove("loading"); }
}

// ── date range ──
function misSetRange(from, to) { $("#misFrom").value = from; $("#misTo").value = to; misRefilter(); }
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

// ── rendering ──
function misFare(trip) { return misNum(trip.total_trip_fare || trip.trip_fare); }

function misRender() {
  const trips = _mis.trips, sum = _mis.sum;
  const shown = new Set(trips.map(t => t.id));
  _mis.selected = new Set([..._mis.selected].filter(id => shown.has(id)));
  const counts = { all: sum.trips, open: sum.open, closed: sum.closed, fix: sum.need_fixing, pipeline: sum.pipeline };
  $("#misRange").textContent = misRangeLabel();
  misSyncDates($("#pane-eee-mis"));
  $("#misViews").innerHTML = MIS_VIEWS.map(([id, label]) => `<button class="mis-tab${_mis.view === id ? " active" : ""}${(id === "open" || id === "fix") && counts[id] ? " warn" : ""}" role="tab" aria-selected="${_mis.view === id}" onclick="misSetView('${id}')"><span>${label}</span><b>${misCount(counts[id])}</b></button>`).join("");
  // Money of every trip in the dates, client and search chosen, whichever tab is open.
  $("#misMoney").innerHTML = `<div class="mis-sum fare" title="Trip Fare of every trip in these dates, before GST, MCD, parking and toll"><span>Fare</span><b>${misMoney(sum.trip_fare)}</b></div>
    <div class="mis-sum revenue" title="Total Trip Fare of every trip in these dates: fare with GST, MCD, parking and toll"><span>Revenue</span><b>${misMoney(sum.total_trip_fare)}</b></div>`;
  $$("#misLayout .seg-btn").forEach(b => b.classList.toggle("active", b.dataset.layout === _mis.layout));
  misRenderSummary();
  const first = (_mis.page - 1) * _mis.pageSize + 1;
  $("#misCount").textContent = (_mis.total ? `Showing ${misCount(first)} to ${misCount(first + trips.length - 1)} of ${misCount(_mis.total)} ${_mis.total === 1 ? "trip" : "trips"}` : "No trips to show") +
    (_mis.view === "pipeline" || !sum.trips ? "" : ` · all ${misCount(sum.trips)} in these dates ran ${misCount(sum.total_kms)} km`);
  const piped = _mis.view === "pipeline", desk = _mis.layout === "desk" || piped;
  const oneDay = !piped && $("#misFrom").value && $("#misFrom").value === $("#misTo").value;
  $("#misHead").innerHTML = `<th><input type="checkbox" id="misAll" aria-label="Select all trips on this page" onchange="misSelectAll(this.checked)"></th><th>Status</th>` +
    (desk ? (oneDay ? "" : "<th>Date</th>") + "<th>Pick up</th><th>Client</th><th>Guest</th><th>Route</th><th>Driver and cab</th><th>Drop</th><th class='num'>Kms</th><th class='num'>Fare</th><th>Check</th><th class='mis-act'></th>"
      : MIS_SHEET.map(([, label, kind]) => `<th${kind === "num" ? ' class="num"' : ""}>${rcEsc(label)}</th>`).join("") + "<th>Check</th>");
  $("#misRows").innerHTML = trips.map(t => desk ? misDeskRow(t, oneDay) : misSheetRow(t)).join("") ||
    `<tr><td colspan="${desk ? 13 : MIS_SHEET.length + 3}" class="mis-empty">${misEmptyText()}</td></tr>`;
  misRenderPager();
  misUpdateSelection();
}
function misEmptyText() {
  if (_mis.view === "pipeline") return "Nothing in the pipeline from today onward. For a future booking, open New trip and choose Add to pipeline.";
  if ($("#misSearch").value.trim()) return "No trips match the search.";
  if (!_mis.sum.trips) return "No trips booked for these dates. Add a trip, or upload the MMT file.";
  if (_mis.view === "open") return "Nothing open here. Every duty in these dates is closed.";
  if (_mis.view === "fix") return "Every driver and cab here matches the masters.";
  return "No trips to show.";
}
function misRenderPager() {
  const pages = Math.max(1, Math.ceil(_mis.total / _mis.pageSize)), page = _mis.page;
  const go = (to, text, label, off) => `<button class="btn secondary sm" onclick="misGoPage(${to})" aria-label="${label}"${off ? " disabled" : ""}>${text}</button>`;
  const html = go(1, "« First", "First page", page <= 1) + go(page - 1, "‹ Previous", "Previous page", page <= 1) +
    `<span>Page <b>${misCount(page)}</b> of <b>${misCount(pages)}</b></span>` +
    go(page + 1, "Next ›", "Next page", page >= pages) + go(pages, "Last »", "Last page", page >= pages) +
    `<label>Trips per page <select onchange="misSetPageSize(this.value)">${MIS_PAGE_SIZES.map(size => `<option${size === _mis.pageSize ? " selected" : ""}>${size}</option>`).join("")}</select></label>`;
  $$(".mis-pager").forEach(pager => { pager.innerHTML = html; });
}
async function misGoPage(page) {
  _mis.page = Math.max(1, page);
  await misLoad();
  $("#pane-eee-mis .ed-table-wrap").scrollTop = 0;
}
function misSetPageSize(size) { _mis.pageSize = Number(size); misSavePrefs(); misRefilter(); }
function misStatusCell(t) {
  if (t.status === "Closed") return '<td><span class="badge generated">Closed</span></td>';
  const short = { "Trip Fare": "fare", "Drop Time": "drop time", "Total kms": "km" };
  return `<td><span class="badge draft">Open</span><div class="mis-miss">needs ${rcEsc(t.missing.map(m => short[m] || m).join(" · "))}</div>${t.in_pipeline && _mis.view !== "pipeline" ? '<div class="meta">In pipeline</div>' : ""}</td>`;
}
function misCheckCell(t) {
  const hired = t.hired ? '<span class="mis-zone" title="Cab and driver hired from outside; not checked against the masters">Hired</span> ' : "";
  return `<td>${hired}${t.issues.length ? t.issues.map(i => `<span class="mis-issue">${rcEsc(i)}</span>`).join("") : t.hired ? "" : '<span class="mis-ok">OK</span>'}</td>`;
}
function misPick(t) { return `<td><input type="checkbox" data-pick="${rcEsc(t.id)}" ${_mis.selected.has(t.id) ? "checked" : ""} aria-label="Select trip of ${rcEsc(t.guest_name || t.route_no)}"></td>`; }
function misDeskRow(t, oneDay) {
  const place = (address, zone) => `<span class="mis-place" title="${rcEsc(address)}">${rcEsc(address || "–")}</span>${zone ? `<span class="mis-zone">${rcEsc(zone)}</span>` : ""}`;
  return `<tr data-id="${rcEsc(t.id)}" class="${t.issues.length ? "mis-flag" : ""}">${misPick(t)}${misStatusCell(t)}
    ${oneDay ? "" : `<td>${rcEsc((_mis.view === "pipeline" && t.date === misIstToday() ? "Today, " : "") + misDay(t.date))}</td>`}<td class="mis-time">${rcEsc(t.pickup_time || "–")}</td>
    <td><b>${rcEsc(t.company_name)}</b>${t.company_other ? `<div class="meta">${rcEsc(t.company_other)}</div>` : ""}</td>
    <td><div class="cut" title="${rcEsc(t.guest_name)}">${rcEsc(t.guest_name || "–")}</div><div class="meta">${rcEsc(t.route_no)}</div></td>
    <td><div class="mis-leg">${place(t.pickup_address, t.pickup_zone)}</div><div class="mis-leg to">${place(t.drop_location, t.drop_zone)}</div></td>
    <td>${rcEsc(t.driver_name || "–")}<div class="meta">${rcEsc([t.cab_no, t.driver_id].filter(Boolean).join(" · "))}</div></td>
    <td class="mis-time">${rcEsc(t.drop_time)}<div class="meta">${rcEsc(t.trip_duration && t.drop_time ? t.trip_duration + " h" : "")}</div></td>
    <td class="num">${rcEsc(t.total_kms)}</td><td class="num">${t.trip_fare ? misMoney(misFare(t)) : ""}</td>${misCheckCell(t)}
    <td class="mis-act">${_mis.view === "pipeline" ? `<button class="btn secondary sm" data-unpipe="${rcEsc(t.id)}" title="The trip stays in the MIS">Remove from pipeline</button>`
      : t.status === "Open" ? `<button class="btn sm" data-close="${rcEsc(t.id)}">Close duty</button>` : ""}</td></tr>`;
}
function misSheetRow(t) {
  const cells = MIS_SHEET.map(([key, , kind]) => {
    const value = kind === "date" ? misDmy(t[key]) : t[key] || "";
    const cls = kind === "num" ? "num" : kind === "long" ? "cut" : "";
    return `<td class="${cls}"${kind === "long" ? ` title="${rcEsc(value)}"` : ""}>${rcEsc(value)}</td>`;
  }).join("");
  return `<tr data-id="${rcEsc(t.id)}" class="${t.issues.length ? "mis-flag" : ""}">${misPick(t)}${misStatusCell(t)}${cells}${misCheckCell(t)}</tr>`;
}
function misSetView(view) { _mis.view = view; misRefilter(); }
function misSetLayout(layout) { _mis.layout = layout; misSavePrefs(); misRender(); }
// The search runs on the server, so wait for a pause in the typing.
function misOnSearch() { clearTimeout(_mis.searchTimer); _mis.searchTimer = setTimeout(misRefilter, MIS_SEARCH_DELAY); }

// ── summary: the trips of the chosen tab added up by client, date, dispatcher... ──
function misToggleSummary() { _mis.summary = !_mis.summary; misSavePrefs(); misRenderSummary(); if (_mis.summary) misLoadSummary(); }
function misSetGroup(key) { _mis.groupBy = key; misSavePrefs(); misLoadSummary(); }
async function misLoadSummary() {
  const request = _mis.groupSeq = (_mis.groupSeq || 0) + 1;
  try {
    const params = misQuery();
    params.set("view", _mis.view);
    params.set("group", _mis.groupBy);
    const data = await api("/api/eee-taxi/mis/summary?" + params);
    if (request !== _mis.groupSeq) return;
    _mis.groups = data.groups;
    misRenderSummary();
  } catch (error) { misMessage("Could not load the summary: " + error.message); }
}
function misRenderSummary() {
  $("#misSummary").classList.toggle("hidden", !_mis.summary);
  $("#misSummaryBtn").classList.toggle("active", _mis.summary);
  if (!_mis.summary) return;
  $("#misGroups").innerHTML = MIS_GROUPS.map(([key, label]) => `<button class="seg-btn${_mis.groupBy === key ? " active" : ""}" onclick="misSetGroup('${key}')">${label}</button>`).join("");
  const byDate = _mis.groupBy === "date";
  const rows = _mis.groups.map(g => ({ name: g.name || "(blank)", trips: g.trips, open: g.open, kms: g.total_kms, fare: g.total_trip_fare }));
  const top = rows.slice(0, 40), most = Math.max(1, ...top.map(g => g.trips));
  const add = key => rows.reduce((s, g) => s + g[key], 0);
  const total = { trips: add("trips"), open: add("open"), kms: add("kms"), fare: add("fare") };
  const line = (g, name, attrs = "") => `<tr${attrs}><td>${name}</td><td class="num">${misCount(g.trips)}</td>
    <td class="num">${g.open ? `<span class="mis-n warn">${g.open}</span>` : "0"}</td><td class="num">${misCount(g.kms)}</td>
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
  if (_mis.groupBy === "company_name") { $("#misCompany").value = value; misRefilter(); }
  else if (_mis.groupBy === "date") misSetRange(value, value);
  else { $("#misSearch").value = value; misRefilter(); }
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
  const unpipe = event.target.closest("[data-unpipe]");
  if (unpipe) { misSetPipeline([unpipe.dataset.unpipe], false); return; }
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
// The Pipeline view is a second place to see a future booking; the trip itself is one MIS entry.
async function misSetPipeline(ids, on) {
  try {
    await misPost("/pipeline", { ids, in_pipeline: on });
    await misLoad();
    misMessage(on ? "Added to the pipeline. The trip is in the MIS as before." : "Removed from the pipeline. The trip is still in the MIS.", true);
  } catch (error) { misMessage("Could not update the pipeline: " + error.message); }
}
function misDeleteTrips(ids, after = () => {}) {
  rcModal({ title: `Delete ${misPlural(ids.length, "trip")}?`, sub: "Deleted trips are removed from the MIS at once and cannot be brought back.", okText: "Delete",
    onOk: async () => {
      const result = await misPost("/delete", { ids });
      _mis.selected.clear();
      after();
      await misReload();
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
      _mis.page = 1;
      await misReload();
      misMessage(`Booked ${misPlural(result.added, "trip")} on ${misDmy(date)}.` + (result.skipped_existing ? ` ${result.skipped_existing} were already there.` : ""), true);
    } });
}

// ── client file uploads (MMT, MongoDB) and Excel download ──
// Shown before an upload is taken: what the file would add, and which drivers and cabs would need fixing.
function misConfirmUpload(fileName, check, kind) {
  const changes = check.added + (check.updated || 0);
  const table = (title, head, rows) => rows.length ? `<h4>${title}</h4><div class="mis-check"><table class="data-table"><thead><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(cells => `<tr>${cells.map((c, i) => `<td${i === cells.length - 1 ? ' class="num"' : ""}>${rcEsc(String(c))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";
  const lines = [`<b>${misPlural(check.added, "new trip")}</b> will be added.`];
  if (check.updated) lines.push(`${misPlural(check.updated, "open trip")} already in the MIS will be completed from the file.`);
  if (check.skipped_existing) lines.push(`${check.skipped_existing} already in the MIS (same DS no/Route No) will be skipped, not added again.`);
  if (check.errors.length) lines.push(`${misPlural(check.errors.length, kind === "mmt" ? "row" : "trip")} ${kind === "mmt" ? "cannot be read and will be left out" : "need a look"}: ${rcEsc(check.errors.slice(0, 3).join(" | "))}${check.errors.length > 3 ? " …" : ""}`);
  const verdict = !changes ? "" : check.need_fixing
    ? `<p class="mis-check-warn"><b>${check.need_fixing} of these ${changes === 1 ? "trip" : "trips"}</b> will go under Needs fixing: the driver or the cab does not match the masters.</p>`
    : `<p class="mis-check-ok">Every driver and cab in these trips matches the masters.</p>`;
  return new Promise(resolve => {
    const wrap = document.createElement("div");
    wrap.className = "modal-backdrop";
    wrap.innerHTML = `<div class="modal mis-form" role="dialog" aria-modal="true" aria-labelledby="misCheckTitle">
        <h3 id="misCheckTitle">Check before uploading</h3><p class="modal-sub">${rcEsc(fileName)}. Nothing has been added yet.</p>
        <ul class="mis-check-list">${lines.map(line => `<li>${line}</li>`).join("")}</ul>${verdict}
        ${table("Drivers to fix", ["Driver name in file", "Mobile in file", "Problem", "Trips"], check.fix_drivers.map(d => [d.name || "(blank)", d.mobile || "(blank)", d.problem, d.trips]))}
        ${table("Cabs to fix", ["Cab No in file", "Problem", "Trips"], check.fix_cabs.map(c => [c.cab_no || "(blank)", c.problem, c.trips]))}
        <div class="modal-actions"><button type="button" class="btn secondary" data-cancel>${changes ? "Cancel, add nothing" : "Close"}</button>
          ${changes ? `<button type="button" class="btn" data-ok>Upload ${misPlural(changes, "trip")}</button>` : ""}</div>
      </div>`;
    document.body.appendChild(wrap);
    const done = answer => { wrap.remove(); resolve(answer); };
    wrap.querySelector("[data-cancel]").onclick = () => done(false);
    if (changes) wrap.querySelector("[data-ok]").onclick = () => done(true);
    wrap.addEventListener("keydown", event => { if (event.key === "Escape") done(false); });
    (wrap.querySelector("[data-ok]") || wrap.querySelector("[data-cancel]")).focus();
  });
}
async function misUpload(input, kind = "mmt") {
  const [endpoint, buttonSel, buttonText, client] = MIS_UPLOADS[kind];
  const file = input.files[0];
  input.value = "";
  if (!file) return;
  const dispatcher = $("#misDispatcher").value.trim();
  if (!dispatcher) { misMessage("Enter your name in the Dispatcher box, then upload the file again."); $("#misDispatcher").focus(); return; }
  misStore("misDispatcher", dispatcher);
  const send = dryRun => {
    const form = new FormData();
    form.append("file", file);
    form.append("dispatcher_name", dispatcher);
    form.append("dry_run", dryRun ? "true" : "false");
    return api("/api/eee-taxi/mis" + endpoint, { method: "POST", body: form });
  };
  const button = $(buttonSel);
  button.disabled = true;
  button.textContent = "Checking…";
  try {
    // First a check that saves nothing; the trips go in only after the user has seen what needs fixing.
    if (!await misConfirmUpload(file.name, await send(true), kind)) { misMessage("Upload cancelled. Nothing was added.", true); return; }
    button.textContent = "Uploading…";
    const result = await send(false);
    // Move the filters so the uploaded trips are on screen.
    if (result.first_date && (!$("#misFrom").value || result.first_date < $("#misFrom").value)) $("#misFrom").value = result.first_date;
    if (result.last_date && $("#misTo").value && result.last_date > $("#misTo").value) $("#misTo").value = result.last_date;
    if ((result.added || result.updated) && !["", client].includes($("#misCompany").value)) $("#misCompany").value = "";
    _mis.page = 1;
    await misReload();
    const parts = [`Added ${misPlural(result.added, "trip")} from ${file.name}.`];
    if (result.updated) parts.push(`${misPlural(result.updated, "open trip")} already in the MIS ${result.updated === 1 ? "was" : "were"} completed from the file.`);
    if (result.skipped_existing) parts.push(`${result.skipped_existing} already in the MIS were skipped.`);
    if (result.still_open) parts.push(`${result.still_open} came in Open with no fare because nobody had boarded; upload the report again after the shift to complete them.`);
    if (result.need_fixing) parts.push(`${result.need_fixing} are under Needs fixing: driver or cab does not match the masters.`);
    if (result.errors.length) parts.push(`${misPlural(result.errors.length, kind === "mmt" ? "row" : "trip")} ${kind === "mmt" ? "could not be read" : "need a look"}: ${result.errors.slice(0, 5).join(" | ")}${result.errors.length > 5 ? " …" : ""}`);
    misMessage(parts.join(" "), !result.errors.length);
  } catch (error) { misMessage("Upload failed: " + error.message); }
  finally { button.disabled = false; button.textContent = buttonText; }
}
async function misExport() {
  const params = misQuery();
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

// ── master matching (same rule as the server) ──
function misNameKey(name) { return (name || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }
function misFindByName(drivers, name) {
  const key = misNameKey(name);
  if (!key) return null;
  const same = drivers.filter(d => misNameKey(d.name) === key);
  if (same.length) return same.length === 1 ? same[0] : null;
  const starts = key.length >= 4 ? drivers.filter(d => misNameKey(d.name).startsWith(key)) : [];
  return starts.length === 1 ? starts[0] : null;
}
// A Driver ID without case or punctuation; "N/A" counts as no ID.
function misIdKey(id) { const key = (id || "").toUpperCase().replace(/[^A-Z0-9]/g, ""); return key === "NA" ? "" : key; }
function misHasPhone(driver, phone) { return phone.length === 10 && [driver.mobile, driver.alternate_mobile].some(m => misDigits(m) === phone); }
// "Faisal" fits "FAISAL KHAN"; a different name, or one too short to tell, does not.
function misNameFits(typed, driver) {
  const key = misNameKey(typed), full = misNameKey(driver.name);
  return !!key && (key === full || (key.length >= 4 && full.startsWith(key)));
}
// Same rule as the server: the Driver ID (Truein ID) decides when there is one; without one,
// the mobile and the name must both be the same master driver's.
function misFindDriver(values) {
  const drivers = (_mis.lookups && _mis.lookups.drivers) || [];
  const id = misIdKey(values.driver_id), phone = misDigits(values.driver_contact);
  if (id) return drivers.find(d => misIdKey(d.truein_id) === id) || null;
  const fits = drivers.filter(d => misHasPhone(d, phone) && misNameFits(values.driver_name, d));
  return fits.length === 1 ? fits[0] : null;
}
// While typing in the form one field is enough to look a driver up; the ID it fills in then decides.
function misGuessDriver(key, typed) {
  const drivers = (_mis.lookups && _mis.lookups.drivers) || [];
  if (!typed) return null;
  if (key === "driver_id") return drivers.find(d => misIdKey(d.truein_id) && misIdKey(d.truein_id) === misIdKey(typed)) || null;
  if (key === "driver_contact") { const owners = drivers.filter(d => misHasPhone(d, misDigits(typed))); return owners.length === 1 ? owners[0] : null; }
  return misFindByName(drivers, typed);
}

// ── trip form: new booking, full edit, or close duty ──
function misField([key, label, kind = ""], value) {
  const wide = kind.includes("wide") ? "wide" : "";
  if (kind === "company") {
    const companies = (_mis.lookups && _mis.lookups.companies) || [];
    const choices = value && !companies.includes(value) ? [value, ...companies] : companies;
    return `<label>${rcEsc(label)}<select data-m="${key}"><option value="">Choose client</option>${choices.map(c => `<option${c === value ? " selected" : ""}>${rcEsc(c)}</option>`).join("")}<option value="__new__">+ Add new client…</option></select></label>`;
  }
  if (kind === "hired") return `<label>${rcEsc(label)}<select data-m="${key}"><option value="">Own fleet</option><option value="Yes"${value === "Yes" ? " selected" : ""}>Hired</option></select></label>`;
  const type = kind === "date" || kind === "time" ? kind : "text";
  const list = (kind.match(/list:(\w+)/) || [])[1];
  const attrs = (list ? ` list="${list}"` : "") + (kind === "num" ? ' inputmode="decimal"' : "") + (kind === "auto" ? ' readonly tabindex="-1" placeholder="Worked out for you"' : "");
  const hinted = ["driver_name", "cab_no", "night_charge", "trip_fare", "total_kms"].includes(key) ? `<span class="field-hint" data-hint="${key}"></span>` : "";
  return `<label class="${wide}">${rcEsc(label)}<input type="${type}" data-m="${key}"${attrs} value="${rcEsc(value || "")}" autocomplete="off">${hinted}</label>`;
}
// "+ Add new client…" in the Company Name list: the name joins the list for every dispatcher.
function misAddClient(select) {
  select.value = "";
  rcModal({ title: "Add a new client", sub: "The name is added to the client list for everyone. For a one-off client, choose Others and type the name instead.",
    okText: "Add client", fields: [{ id: "name", label: "Client name", type: "text" }],
    onOk: async ({ name }) => {
      if (!name.trim()) throw new Error("Type the client's name.");
      const result = await misPost("/clients", { name: name.trim() });
      await misLoadLookups();
      select.innerHTML = '<option value="">Choose client</option>' + result.companies.map(c => `<option>${rcEsc(c)}</option>`).join("") + '<option value="__new__">+ Add new client…</option>';
      select.value = result.name;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    } });
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
function misRecalc(form, base, changed = "") {
  const set = (key, value) => { const input = form.querySelector(`[data-m="${key}"]`); if (input) input.value = value; };
  const input = key => form.querySelector(`[data-m="${key}"]`);
  misSyncDates(form);
  let v = { ...base, ...misFormValues(form) };
  const other = input("company_other");
  if (other) other.closest("label").style.display = v.company_name === "Others" ? "" : "none";
  // A hired cab and driver have no Truein ID and are not in the masters, so nothing is checked.
  const hired = v.hired === "Yes";
  if (input("driver_id")) input("driver_id").closest("label").style.display = hired ? "none" : "";
  if (input("start_odometer") && v.start_odometer && v.end_odometer) {
    const kms = misNum(v.end_odometer) - misNum(v.start_odometer);
    // A Total kms already typed (old sheets add hub km) changes only when an odometer is edited.
    if (kms >= 0 && (!v.total_kms || ["start_odometer", "end_odometer"].includes(changed))) set("total_kms", misTrim(kms));
    const same = misNum(input("total_kms").value) === kms;
    misHint(form, "total_kms", kms < 0 ? "End odometer is lower than start" : same ? "From the odometers" : `Odometers show ${misTrim(kms)} km`, kms >= 0 && same);
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
  if (hired && input("driver_name")) {
    misHint(form, "driver_name", v.driver_name || v.driver_contact ? "Hired driver: not checked against the master" : "");
    misHint(form, "cab_no", v.cab_no ? "Hired cab: not checked against the master" : "");
  } else if (_mis.lookups && input("driver_name")) {
    const driver = misFindDriver(v);
    if (!v.driver_id && !v.driver_name && !v.driver_contact) misHint(form, "driver_name", "");
    else if (!driver) misHint(form, "driver_name", misIdKey(v.driver_id) ? "This Driver ID is not in the driver master" : "Name and mobile must both match one driver in the master", false);
    else misHint(form, "driver_name", driver.status === "Deactive" ? "Deactive in the driver master" : "In the driver master", driver.status !== "Deactive");
    const plate = v.cab_no.toUpperCase().replace(/[^A-Z0-9]/g, "");
    misHint(form, "cab_no", !plate ? "" : _mis.lookups.cabs.includes(plate) ? "In the vehicle master" : "Not in the vehicle master", _mis.lookups.cabs.includes(plate));
  }
  const missing = [["trip_fare", "Trip Fare"], ["drop_time", "Drop Time"], ["total_kms", "Total kms"]].filter(([key]) => !v[key]).map(([, label]) => label);
  const state = form.querySelector("[data-state]");
  state.className = "mis-state " + (missing.length ? "open" : "closed");
  state.textContent = missing.length ? `Will stay Open: needs ${missing.join(", ")}` : "Will be saved as Closed";
}
// How a driver reads in the Driver Name list: "NAME (EEED/VK/0044)", or just the name when there is no ID.
function misDriverLabel(driver) { return driver.truein_id ? `${driver.name} (${driver.truein_id})` : driver.name; }
function misFillDriver(form, changedKey) {
  const values = misFormValues(form);
  if (values.hired === "Yes") return;
  const drivers = (_mis.lookups && _mis.lookups.drivers) || [];
  const picked = changedKey === "driver_name" && drivers.find(d => misDriverLabel(d) === values.driver_name);
  const driver = picked || misGuessDriver(changedKey, values[changedKey]);
  const contact = form.querySelector('[data-m="driver_contact"]');
  // A name the master does not know cannot keep the ID and phone of whoever was chosen before it.
  if (!driver && changedKey === "driver_name") {
    form.querySelector('[data-m="driver_id"]').value = "";
    if (contact.value && contact.value === contact.dataset.filled) contact.value = "";
  }
  if (!driver) return;
  form.querySelector('[data-m="driver_id"]').value = driver.truein_id;
  form.querySelector('[data-m="driver_name"]').value = driver.name;
  contact.value = contact.dataset.filled = misDigits(driver.mobile);
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
        ${closing || (base.status === "Closed" && !base.in_pipeline) ? "" : `<button type="button" class="btn secondary" data-pipe title="Saves the trip in the MIS and ${base.in_pipeline ? "stops showing it" : "also shows it"} in the Pipeline view">${base.in_pipeline ? "Save &amp; remove from pipeline" : isNew ? "Add to pipeline" : "Save &amp; add to pipeline"}</button>`}
      </div>
    </form>`;
  document.body.appendChild(wrap);
  const form = wrap.querySelector("form"), err = wrap.querySelector("[data-err]");
  form.querySelectorAll('input[type="date"]').forEach(misWrapDate);
  const close = () => wrap.remove();
  const fare = form.querySelector('[data-m="trip_fare"]');
  if (fare && !fare.value) fare.dataset.auto = "1";
  const save = async after => {
    // Ctrl+Enter reaches here even while a save is running; one save at a time.
    if (form.querySelector('button[type="submit"]').disabled) return;
    // Close mode shows only the closing fields; the booking details ride along unchanged.
    const kept = Object.fromEntries([...MIS_SHEET.map(([key]) => key), "company_other", "hired"].map(key => [key, base[key] || ""]));
    const body = { ...(isNew ? {} : kept), ...misFormValues(form) };
    if (form.querySelector(".mis-date-text.bad")) { err.textContent = "Type the date as DD/MM/YYYY, for example 05/11/2026."; return; }
    if (!body.date || !body.company_name) { err.textContent = "Date and Company Name are required."; return; }
    if (body.company_name === "Others" && !body.company_other) { err.textContent = "Type the client's name for Others."; return; }
    err.textContent = "";
    form.querySelectorAll("button").forEach(b => { b.disabled = true; });
    try {
      const toPipeline = after === "pipeline";
      await api("/api/eee-taxi/mis" + (isNew ? (toPipeline ? "?pipeline=true" : "") : "/" + encodeURIComponent(base.id)),
        { method: isNew ? "POST" : "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (toPipeline && !isNew) await misPost("/pipeline", { ids: [base.id], in_pipeline: !base.in_pipeline });
      const order = _mis.trips.map(t => t.id), at = order.indexOf(base.id);
      await misReload();
      misMessage(toPipeline ? (base.in_pipeline ? "Trip saved and removed from the pipeline." : "Trip saved in the MIS and added to the pipeline.") : isNew ? "Trip booked." : "Trip saved.", true);
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
        // The page holds 50 trips; when none of them is open, the next open duty is on another page.
        const next = order.slice(at + 1).map(id => open.find(t => t.id === id)).find(Boolean) || open[0] || await misNextOpen();
        if (next) misOpenForm(next, "close"); else misMessage("All duties in these dates are closed.", true);
      }
    } catch (error) { err.className = "text-err"; err.textContent = error.message; }
    finally { form.querySelectorAll("button").forEach(b => { b.disabled = false; }); }
  };
  form.addEventListener("submit", e => { e.preventDefault(); save(); });
  form.addEventListener("input", e => {
    if (e.target.dataset.m === "trip_fare") e.target.dataset.auto = e.target.value ? "0" : "1";
    misRecalc(form, isNew ? {} : base, e.target.dataset.m);
  });
  form.addEventListener("change", e => {
    const key = e.target.dataset.m;
    if (key === "company_name" && e.target.value === "__new__") { misAddClient(e.target); return; }
    if (["driver_id", "driver_name", "driver_contact"].includes(key)) misFillDriver(form, key);
    if (key === "cab_no") e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    misRecalc(form, isNew ? {} : base);
  });
  wrap.querySelector("[data-cancel]").onclick = close;
  if (isNew) wrap.querySelector("[data-again]").onclick = () => save("again");
  else {
    wrap.querySelector("[data-delete]").onclick = () => misDeleteTrips([base.id], close);
    wrap.querySelector("[data-dup]").onclick = () => { close(); misOpenForm(null, "new", { ...Object.fromEntries(MIS_DISPATCH_KEYS.map(key => [key, base[key]])), date: misIstToday(), route_no: /^\d{10}$/.test(base.route_no) ? base.route_no : "" }); };
  }
  if (closing) wrap.querySelector("[data-next]").onclick = () => save("next");
  const pipe = wrap.querySelector("[data-pipe]");
  if (pipe) pipe.onclick = () => save("pipeline");
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

// ── Masters page: MongoDB rate list (one fare per billing zone; shares the masters password) ──
const MG_URL = "/api/eee-taxi/mis/mongodb-rates";
let _mg = { saved: null, password: null };
function mgMessage(text, ok = false) {
  $("#mgMsg").className = "extraction-note " + (ok ? "ok" : "err");
  $("#mgMsg").textContent = text;
}
function mgFill(zones) {
  const off = _mg.password ? "" : "disabled";
  $("#mgRows").innerHTML = zones.map(z => `<tr><td><input type="text" data-mg="zone" maxlength="60" value="${rcEsc(z.zone)}" ${off} aria-label="Billing zone"></td>
    <td><input type="number" data-mg="fare" min="1" step="any" value="${rcEsc(z.fare)}" ${off} aria-label="Fare in rupees"></td></tr>`).join("");
  ["#mgCancel", "#mgAdd", "#mgSave"].forEach(sel => $(sel).classList.toggle("hidden", !_mg.password));
  $("#mgEdit").classList.toggle("hidden", !!_mg.password);
}
function mgRead() {
  return [...$$("#mgRows tr")].map(tr => ({ zone: tr.querySelector('[data-mg="zone"]').value.trim(), fare: tr.querySelector('[data-mg="fare"]').value.trim() })).filter(z => z.zone);
}
function mgShow(data) {
  _mg.saved = data.rates.zones;
  $("#mgMeta").textContent = "MongoDB · " + rcMetaText(data);
  mgFill(_mg.saved);
}
async function mgLoad() {
  if (_mg.password) return;
  try {
    const data = await api(MG_URL);
    _mstHasPassword = data.has_edit_password;
    mgShow(data);
  } catch (error) { mgMessage("Could not load the MongoDB rate list: " + error.message); }
}
function mgStartEdit() {
  if (!_mg.saved) return;
  mstUnlock(password => { _mg.password = password; mgFill(_mg.saved); mgMessage("Editing the MongoDB rate list. Save when ready.", true); });
}
function mgCancelEdit() { _mg.password = null; mgFill(_mg.saved); $("#mgMsg").className = "hidden"; }
function mgAddRow() { mgFill([...mgRead(), { zone: "", fare: "" }]); }
async function mgSave() {
  const zones = mgRead();
  if (!zones.length || zones.some(z => !(Number(z.fare) > 0))) { mgMessage("Every zone needs a fare above 0."); return; }
  try {
    const data = await api(MG_URL, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ edit_password: _mg.password, rates: { zones } }) });
    _mg.password = null;
    mgShow(data);
    mgMessage("MongoDB rate list saved. The next MongoDB upload uses these fares.", true);
  } catch (error) { mgMessage("Could not save: " + error.message); }
}
