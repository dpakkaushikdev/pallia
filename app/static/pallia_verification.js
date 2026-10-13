const PALLIA_VEHICLE_MASTER_URL = "/api/pallia/vehicle-master";
const PALLIA_VERIFY_URL = "/api/pallia/vehicle-verification";
const PALLIA_BANK_VERIFY_URL = "/api/pallia/bank-verification";
const PALLIA_COMPANY_VERIFY_URL = "/api/pallia/company-details";
const PALLIA_GSTIN_VERIFY_URL = "/api/pallia/corporate-gstin";
let pMasterVehicles = [];
let pMasterSavedVehicles = [];
let pMasterSurepassConfigured = false;
let pVerifySelected = new Set();
let pVerifyBusy = false;
let pBankBusy = false;
let pStructuredBusy = false;
const PALLIA_API_RATE_ROWS = [
  ["rc_verification", "RC Verification"], ["aadhaar_pan_link", "Aadhaar PAN Link"],
  ["bank_verification", "Bank Verification"], ["company_details", "Company Details"],
  ["corporate_cin", "Corporate CIN"], ["corporate_gstin", "Corporate GSTIN"]
];
let pMasterApiRates = {};
let pMasterRatesEditing = false;
let pMasterRatesHasPassword = false;
const PALLIA_API_DESCRIPTIONS = {
  "rc-v2": "Checks a vehicle registration and returns the details available from the RC Verification API. Each lookup may incur a Surepass charge.",
  "aadhaar-pan-link": "Checks whether an Aadhaar number is linked with a PAN. Its input form and verification flow will be added later.",
  "bank-verification": "Checks whether a bank account exists and returns the account name and bank details. IFSC branch details are included automatically when an IFSC is supplied.",
  "company-details": "Looks up a company by CIN and returns available registration, financial, management, legal-status, director, and registered-charge details. Each lookup may incur a Surepass charge.",
  "corporate-cin": "Looks up corporate registration information using a Corporate Identification Number (CIN). Its input form will be added later.",
  "corporate-gstin": "Verifies a GSTIN and returns registration information, with optional filing status, HSN, filing frequency, and split address details. Each lookup may incur a Surepass charge."
};

function pVerifyApiChange() {
  const api = document.getElementById("pVerifyApi")?.value || "rc-v2";
  const isRc = api === "rc-v2";
  const isBank = api === "bank-verification";
  const isCompany = api === "company-details";
  const isGstin = api === "corporate-gstin";
  const description = document.getElementById("pVerifyApiDescription");
  const rcPanel = document.getElementById("pVerifyRcPanel");
  const rcResults = document.getElementById("pVerifyRcResults");
  const comingSoon = document.getElementById("pVerifyComingSoon");
  const bankPanel = document.getElementById("pVerifyBankPanel");
  const companyPanel = document.getElementById("pVerifyCompanyPanel");
  const gstinPanel = document.getElementById("pVerifyGstinPanel");
  if (description) {
    const rateKey = ({"rc-v2":"rc_verification", "bank-verification":"bank_verification", "company-details":"company_details", "corporate-gstin":"corporate_gstin"})[api];
    const rate = rateKey ? pMasterApiRates[rateKey] : null;
    const billing = rateKey ? (rate == null ? "Per-hit rate is not set in Pallia Master, so this API will not reduce the app wallet." : `App wallet deduction: ₹${Number(rate).toFixed(2)} per hit.`) : "";
    description.textContent = [PALLIA_API_DESCRIPTIONS[api] || "Description not available.", billing].filter(Boolean).join(" ");
  }
  if (rcPanel) rcPanel.hidden = !isRc;
  if (rcResults) rcResults.hidden = !isRc;
  if (bankPanel) bankPanel.hidden = !isBank;
  if (companyPanel) companyPanel.hidden = !isCompany;
  if (gstinPanel) gstinPanel.hidden = !isGstin;
  if (comingSoon) comingSoon.hidden = isRc || isBank || isCompany || isGstin;
  const bankSubmit = document.getElementById("pBankManualSubmit");
  const bankCsvSubmit = document.getElementById("pBankCsvSubmit");
  if (bankSubmit) bankSubmit.disabled = pBankBusy || !pMasterSurepassConfigured;
  if (bankCsvSubmit) bankCsvSubmit.disabled = pBankBusy || !pMasterSurepassConfigured;
  for (const id of ["pCompanyManualSubmit", "pCompanyCsvSubmit", "pGstinManualSubmit", "pGstinCsvSubmit"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = pStructuredBusy || !pMasterSurepassConfigured;
  }
}

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
  pMasterRatesEditing = false;
  const vehiclePassword = document.getElementById("pMasterVehiclePassword");
  if (vehiclePassword) vehiclePassword.value = "";
  const passwordInput = document.getElementById("pMasterRatesPassword");
  if (passwordInput) passwordInput.value = "";
  const status = document.getElementById("pMasterStatus"); status.textContent = "Loading Pallia Vehicle Master...";
  try {
    const data = await api(PALLIA_VEHICLE_MASTER_URL);
    pMasterVehicles = (data.vehicles || []).map(pVehicleRecord);
    pMasterSavedVehicles = structuredClone(pMasterVehicles);
    pMasterSurepassConfigured = !!data.surepass_configured;
    status.textContent = `${pMasterVehicles.length} vehicles.${data.updated_by ? ` Last saved by ${data.updated_by}.` : ""}`;
    pMasterRender(); pVerifyRender();
    await pMasterRatesLoad();
  } catch (error) { status.textContent = `Could not load Pallia Vehicle Master: ${error.message}`; }
}
async function pMasterRatesLoad() {
  const status = document.getElementById("pMasterRatesStatus");
  try {
    const data = await api("/api/pallia/api-rates");
    pMasterApiRates = data.rates || {};
    pMasterRatesHasPassword = !!data.has_edit_password;
    status.textContent = data.updated_by ? `Last updated by ${data.updated_by}${data.updated_at ? ` on ${new Date(data.updated_at).toLocaleString()}` : ""}.` : "Rates have not been edited yet; the current RC rate is ₹4 per hit.";
    if (!pMasterRatesHasPassword) status.textContent += " Set the shared Masters edit password first (EEE-Taxi > Masters).";
    pMasterRatesRender();
  } catch (error) { status.textContent = `Could not load API rates: ${error.message}`; }
}
function pMasterRatesRender() {
  const root = document.getElementById("pMasterApiRates");
  if (!root) return;
  root.innerHTML = PALLIA_API_RATE_ROWS.map(([key, label]) => {
    const value = pMasterApiRates[key];
    return `<tr><td>${label}</td><td><input type="number" min="0" step="0.01" data-p-api-rate="${key}" value="${value == null ? "" : pEscape(value)}" placeholder="Not set" ${pMasterRatesEditing ? "" : "disabled"} style="max-width:180px;"></td></tr>`;
  }).join("");
  const unlock = document.getElementById("pMasterRatesUnlockBtn");
  const save = document.getElementById("pMasterRatesSaveBtn");
  const cancel = document.getElementById("pMasterRatesCancelBtn");
  const password = document.getElementById("pMasterRatesPassword");
  if (unlock) unlock.hidden = pMasterRatesEditing;
  if (save) save.hidden = !pMasterRatesEditing;
  if (cancel) cancel.hidden = !pMasterRatesEditing;
  if (password) password.disabled = pMasterRatesEditing;
}
async function pMasterRatesUnlock() {
  const password = document.getElementById("pMasterRatesPassword").value;
  const status = document.getElementById("pMasterRatesStatus");
  if (!password) { status.textContent = "Enter the shared Masters edit password to unlock rates."; return; }
  try {
    await api("/api/pallia/api-rates/unlock", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({password})});
    pMasterRatesEditing = true;
    status.textContent = "Rates unlocked. The password will be checked again when you save.";
    pMasterRatesRender();
  } catch (error) { status.textContent = `Could not unlock API rates: ${error.message}`; }
}
function pMasterRatesCancel() {
  pMasterRatesEditing = false;
  document.getElementById("pMasterRatesPassword").value = "";
  pMasterRatesLoad();
}
async function pMasterRatesSave() {
  const password = document.getElementById("pMasterRatesPassword").value;
  const status = document.getElementById("pMasterRatesStatus");
  const rates = {};
  for (const input of document.querySelectorAll("[data-p-api-rate]")) {
    const raw = input.value.trim();
    if (raw && (!Number.isFinite(Number(raw)) || Number(raw) < 0)) { status.textContent = "Rates must be non-negative numbers or left blank."; return; }
    rates[input.dataset.pApiRate] = raw ? Number(raw) : null;
  }
  try {
    const data = await api("/api/pallia/api-rates", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({edit_password:password, rates})});
    pMasterApiRates = data.rates || rates;
    pMasterRatesEditing = false;
    document.getElementById("pMasterRatesPassword").value = "";
    status.textContent = `API rates saved${data.updated_by ? ` by ${data.updated_by}` : ""}.`;
    pMasterRatesRender();
  } catch (error) { status.textContent = `Could not save API rates: ${error.message}`; }
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
  const button=document.getElementById("pMasterSave");
  const passwordInput=document.getElementById("pMasterVehiclePassword");
  const editPassword=passwordInput?.value||"";
  const status=document.getElementById("pMasterStatus");
  if(!editPassword.trim()){status.textContent="Enter the Masters edit password to save vehicle changes.";passwordInput?.focus();return;}
  button.disabled=true;
  try {
    const data=await api(PALLIA_VEHICLE_MASTER_URL,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({vehicles:pMasterVehicles,edit_password:editPassword})});
    pMasterVehicles=(data.vehicles||[]).map(pVehicleRecord);pMasterSavedVehicles=structuredClone(pMasterVehicles);
    status.textContent=`Saved ${pMasterVehicles.length} vehicles to the shared Pallia master.`;
    pMasterRender();pVerifyRender();
  } catch(error){status.textContent=`Could not save vehicle master: ${error.message}`;button.disabled=!pMasterDirty();}
  finally{if(passwordInput)passwordInput.value="";}
}
async function pVerifyLoad() {
  pVerifyApiChange();
  const status=document.getElementById("pVerifyStatus"); status.textContent="Loading Pallia Vehicle Master...";
  try {
    const data=await api(PALLIA_VEHICLE_MASTER_URL);pMasterVehicles=(data.vehicles||[]).map(pVehicleRecord);
    pMasterSavedVehicles=structuredClone(pMasterVehicles);pMasterSurepassConfigured=!!data.surepass_configured;
    pVerifySelected=new Set([...pVerifySelected].filter(no=>pMasterVehicles.some(v=>v.registration_no===no)));
    status.textContent=pMasterSurepassConfigured?`${pMasterVehicles.length} Pallia vehicles available. Each vehicle lookup may incur a charge.`:"Surepass token is not configured on the server. Add AUTH_TOKEN_SUREPASS to the server environment.";
    pVerifyRender();
    await pWalletLoad();
  } catch(error){status.textContent=`Could not load Pallia Vehicle Master: ${error.message}`;}
}
async function pWalletLoad() {
  const balance=document.getElementById("pWalletBalance"),status=document.getElementById("pWalletStatus");
  try {
    const data=await api("/api/pallia/wallet");
    pMasterApiRates=data.rates||{};
    if(balance)balance.textContent=new Intl.NumberFormat("en-IN",{style:"currency",currency:"INR",minimumFractionDigits:2,maximumFractionDigits:2}).format(data.balance);
    if(status)status.textContent=`App estimate based on the ₹56,220.51 starting balance and recorded hits. Estimated deductions to date: ₹${Number(data.estimated_charged_total||0).toFixed(2)}. This balance does not sync with Surepass.`;
    pVerifyApiChange();
  } catch(error) {if(status)status.textContent=`Could not load app wallet balance: ${error.message}`;}
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
  const run=document.getElementById("pVerifyRun");if(run)run.disabled=pVerifyBusy||pVerifySelected.size===0||!pMasterSurepassConfigured;
  const all=document.getElementById("pVerifyAll");if(all){all.checked=false;all.indeterminate=false;all.disabled=visible.length===0;}
}
function pVerifySelect(input) {
  const vehicle=input.dataset.pVehicle;
  if(input.checked)pVerifySelected.add(vehicle);
  pVerifyRender();
}
function pVerifyRemove(vehicle){pVerifySelected.delete(vehicle);pVerifyRender();}
function pVerifyToggleAll(checked) {
  const query=(document.getElementById("pVerifySearch").value||"").trim().toUpperCase();
  if(checked){for(const item of pMasterVehicles){if(!pVerifySelected.has(item.registration_no)&&`${item.registration_no} ${item.make} ${item.model}`.toUpperCase().includes(query))pVerifySelected.add(item.registration_no);}}
  else pVerifySelected.clear();
  pVerifyRender();
}
async function pVerifyRun() {
  if ((document.getElementById("pVerifyApi")?.value || "rc-v2") !== "rc-v2") return;
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
    status.textContent="Verification finished. The CSV includes one row per selected vehicle; check its success and error columns.";
    await pWalletLoad();
  } catch(error){document.getElementById("pVerifyResult").textContent=error.message;status.textContent=error.message;}
  finally{pVerifyBusy=false;pVerifyRender();}
}

function pBankCsvRows(text) {
  const rows=[]; let row=[], cell="", quoted=false;
  for(let i=0;i<text.length;i++){
    const ch=text[i];
    if(quoted&&ch==='"'&&text[i+1]==='"'){cell+='"';i++;}
    else if(ch==='"')quoted=!quoted;
    else if(ch===","&&!quoted){row.push(cell);cell="";}
    else if((ch==="\n"||ch==="\r")&&!quoted){if(ch==="\r"&&text[i+1]==="\n")i++;row.push(cell);if(row.some(v=>v.trim()))rows.push(row);row=[];cell="";}
    else cell+=ch;
  }
  row.push(cell);if(row.some(v=>v.trim()))rows.push(row);
  if(!rows.length)throw new Error("The CSV file is empty.");
  const headers=rows.shift().map(h=>h.trim().replace(/^\uFEFF/,"").toLowerCase().replace(/[^a-z]/g,""));
  const idIndex=headers.findIndex(h=>["idnumber","accountnumber","accountno","bankaccountnumber"].includes(h));
  const ifscIndex=headers.indexOf("ifsc");
  if(idIndex<0)throw new Error("CSV must include an ID Number column.");
  const accounts=rows.map(values=>({id_number:String(values[idIndex]||"").trim(),ifsc:ifscIndex<0?"":String(values[ifscIndex]||"").trim()}));
  if(!accounts.length)throw new Error("No account rows were found in the CSV.");
  return accounts;
}

async function pBankSubmit(accounts) {
  if(pBankBusy) return;
  if(!pMasterSurepassConfigured)throw new Error("Surepass is not configured on the server.");
  pBankBusy=true;
  const status=document.getElementById("pBankCsvStatus");
  const result=document.getElementById("pBankResult");
  const preview=document.getElementById("pBankCsvPreview");
  pVerifyApiChange();
  status.textContent=`Sending ${accounts.length} bank verification request(s). Each valid account lookup may incur a charge...`;
  result.textContent="Verification is running...";
  preview.hidden=true;
  try{
    const response=await fetch(PALLIA_BANK_VERIFY_URL,{method:"POST",headers:{"Content-Type":"application/json",...(authToken?{Authorization:`Bearer ${authToken}`}:{})},body:JSON.stringify({accounts})});
    if(response.status===401){logout();throw new Error("Session expired. Please sign in again.");}
    if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||`Bank verification failed (${response.status}).`);}
    const blob=await response.blob(),text=await blob.text(),disposition=response.headers.get("Content-Disposition")||"",match=disposition.match(/filename="?([^";]+)"?/i),link=document.createElement("a");
    link.href=URL.createObjectURL(blob);link.download=match?match[1]:"pallia_bank_verification.csv";document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),10000);
    preview.textContent=text.length>12000?`${text.slice(0,12000)}\n… Preview shortened; the downloaded CSV contains all rows.`:text;
    preview.hidden=false;
    result.textContent=`CSV downloaded with ${accounts.length} row(s). Review the success and error columns for per-account results.`;
    status.textContent="Bank verification finished. IFSC details were requested only for rows with an IFSC value.";
    await pWalletLoad();
  }catch(error){result.textContent=error.message;status.textContent=error.message;throw error;}
  finally{pBankBusy=false;pVerifyApiChange();}
}

async function pBankVerifyManual(){
  const idNumber=document.getElementById("pBankIdNumber").value.trim();
  const ifsc=document.getElementById("pBankIfsc").value.trim();
  const status=document.getElementById("pBankCsvStatus");
  if(!idNumber){status.textContent="Enter an ID Number.";document.getElementById("pBankIdNumber").focus();return;}
  try{await pBankSubmit([{id_number:idNumber,ifsc}]);}
  catch(error){/* The shared result area already shows the request error. */}
}

async function pBankVerifyCsv(){
  const file=document.getElementById("pBankCsvFile").files?.[0];
  const status=document.getElementById("pBankCsvStatus");
  if(!file){status.textContent="Choose a CSV file first.";return;}
  try{await pBankSubmit(pBankCsvRows(await file.text()));}
  catch(error){status.textContent=error.message;}
  finally{document.getElementById("pBankCsvFile").value="";}
}

function pStructuredCsvIds(text, label) {
  const rows=[];let row=[],cell="",quoted=false;
  for(let i=0;i<text.length;i++){
    const ch=text[i];
    if(quoted&&ch==='"'&&text[i+1]==='"'){cell+='"';i++;}
    else if(ch==='"')quoted=!quoted;
    else if(ch===","&&!quoted){row.push(cell);cell="";}
    else if((ch==="\n"||ch==="\r")&&!quoted){if(ch==="\r"&&text[i+1]==="\n")i++;row.push(cell);if(row.some(v=>v.trim()))rows.push(row);row=[];cell="";}
    else cell+=ch;
  }
  row.push(cell);if(row.some(v=>v.trim()))rows.push(row);
  if(!rows.length)throw new Error("The CSV file is empty.");
  const headers=rows.shift().map(h=>h.trim().replace(/^\uFEFF/,"").toLowerCase().replace(/[^a-z]/g,""));
  const idIndex=headers.findIndex(h=>["idnumber","cin","gstin","corporateidentificationnumber"].includes(h));
  if(idIndex<0)throw new Error(`CSV must include an ID Number or ${label} column.`);
  const ids=rows.map(values=>String(values[idIndex]||"").trim().toUpperCase());
  if(!ids.length)throw new Error("No data rows were found in the CSV.");
  return ids.map(id_number=>({id_number}));
}

function pGstinOptions() {
  return {
    financial_year: document.getElementById("pGstinFinancialYear")?.value || "2024",
    filing_status: !!document.getElementById("pGstinFilingStatus")?.checked,
    hsn_info: !!document.getElementById("pGstinHsnInfo")?.checked,
    filing_frequency: !!document.getElementById("pGstinFilingFrequency")?.checked,
    split_address: !!document.getElementById("pGstinSplitAddress")?.checked
  };
}

async function pStructuredSubmit(apiName, requests) {
  if(pStructuredBusy)return;
  if(!pMasterSurepassConfigured)throw new Error("Surepass is not configured on the server.");
  const isGstin=apiName==="corporate-gstin",prefix=isGstin?"pGstin":"pCompany";
  const url=isGstin?PALLIA_GSTIN_VERIFY_URL:PALLIA_COMPANY_VERIFY_URL;
  const status=document.getElementById(`${prefix}CsvStatus`),result=document.getElementById(`${prefix}Result`),preview=document.getElementById(`${prefix}CsvPreview`);
  pStructuredBusy=true;pVerifyApiChange();
  status.textContent=`Sending ${requests.length} ${isGstin?"GSTIN":"company"} verification request(s). Each non-empty lookup may incur a charge...`;
  result.textContent="Verification is running...";preview.hidden=true;
  const options=isGstin?pGstinOptions():{};
  try{
    const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json",...(authToken?{Authorization:`Bearer ${authToken}`}:{})},body:JSON.stringify({requests:requests.map(item=>({...item,...options}))})});
    if(response.status===401){logout();throw new Error("Session expired. Please sign in again.");}
    if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail||`Verification failed (${response.status}).`);}
    const blob=await response.blob(),text=await blob.text(),disposition=response.headers.get("Content-Disposition")||"",match=disposition.match(/filename="?([^";]+)"?/i),link=document.createElement("a");
    link.href=URL.createObjectURL(blob);link.download=match?match[1]:`${isGstin?"pallia_corporate_gstin":"pallia_company_details"}.csv`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),10000);
    preview.textContent=text.length>16000?`${text.slice(0,16000)}\n… Preview shortened; the downloaded CSV contains all rows.`:text;preview.hidden=false;
    result.textContent=`CSV downloaded with ${requests.length} row(s). Review the success and error columns for per-request results.`;
    status.textContent=`${isGstin?"Corporate GSTIN":"Company Details"} verification finished. Each input has a CSV row, including any lookup failure.`;
    await pWalletLoad();
  }catch(error){result.textContent=error.message;status.textContent=error.message;throw error;}
  finally{pStructuredBusy=false;pVerifyApiChange();}
}

async function pStructuredVerifyManual(apiName) {
  const isGstin=apiName==="corporate-gstin",prefix=isGstin?"pGstin":"pCompany";
  const status=document.getElementById(`${prefix}CsvStatus`);
  const idNumber=document.getElementById(`${prefix}IdNumber`).value.trim();
  if(!idNumber){status.textContent=`Enter an ${isGstin?"ID Number (GSTIN)":"ID Number (CIN)"}.`;document.getElementById(`${prefix}IdNumber`).focus();return;}
  try{await pStructuredSubmit(apiName,[{id_number:idNumber}]);}catch(error){status.textContent=error.message;}
}

async function pStructuredVerifyCsv(apiName) {
  const isGstin=apiName==="corporate-gstin",prefix=isGstin?"pGstin":"pCompany",label=isGstin?"GSTIN":"CIN";
  const fileInput=document.getElementById(`${prefix}CsvFile`),status=document.getElementById(`${prefix}CsvStatus`),file=fileInput.files?.[0];
  if(!file){status.textContent="Choose a CSV file first.";return;}
  try{await pStructuredSubmit(apiName,pStructuredCsvIds(await file.text(),label));}
  catch(error){status.textContent=error.message;}
  finally{fileInput.value="";}
}

(()=>{
  const select=document.getElementById("pGstinFinancialYear");
  if(select&&!select.options.length){for(let year=2026;year>=2017;year--){const option=document.createElement("option");option.value=String(year);option.textContent=String(year);if(year===2024)option.selected=true;select.appendChild(option);}}
})();
