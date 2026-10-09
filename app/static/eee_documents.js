const ed = {entry: null, busy: false, offset: 0, total: 0, listVersion: 0, previewUrl: null, password: "", historyOffset: 0, historyTotal: 0, historyVersion: 0};
const edNames = {invoice: "Invoice", ds: "DS", parking: "Parking", toll_mcd: "Toll / MCD", gps: "GPS", email_screenshot: "Email Screenshot"};
const edBase = "/api/eee-taxi/documents";
const edEl = id => document.getElementById(id);
const edEscape = value => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));

function edReset() {
  ed.listVersion++; ed.historyVersion++;
  edEl("ehEntries").replaceChildren();
  edEl("ehCount").textContent = "";
  ed.entry = null; ed.password = "";
  edClosePreview();
  edRender();
  edEl("edEntries").replaceChildren();
  edEl("edRoute").value = "";
  edStatus("");
}

function edStatus(text, error = false) {
  edEl("edStatus").textContent = text;
  edEl("edStatus").className = error ? "text-err" : "meta";
}

async function edList(prefix, reset = true) {
  const history = prefix === "eh", offsetKey = history ? "historyOffset" : "offset", totalKey = history ? "historyTotal" : "total";
  const pageSize = history ? 30 : 10;
  const versionKey = history ? "historyVersion" : "listVersion";
  if (reset) ed[offsetKey] = 0;
  const version = ++ed[versionKey];
  try {
    const params = new URLSearchParams({search: edEl(prefix+"Search").value, offset: ed[offsetKey], limit: pageSize});
    for (const [key, id] of [["client_profile", "FilterClient"], ["updated_from", "From"], ["updated_to", "To"]]) {
      const value = edEl(prefix+id).value; if (value) params.set(key,value);
    }
    if (history && edEl("ehFilterStatus").value) params.set("used", edEl("ehFilterStatus").value);
    const data = await api(`${edBase}?${params}`);
    if (version !== ed[versionKey]) return;
    ed[totalKey] = data.total;
    edEl(prefix+"Entries").innerHTML = data.entries.map(entry => `<tr>
      ${history ? `<td><input type="checkbox" data-doc-select="${entry.id}" aria-label="Select ${edEscape(entry.route_no)}"></td>` : ""}
      <td><button class="btn secondary sm" data-doc-open="${entry.id}">${edEscape(entry.route_no)}</button></td>
      <td>${entry.client_profile.toUpperCase()}</td><td>${entry.files.length} files</td><td><span class="ed-status-badge ${entry.status}">${entry.status === "used" ? "Used" : "Ready"}</span>${entry.used_invoice_no && entry.status === "used" ? `<br><small>${edEscape(entry.used_invoice_no)}</small>` : ""}</td>
      <td>${new Date(entry.updated_at).toLocaleString("en-IN", {timeZone:"Asia/Kolkata"})}</td><td>${entry.edited_by ? edEscape(entry.edited_by)+"<br>"+new Date(entry.edited_at).toLocaleString("en-IN", {timeZone:"Asia/Kolkata"}) : "-"}</td>
      <td><button class="btn secondary sm" data-doc-edit="${entry.id}">Edit</button> <button class="btn danger sm" data-doc-delete="${entry.id}">Delete</button></td></tr>`).join("") || `<tr><td colspan="8" class="meta">No matching saved entries.</td></tr>`;
    edEl(prefix+"Count").textContent = `Total saved records: ${data.total_all}. Matching records: ${data.total}. Showing up to ${pageSize}.`;
    edEl(prefix+"PageInfo").textContent = data.total ? `${ed[offsetKey]+1}-${Math.min(ed[offsetKey]+pageSize,data.total)} of ${data.total}` : "0 entries";
    edEl(prefix+"Previous").disabled = ed[offsetKey] === 0;
    edEl(prefix+"Next").disabled = ed[offsetKey]+pageSize >= data.total;
    if (history) { edEl("ehAll").checked = false; edUpdateSelection(); }
  } catch(error) { edStatus(error.message,true); if(history) edEl("ehCount").textContent=error.message; }
}
function edLoad(reset=true) { return edList("ed",reset); }
function edHistoryLoad(reset=true) { return edList("eh",reset); }
function edLocked() { return !!(ed.entry?.edit_protected && !ed.password); }
function edAskPassword(message) {
  return new Promise(resolve => {
    const modal=document.createElement('div'); modal.className='modal-backdrop';
    modal.innerHTML=`<div class="modal"><h3>${edEscape(message)}</h3><label>Masters edit password<input type="password" autocomplete="off" maxlength="128"></label><div style="margin-top:16px"><button class="btn" data-ok>Continue</button> <button class="btn secondary" data-cancel>Cancel</button></div></div>`;
    document.body.append(modal);
    const input=modal.querySelector('input');
    const finish=value=>{modal.remove();resolve(value);};
    modal.querySelector('[data-ok]').onclick=()=>finish(input.value);
    modal.querySelector('[data-cancel]').onclick=()=>finish(null);
    input.onkeydown=event=>{if(event.key==='Enter')finish(input.value);if(event.key==='Escape')finish(null);};
    input.focus();
  });
}
async function edUnlock() {
  const password = await edAskPassword("Unlock document editing");
  if (!password) return false;
  await api("/api/eee-taxi/rates/unlock", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password})});
  ed.password=password; edRender(); return true;
}
async function edFinish() {
  if(ed.busy || !ed.entry || edLocked()) return;
  edSetBusy(true);
  try {
    const route=edEl("edRoute").value.trim(), profile=edEl("edClient").value;
    if(route.toUpperCase() !== ed.entry.route_no || profile !== ed.entry.client_profile)
      ed.entry=await api(`${edBase}/${ed.entry.id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({route_no:route,client_profile:profile,edit_password:ed.password})});
    await api(`${edBase}/${ed.entry.id}/save`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({edit_password:ed.password})});
    ed.entry=null; ed.password=""; edEl("edRoute").value=""; edRender();
    edStatus("Saved. Enter the next DS no/Route No."); await edLoad(); await edHistoryLoad();
  } catch(error) { edStatus(error.message,true); }
  finally { edSetBusy(false); edEl("edRoute").focus(); }
}
async function edDelete(ids, history=false) {
  if(ed.busy || !ids.length) return;
  if(!confirm(`Delete ${ids.length} document entry/entries and their uploaded files? Invoice copies will remain.`)) return;
  const password=await edAskPassword("Delete document entries"); if(!password) return;
  edSetBusy(true);
  try {
    const result=await api(`${edBase}/bulk-delete`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ids,used_only:history && edEl("ehFilterStatus").value === "true",edit_password:password})});
    if(ed.entry && ids.includes(ed.entry.id)) { ed.entry=null; ed.password=""; edEl("edRoute").value=""; edRender(); }
    edStatus(`Deleted ${result.deleted} entries; freed ${Math.ceil(result.freed_bytes/1024)} KB of source uploads.`);
    await edLoad(); await edHistoryLoad();
    if(history) edEl("ehCount").textContent += ` Deleted ${result.deleted} entries; freed ${Math.ceil(result.freed_bytes/1024)} KB.`;
  } catch(error) { edStatus(error.message,true); if(history) edEl("ehCount").textContent=error.message; }
  finally { edSetBusy(false); }
}

function edRender() {
  const entry = ed.entry;
  edEl("edEditor").hidden = !entry;
  edEl("edRoute").disabled = ed.busy || edLocked();
  edEl("edClient").disabled = ed.busy || edLocked();
  if (!entry) return;
  edEl("edFinish").disabled = ed.busy || edLocked();
  edEl("edFinish").hidden = edLocked();
  edEl("edEdit").hidden = !edLocked();
  edEl("edRoute").disabled = ed.busy || edLocked();
  edEl("edClient").disabled = ed.busy || edLocked();
  edEl("edEntryTitle").textContent = `${entry.client_profile.toUpperCase()} · ${entry.route_no}`;
  edEl("edCompleteness").textContent = entry.missing.length
    ? `Missing: ${entry.missing.map(category => edNames[category]).join(", ")}`
    : "All document categories have attachments.";
  edEl("edCards").innerHTML = entry.categories.map(category => {
    const files = entry.files.filter(file => file.category === category);
    return `<section class="ed-doc-card"><div class="ed-card-title"><strong>${edNames[category]}</strong><span class="meta">${files.length} attached</span></div>
      <div class="ed-paste" tabindex="0" role="textbox" aria-label="Paste screenshot for ${edNames[category]}" data-doc-paste="${category}">Click here and press Ctrl+V to paste a screenshot</div>
      <label class="btn secondary sm ed-upload-label">Upload from PC<input type="file" data-doc-upload="${category}" accept="application/pdf,image/png,image/jpeg,image/webp" multiple ${ed.busy || edLocked() ? "disabled" : ""}></label>
      <ul class="ed-file-list">${files.map(file => `<li><span title="${edEscape(file.filename)}">${edEscape(file.filename)} <small class="meta">(${Math.ceil(file.size / 1024)} KB)</small></span><div><button class="btn sm" data-doc-view="${file.id}">View</button> <button class="btn secondary sm" data-doc-download="${file.id}">Download</button> <button class="btn danger sm" data-doc-remove="${file.id}" ${ed.busy || edLocked() ? "disabled" : ""}>Remove</button></div></li>`).join("") || '<li class="meta">No files added.</li>'}</ul></section>`;
  }).join("");
}

function edSetBusy(busy) {
  ed.busy = busy;
  edUpdateSelection();
  for (const id of ["edClient", "edRoute", "edSave", "edSearch", "edSearchButton", "edEdit"]) edEl(id).disabled = busy;
  edEl("edPrevious").disabled = busy || ed.offset === 0;
  edEl("edNext").disabled = busy || ed.offset + 10 >= ed.total;
  edEl("edRoute").disabled = busy || edLocked();
  edEl("edClient").disabled = busy || edLocked();
  edEl("edFinish").disabled = busy || edLocked();
  edEl("edCards").querySelectorAll('input,button[data-doc-remove]').forEach(input => input.disabled = busy || edLocked());
}

async function edCreate(event) {
  event.preventDefault();
  if (ed.busy) return;
  const route = edEl("edRoute").value.trim();
  if (!route) { edStatus("Enter the DS no/Route No first.", true); return; }
  edSetBusy(true);
  try {
    ed.password = "";
    ed.entry = await api(edBase, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({client_profile: edEl("edClient").value, route_no: route})});
    edEl("edRoute").value = ed.entry.route_no;
    edRender();
    edStatus("Attach documents, then click Save to finish. Past entries require Edit with password.");
    await edLoad();
  } catch (error) { edStatus(error.message, true); }
  finally { edSetBusy(false); }
}

async function edOpen(id, edit=false) {
  if (ed.busy) return;
  edSetBusy(true);
  try {
    ed.password="";
    ed.entry = await api(`${edBase}/${id}`);
    edEl("edRoute").value = ed.entry.route_no;
    edEl("edClient").value = ed.entry.client_profile;
    edRender(); edStatus("Saved entry opened. Use Edit with password to make changes.");
    if(edit && edLocked()) await edUnlock();
    switchTab("eee-documents");
    edEl("edEditor").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) { edStatus(error.message, true); }
  finally { edSetBusy(false); }
}

async function edUpload(category, files) {
  if (ed.busy || !ed.entry || edLocked() || !files.length) return;
  const entryId = ed.entry.id;
  edSetBusy(true);
  let saved = 0;
  try {
    for (const file of files) {
      if (file.size > 3 * 1024 * 1024) throw new Error(`${file.name}: each document must be 3 MB or smaller.`);
      const data = new FormData(); data.append("edit_password",ed.password); data.append("category", category); data.append("file", file, file.name);
      edStatus(`Saving ${edNames[category]}: ${file.name}…`);
      ed.entry = await api(`${edBase}/${entryId}/files`, {method: "POST", body: data});
      saved++;
    }
    edStatus(`${saved} ${edNames[category]} file${saved === 1 ? "" : "s"} saved.`);
  } catch (error) { edStatus(`${saved ? `${saved} file(s) saved. ` : ""}${error.message}`, true); }
  finally {
    edRender(); edSetBusy(false);
    edEl("edCards").querySelector(`[data-doc-paste="${category}"]`)?.focus();
    await edLoad(false);
  }
}

async function edRemove(fileId) {
  if (ed.busy || !ed.entry || edLocked()) return;
  if (!confirm("Remove this attachment from the document entry?")) return;
  edSetBusy(true);
  try {
    ed.entry = await api(`${edBase}/${ed.entry.id}/files/${fileId}`, {method: "DELETE",headers:{"Content-Type":"application/json"},body:JSON.stringify({edit_password:ed.password})});
    edRender(); edStatus("Attachment removed."); await edLoad(false);
  } catch (error) { edStatus(error.message, true); }
  finally { edSetBusy(false); }
}

function edClosePreview() {
  edEl("edPreview").classList.add("hidden");
  edEl("edPreviewBody").replaceChildren();
  if (ed.previewUrl) URL.revokeObjectURL(ed.previewUrl);
  ed.previewUrl = null;
}

async function edFile(fileId, preview) {
  if (!ed.entry) return;
  const file = ed.entry.files.find(file => file.id === fileId);
  const entryId = ed.entry.id;
  try {
    const response = await fetch(`${edBase}/${entryId}/files/${fileId}?inline=${preview}`, {headers: authHeaders()});
    if (!response.ok) throw new Error("Could not read document. Refresh the entry and try again.");
    const url = URL.createObjectURL(await response.blob());
    if (!preview) {
      const link = document.createElement("a"); link.href = url; link.download = file.filename;
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); return;
    }
    edClosePreview(); ed.previewUrl = url;
    edEl("edPreviewTitle").textContent = file.filename;
    const element = document.createElement(file.content_type === "application/pdf" ? "iframe" : "img");
    element.src = url;
    element.title = file.filename;
    if (element.tagName === "IMG") element.alt = file.filename;
    edEl("edPreviewBody").append(element);
    edEl("edPreview").classList.remove("hidden");
    edEl("edPreviewClose").focus();
  } catch (error) { edStatus(error.message, true); }
}

edEl("edForm").addEventListener("submit", edCreate);
edEl("edFinish").addEventListener("click",edFinish);
edEl("edEdit").addEventListener("click",async()=>{try {await edUnlock(); edSetBusy(false);} catch(error){edStatus(error.message,true);}});
for(const prefix of ["ed","eh"]) {
 for (const suffix of ["FilterClient","From","To",...(prefix==="eh"?["FilterStatus"]:[])]) edEl(prefix+suffix).addEventListener("change",()=>edList(prefix));
 edEl(prefix+"Clear").addEventListener("click",()=>{for(const suffix of ["Search","FilterClient","From","To",...(prefix==="eh"?["FilterStatus"]:[])])edEl(prefix+suffix).value="";edList(prefix);});
 edEl(prefix+"SearchButton").addEventListener("click",()=>edList(prefix));
 edEl(prefix+"Search").addEventListener("keydown",event=>{if(event.key==="Enter")edList(prefix);});
 edEl(prefix+"Previous").addEventListener("click",()=>{const key=prefix==="eh"?"historyOffset":"offset";ed[key]=Math.max(0,ed[key]-(prefix==="eh"?30:10));edList(prefix,false);});
 edEl(prefix+"Next").addEventListener("click",()=>{ed[prefix==="eh"?"historyOffset":"offset"]+=(prefix==="eh"?30:10);edList(prefix,false);});
 edEl(prefix+"Entries").addEventListener("click",event=>{const button=event.target.closest("button");if(!button)return;if(button.dataset.docOpen)edOpen(button.dataset.docOpen);if(button.dataset.docEdit)edOpen(button.dataset.docEdit,true);if(button.dataset.docDelete)edDelete([button.dataset.docDelete],prefix==="eh");});
}
function edUpdateSelection() {
 const boxes=Array.from(edEl("ehEntries").querySelectorAll("[data-doc-select]"));
 const selected=boxes.filter(box=>box.checked).length;
 edEl("ehSelected").textContent=`${selected} selected`;
 edEl("ehDelete").disabled=ed.busy || !selected;
 edEl("ehAll").disabled=!boxes.length;
 edEl("ehAll").checked=!!boxes.length && selected===boxes.length;
 edEl("ehAll").indeterminate=selected>0 && selected<boxes.length;
}
edEl("ehEntries").addEventListener("change",edUpdateSelection);
edEl("ehAll").addEventListener("change",()=>{edEl("ehEntries").querySelectorAll("[data-doc-select]").forEach(box=>box.checked=edEl("ehAll").checked);edUpdateSelection();});
edEl("ehDelete").addEventListener("click",()=>edDelete(Array.from(edEl("ehEntries").querySelectorAll("[data-doc-select]:checked")).map(box=>box.dataset.docSelect),true));
edEl("edCards").addEventListener("change", event => {
  if (event.target.matches("[data-doc-upload]")) edUpload(event.target.dataset.docUpload, Array.from(event.target.files));
});
edEl("edCards").addEventListener("paste", event => {
  const target = event.target.closest("[data-doc-paste]");
  if (!target) return;
  const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === "file").map(item => item.getAsFile()).filter(Boolean);
  event.preventDefault();
  if (ed.busy) { edStatus("Wait for the current upload to finish before pasting another screenshot."); return; }
  if (!files.length) { edStatus("Copy an image or take a screenshot, then paste it here with Ctrl+V.", true); return; }
  edUpload(target.dataset.docPaste, files.map((file, index) => new File([file], `screenshot-${Date.now()}-${index}.png`, {type: file.type})));
});
edEl("edCards").addEventListener("click", event => {
  const button = event.target.closest("button"); if (!button) return;
  if (button.dataset.docView) edFile(button.dataset.docView, true);
  if (button.dataset.docDownload) edFile(button.dataset.docDownload, false);
  if (button.dataset.docRemove) edRemove(button.dataset.docRemove);
});
edEl("edPreviewClose").addEventListener("click", edClosePreview);
edEl("edPreview").addEventListener("click", event => { if (event.target === edEl("edPreview")) edClosePreview(); });
document.addEventListener("keydown", event => { if (event.key === "Escape") edClosePreview(); });
