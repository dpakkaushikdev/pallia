const PALLIA_VEHICLE_MASTER_URL = "/api/pallia/vehicle-master";
const PALLIA_VERIFY_URL = "/api/pallia/vehicle-verification";
let pMasterVehicles = [];
let pMasterSavedVehicles = [];
let pMasterSurepassConfigured = false;
let pVerifySelected = new Set();
let pVerifyBusy = false;

function pNormalizeVehicle(value) {
  return String(value || "").replace(/[\s-]+/g, "").toUpperCase();
}
function pEscape(value) {
  return String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
}
function pMasterDirty() {
  return JSON.stringify(pMasterVehicles) !== JSON.stringify(pMasterSavedVehicles);
}
async function pMasterLoad() {
  const status = document.getElementById("pMasterStatus");
  status.textContent = "Loading Pallia Vehicle Master...";
  try {
    const data = await api(PALLIA_VEHICLE_MASTER_URL);
    pMasterVehicles = data.vehicles || [];
    pMasterSurepassConfigured = !!data.surepass_configured;
    // Migrate the old Pallia-only browser validation list to the shared master once.
    if (!pMasterVehicles.length) {
      try {
        const legacy = JSON.parse(localStorage.getItem("pallia_vehicle_master") || "[]").map(pNormalizeVehicle).filter(Boolean);
        const unique = [...new Set(legacy)];
        if (unique.length) {
          const migrated = await api(PALLIA_VEHICLE_MASTER_URL, {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({vehicles:unique})});
          pMasterVehicles = migrated.vehicles || unique;
        }
      } catch (_) { /* Keep loading the shared master if browser migration is unavailable. */ }
    }
    pMasterSavedVehicles = [...pMasterVehicles];
    const updated = data.updated_by ? ` Last saved by ${data.updated_by}.` : "";
    status.textContent = `${pMasterVehicles.length} vehicles.${updated}`;
    pMasterRender();
    pVerifyRender();
  } catch (error) {
    status.textContent = `Could not load Pallia Vehicle Master: ${error.message}`;
  }
}
function pMasterRender() {
  const root = document.getElementById("pMasterVehicles");
  root.innerHTML = pMasterVehicles.map((vehicle, index) => `<span class="vehicle-tag">${pEscape(vehicle)}<button type="button" aria-label="Remove ${pEscape(vehicle)}" onclick="pMasterRemove(${index})">&times;</button></span>`).join("") || '<span class="meta">No vehicles in the Pallia master yet.</span>';
  document.getElementById("pMasterSave").disabled = !pMasterDirty();
}
function pMasterAdd() {
  const input = document.getElementById("pMasterInput");
  const vehicle = pNormalizeVehicle(input.value);
  const status = document.getElementById("pMasterStatus");
  if (!/^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$/.test(vehicle)) {
    status.textContent = "Enter a valid registration, for example DL08AB1234."; return;
  }
  if (pMasterVehicles.includes(vehicle)) { status.textContent = `${vehicle} is already in the Pallia master.`; return; }
  pMasterVehicles.push(vehicle); pMasterVehicles.sort(); input.value = "";
  status.textContent = `${vehicle} added. Save the master to share this change.`;
  pMasterRender(); pVerifyRender();
}
function pMasterBulkAdd() {
  const input = document.getElementById("pMasterBulk");
  const values = input.value.split(/[\s,;]+/).map(pNormalizeVehicle).filter(Boolean);
  const status = document.getElementById("pMasterStatus");
  const invalid = values.find(vehicle => !/^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$/.test(vehicle));
  if (invalid) { status.textContent = `Invalid vehicle registration: ${invalid}`; return; }
  const before = pMasterVehicles.length;
  pMasterVehicles = [...new Set([...pMasterVehicles, ...values])].sort();
  input.value = "";
  status.textContent = `${pMasterVehicles.length - before} vehicle(s) added. Save the master to share this change.`;
  pMasterRender(); pVerifyRender();
}
function pMasterRemove(index) {
  const [removed] = pMasterVehicles.splice(index, 1);
  pVerifySelected.delete(removed);
  document.getElementById("pMasterStatus").textContent = `${removed} removed. Save the master to share this change.`;
  pMasterRender(); pVerifyRender();
}
async function pMasterSave() {
  const button = document.getElementById("pMasterSave");
  button.disabled = true;
  try {
    const data = await api(PALLIA_VEHICLE_MASTER_URL, {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({vehicles:pMasterVehicles})});
    pMasterVehicles = data.vehicles || [];
    pMasterSavedVehicles = [...pMasterVehicles];
    document.getElementById("pMasterStatus").textContent = `Saved ${pMasterVehicles.length} vehicles to the shared Pallia master.`;
    pMasterRender(); pVerifyRender();
  } catch (error) {
    document.getElementById("pMasterStatus").textContent = `Could not save vehicle master: ${error.message}`;
    button.disabled = !pMasterDirty();
  }
}
async function pVerifyLoad() {
  const status = document.getElementById("pVerifyStatus");
  status.textContent = "Loading Pallia Vehicle Master...";
  try {
    const data = await api(PALLIA_VEHICLE_MASTER_URL);
    pMasterVehicles = data.vehicles || [];
    pMasterSavedVehicles = [...pMasterVehicles];
    pMasterSurepassConfigured = !!data.surepass_configured;
    pVerifySelected = new Set([...pVerifySelected].filter(vehicle => pMasterVehicles.includes(vehicle)));
    status.textContent = pMasterSurepassConfigured
      ? `${pMasterVehicles.length} Pallia vehicles available. Each lookup may be charged; select up to 3 per run.`
      : "Surepass token is not configured on the server. Add AUTH_TOKEN_SUREPASS to the server environment.";
    pVerifyRender();
  } catch (error) {
    status.textContent = `Could not load Pallia Vehicle Master: ${error.message}`;
  }
}
function pVerifyRender() {
  const root = document.getElementById("pVerifyVehicles");
  if (!root) return;
  const query = (document.getElementById("pVerifySearch")?.value || "").trim().toUpperCase();
  const visible = pMasterVehicles.filter(vehicle => vehicle.includes(query));
  root.innerHTML = visible.map(vehicle => `<label style="display:flex;align-items:center;gap:10px;padding:9px 10px;border-bottom:1px solid var(--border);"><input type="checkbox" data-p-vehicle="${pEscape(vehicle)}" ${pVerifySelected.has(vehicle) ? "checked" : ""} onchange="pVerifySelect(this)"><span>${pEscape(vehicle)}</span></label>`).join("") || '<div class="meta" style="padding:12px;">No vehicles found. Add vehicles in Pallia Master.</div>';
  const selected = pVerifySelected.size;
  const count = document.getElementById("pVerifySelected");
  if (count) count.textContent = `${selected} selected`;
  const run = document.getElementById("pVerifyRun");
  if (run) run.disabled = pVerifyBusy || selected === 0 || selected > 3 || !pMasterSurepassConfigured;
  const all = document.getElementById("pVerifyAll");
  if (all) {
    const selectedVisible = visible.filter(vehicle => pVerifySelected.has(vehicle)).length;
    all.checked = visible.length > 0 && selectedVisible === visible.length;
    all.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
    all.disabled = visible.length === 0;
  }
}
function pVerifySelect(input) {
  const vehicle = input.dataset.pVehicle;
  if (input.checked && pVerifySelected.size >= 3) {
    pVerifySelected.delete(vehicle);
    document.getElementById("pVerifyStatus").textContent = "Each lookup may be charged. Select no more than 3 vehicles per run.";
  } else if (input.checked) pVerifySelected.add(vehicle);
  else pVerifySelected.delete(vehicle);
  pVerifyRender();
}
function pVerifyToggleAll(checked) {
  const query = (document.getElementById("pVerifySearch").value || "").trim().toUpperCase();
  const visible = pMasterVehicles.filter(item => item.includes(query));
  if (checked) {
    for (const vehicle of visible) {
      if (pVerifySelected.size >= 3) break;
      pVerifySelected.add(vehicle);
    }
  } else {
    for (const vehicle of visible) pVerifySelected.delete(vehicle);
  }
  if (checked && visible.some(vehicle => !pVerifySelected.has(vehicle)))
    document.getElementById("pVerifyStatus").textContent = "Each lookup may be charged. Selected up to the 3-vehicle limit.";
  pVerifyRender();
}
async function pVerifyRun() {
  if (pVerifyBusy || !pVerifySelected.size) return;
  const button = document.getElementById("pVerifyRun");
  const status = document.getElementById("pVerifyStatus");
  pVerifyBusy = true; pVerifyRender();
  status.textContent = `Sending ${pVerifySelected.size} Surepass request(s)...`;
  try {
    const response = await fetch(PALLIA_VERIFY_URL, {
      method:"POST",
      headers:{"Content-Type":"application/json", ...(authToken ? {Authorization:`Bearer ${authToken}`} : {})},
      body:JSON.stringify({vehicles:[...pVerifySelected]})
    });
    if (response.status === 401) { logout(); throw new Error("Session expired. Please sign in again."); }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Verification failed (${response.status}).`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = match ? match[1] : "pallia_vehicle_verification.csv";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    const count = pVerifySelected.size;
    document.getElementById("pVerifyResult").textContent = `CSV downloaded for ${count} vehicle(s).`;
    status.textContent = "Surepass verification finished. Review the downloaded CSV for each response.";
  } catch (error) {
    document.getElementById("pVerifyResult").textContent = error.message;
    status.textContent = error.message;
  } finally {
    pVerifyBusy = false; button.disabled = false; pVerifyRender();
  }
}
