const PALLIA_VEHICLE_MASTER_URL = "/api/pallia/vehicle-master";
const PALLIA_VERIFY_URL = "/api/pallia/vehicle-verification";
let pMasterVehicles = [];
let pMasterSavedVehicles = [];
let pMasterSurepassConfigured = false;
let pVerifySelected = new Set();
let pVerifyBusy = false;

function pNormalizeVehicle(value) { return String(value || "").replace(/[\s-]+/g, "").toUpperCase(); }
function pVehicleNo(item) { return typeof item === "string" ? pNormalizeVehicle(item) : pNormalizeVehicle(item?.registration_no); }
function pVehicleRecord(item) {
  return typeof item === "string" ? {exc:"", registration_no:pVehicleNo(item), registration_date:"", make:"", model:""} : {
    exc:String(item?.exc || ""),
    registration_no:pVehicleNo(item), registration_date:String(item?.registration_date || ""),
    make:String(item?.make || ""), model:String(item?.model || "")
  };
}
function pEscape(value) { return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch])); }
function pMasterDirty() { return JSON.stringify(pMasterVehicles) !== JSON.stringify(pMasterSavedVehicles); }
async function pMasterLoad() {
  const status = document.getElementById("pMasterStatus"); status.textContent = "Loading Pallia Vehicle Master...";
  try {
    const data = await api(PALLIA_VEHICLE_MASTER_URL);
    pMasterVehicles = (data.vehicles || []).map(pVehicleRecord);
    pMasterSavedVehicles = structuredClone(pMasterVehicles);
    pMasterSurepassConfigured = !!data.surepass_configured;
    status.textContent = `${pMasterVehicles.length} vehicles.${data.updated_by ? ` Last saved by ${data.updated_by}.` : ""}`;
    pMasterRender(); pVerifyRender();
  } catch (error) { status.textContent = `Could not load Pallia Vehicle Master: ${error.message}`; }
}
function pMasterRender() {
  const root = document.getElementById("pMasterVehicles");
  if (!root) return;
  root.innerHTML = pMasterVehicles.map((item, index) => `<tr><td>${pEscape(item.exc || "—")}</td><td>${pEscape(item.registration_no)}</td><td>${pEscape(item.registration_date || "—")}</td><td>${pEscape(item.make || "—")}</td><td>${pEscape(item.model || "—")}</td><td><button type="button" class="btn danger xs" onclick="pMasterRemove(${index})">Delete</button></td></tr>`).join("") || '<tr><td colspan="6" class="meta">No vehicles in the Pallia master yet.</td></tr>';
  const count = document.getElementById("pMasterCount"); if (count) count.textContent = `${pMasterVehicles.length} vehicles`;
  const save = document.getElementById("pMasterSave"); if (save) save.disabled = !pMasterDirty();
}
function pMasterAddRecord(record) {
  const no = pVehicleNo(record);
  if (!/^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$/.test(no)) throw new Error(`Invalid vehicle registration: ${no || "(blank)"}`);
  const found = pMasterVehicles.findIndex(item => item.registration_no === no);
  if (found >= 0) pMasterVehicles[found] = {...pMasterVehicles[found], ...pVehicleRecord(record)};
  else pMasterVehicles.push(pVehicleRecord(record));
}
function pMasterAdd() {
  const status = document.getElementById("pMasterStatus");
  try {
    const record = {registration_no:document.getElementById("pMasterInput").value,
      registration_date:document.getElementById("pMasterDate").value,
      make:document.getElementById("pMasterMake").value, model:document.getElementById("pMasterModel").value};
    const existed = pMasterVehicles.some(item => item.registration_no === pVehicleNo(record));
    pMasterAddRecord(record);
    ["pMasterInput", "pMasterDate", "pMasterMake", "pMasterModel"].forEach(id => document.getElementById(id).value = "");
    status.textContent = existed ? `${pVehicleNo(record)} updated. Save the master to share this change.` : "Vehicle added. Save the master to share this change.";
    pMasterRender(); pVerifyRender();
  } catch (error) { status.textContent = error.message; }
}
function pMasterRemove(index) {
  const [removed] = pMasterVehicles.splice(index, 1);
  pVerifySelected.delete(removed.registration_no);
  document.getElementById("pMasterStatus").textContent = `${removed.registration_no} deleted. Save the master to share this change.`;
  pMasterRender(); pVerifyRender();
}
function pCsvRows(text) {
  const rows=[]; let row=[], cell="", quoted=false;
  for (let i=0; i<text.length; i++) {
    const ch=text[i];
    if (quoted && ch==='"' && text[i+1]==='"') { cell+='"'; i++; }
    else if (ch==='"') quoted=!quoted;
    else if (ch==="," && !quoted) { row.push(cell); cell=""; }
    else if ((ch==="\n" || ch==="\r") && !quoted) { if (ch==="\r" && text[i+1]==="\n") i++; row.push(cell); if(row.some(v=>v.trim()))rows.push(row); row=[];cell=""; }
    else cell+=ch;
  }
  row.push(cell); if(row.some(v=>v.trim()))rows.push(row);
  if (!rows.length) throw new Error("The CSV file is empty.");
  const headers=rows.shift().map(h=>h.trim().replace(/^\uFEFF/, "").toLowerCase().replace(/[^a-z]/g,""));
  const col=(...names)=>headers.findIndex(h=>names.includes(h));
  const exc=col("exc","srno","serialnumber"), reg=col("registrationno","registrationnumber","vehiclenumber","registration");
  if(reg<0) throw new Error("CSV must include a Registration No column.");
  const date=col("registrationdate","dateofregistration"), make=col("make"), model=col("model");
  return rows.map(values=>({exc:exc<0?"":values[exc], registration_no:values[reg], registration_date:date<0?"":values[date], make:make<0?"":values[make], model:model<0?"":values[model]})).filter(v=>String(v.registration_no||"").trim());
}
async function pMasterImportCsv(input) {
  const file=input.files?.[0]; if(!file)return;
  try {
    const rows=pCsvRows(await file.text()); const next=[]; const seen=new Set();
    for(const row of rows){const rec=pVehicleRecord(row);if(!/^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$/.test(rec.registration_no))throw new Error(`Invalid vehicle registration: ${row.registration_no}`);if(!seen.has(rec.registration_no)){seen.add(rec.registration_no);next.push(rec);}}
    if(!next.length) throw new Error("No vehicle rows were found in the CSV.");
    pMasterVehicles=next;
    document.getElementById("pMasterStatus").textContent=`Imported ${next.length} vehicles from CSV. Save to replace the shared Pallia master.`;
    pMasterRender();pVerifyRender();
  } catch(error){document.getElementById("pMasterStatus").textContent=`Could not import CSV: ${error.message}`;}
  finally{input.value="";}
}
async function pMasterSave() {
  const button=document.getElementById("pMasterSave"); button.disabled=true;
  try {
    const data=await api(PALLIA_VEHICLE_MASTER_URL,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({vehicles:pMasterVehicles})});
    pMasterVehicles=(data.vehicles||[]).map(pVehicleRecord);pMasterSavedVehicles=structuredClone(pMasterVehicles);
    document.getElementById("pMasterStatus").textContent=`Saved ${pMasterVehicles.length} vehicles to the shared Pallia master.`;
    pMasterRender();pVerifyRender();
  } catch(error){document.getElementById("pMasterStatus").textContent=`Could not save vehicle master: ${error.message}`;button.disabled=!pMasterDirty();}
}
async function pVerifyLoad() {
  const status=document.getElementById("pVerifyStatus"); status.textContent="Loading Pallia Vehicle Master...";
  try {
    const data=await api(PALLIA_VEHICLE_MASTER_URL);pMasterVehicles=(data.vehicles||[]).map(pVehicleRecord);
    pMasterSavedVehicles=structuredClone(pMasterVehicles);pMasterSurepassConfigured=!!data.surepass_configured;
    pVerifySelected=new Set([...pVerifySelected].filter(no=>pMasterVehicles.some(v=>v.registration_no===no)));
    status.textContent=pMasterSurepassConfigured?`${pMasterVehicles.length} Pallia vehicles available. Each lookup may be charged; select up to 3 per run.`:"Surepass token is not configured on the server. Add AUTH_TOKEN_SUREPASS to the server environment.";
    pVerifyRender();
  } catch(error){status.textContent=`Could not load Pallia Vehicle Master: ${error.message}`;}
}
function pVerifyRender() {
  const root=document.getElementById("pVerifyVehicles");if(!root)return;
  const query=(document.getElementById("pVerifySearch")?.value||"").trim().toUpperCase();
  const selectedRoot=document.getElementById("pVerifySelectedList");
  const selectedItems=pMasterVehicles.filter(item=>pVerifySelected.has(item.registration_no));
  selectedRoot.innerHTML=selectedItems.map(item=>`<span class="vehicle-tag">${pEscape(item.registration_no)}<button type="button" aria-label="Remove ${pEscape(item.registration_no)} from selection" onclick="pVerifyRemove('${pEscape(item.registration_no)}')">&times;</button></span>`).join("")||'<span class="meta">No vehicles selected.</span>';
  const visible=pMasterVehicles.filter(item=>!pVerifySelected.has(item.registration_no)&&`${item.registration_no} ${item.make} ${item.model}`.toUpperCase().includes(query));
  root.innerHTML=visible.map(item=>`<label style="display:flex;align-items:center;gap:10px;padding:9px 10px;border-bottom:1px solid var(--border);cursor:pointer;"><input type="checkbox" data-p-vehicle="${pEscape(item.registration_no)}" onchange="pVerifySelect(this)"><span><strong>${pEscape(item.registration_no)}</strong><span class="meta" style="display:block">${pEscape([item.make,item.model].filter(Boolean).join(" · "))}</span></span></label>`).join("")||'<div class="meta" style="padding:12px;">No available vehicles found.</div>';
  const count=document.getElementById("pVerifySelected");if(count)count.textContent=`${pVerifySelected.size} selected`;
  const run=document.getElementById("pVerifyRun");if(run)run.disabled=pVerifyBusy||pVerifySelected.size===0||pVerifySelected.size>3||!pMasterSurepassConfigured;
  const all=document.getElementById("pVerifyAll");if(all){all.checked=false;all.indeterminate=false;all.disabled=visible.length===0;}
}
function pVerifySelect(input) {
  const vehicle=input.dataset.pVehicle;
  if(input.checked&&pVerifySelected.size>=3){document.getElementById("pVerifyStatus").textContent="Each lookup may be charged. Select no more than 3 vehicles per run.";}
  else if(input.checked)pVerifySelected.add(vehicle);
  pVerifyRender();
}
function pVerifyRemove(vehicle){pVerifySelected.delete(vehicle);pVerifyRender();}
function pVerifyToggleAll(checked) {
  const query=(document.getElementById("pVerifySearch").value||"").trim().toUpperCase();
  if(checked){for(const item of pMasterVehicles){if(pVerifySelected.size>=3)break;if(!pVerifySelected.has(item.registration_no)&&`${item.registration_no} ${item.make} ${item.model}`.toUpperCase().includes(query))pVerifySelected.add(item.registration_no);}}
  else pVerifySelected.clear();
  pVerifyRender();
}
async function pVerifyRun() {
  if(pVerifyBusy||!pVerifySelected.size)return;
  const button=document.getElementById("pVerifyRun"),status=document.getElementById("pVerifyStatus");pVerifyBusy=true;pVerifyRender();
  status.textContent=`Sending ${pVerifySelected.size} Surepass request(s)...`;
  try {
    const response=await fetch(PALLIA_VERIFY_URL,{method:"POST",headers:{"Content-Type":"application/json",...(authToken?{Authorization:`Bearer ${authToken}`}:{})},body:JSON.stringify({vehicles:[...pVerifySelected]})});
    if(response.status===401){logout();throw new Error("Session expired. Please sign in again.");}
    if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||`Verification failed (${response.status}).`);}
    const blob=await response.blob(),disposition=response.headers.get("Content-Disposition")||"",match=disposition.match(/filename="?([^";]+)"?/i),link=document.createElement("a");
    link.href=URL.createObjectURL(blob);link.download=match?match[1]:"pallia_vehicle_verification.csv";document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),10000);
    document.getElementById("pVerifyResult").textContent=`CSV downloaded for ${pVerifySelected.size} vehicle(s).`;
    status.textContent="Surepass verification finished. Review the downloaded CSV for each response.";
  } catch(error){document.getElementById("pVerifyResult").textContent=error.message;status.textContent=error.message;}
  finally{pVerifyBusy=false;pVerifyRender();}
}
