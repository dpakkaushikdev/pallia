const ed = {entry: null, busy: false, offset: 0, total: 0, listVersion: 0, previewUrl: null};
const edNames = {invoice: "Invoice", ds: "DS", parking: "Parking", toll_mcd: "Toll / MCD", gps: "GPS", email_screenshot: "Email Screenshot"};
const edBase = "/api/eee-taxi/documents";
const edEl = id => document.getElementById(id);
const edEscape = value => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));

function edReset() {
  ed.listVersion++;
  ed.entry = null;
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

async function edLoad(reset = true) {
  if (reset) ed.offset = 0;
  const version = ++ed.listVersion;
  try {
    const params = new URLSearchParams({client_profile: edEl("edClient").value, search: edEl("edSearch").value, offset: ed.offset});
    const data = await api(`${edBase}?${params}`);
    if (version !== ed.listVersion) return;
    ed.total = data.total;
    edEl("edEntries").innerHTML = data.entries.length ? data.entries.map(entry =>
      `<tr><td><button class="btn secondary sm" data-doc-open="${entry.id}">${edEscape(entry.route_no)}</button></td><td>${entry.client_profile.toUpperCase()}</td><td>${entry.files.length} file${entry.files.length === 1 ? "" : "s"}</td><td>${entry.missing.length ? `${entry.categories.length - entry.missing.length}/${entry.categories.length} categories` : '<span class="text-ok">Complete</span>'}</td><td>${new Date(entry.updated_at).toLocaleString("en-IN")}</td></tr>`
    ).join("") : '<tr><td colspan="5" class="meta">No document entries yet. Enter a DS/Route number above to begin.</td></tr>';
    edEl("edPageInfo").textContent = ed.total ? `${ed.offset + 1}–${Math.min(ed.offset + 50, ed.total)} of ${ed.total}` : "0 entries";
    edEl("edPrevious").disabled = ed.busy || ed.offset === 0;
    edEl("edNext").disabled = ed.busy || ed.offset + 50 >= ed.total;
  } catch (error) { if (version === ed.listVersion) edStatus(error.message, true); }
}

function edRender() {
  const entry = ed.entry;
  edEl("edEditor").hidden = !entry;
  if (!entry) return;
  edEl("edEntryTitle").textContent = `${entry.client_profile.toUpperCase()} · ${entry.route_no}`;
  edEl("edCompleteness").textContent = entry.missing.length
    ? `Missing: ${entry.missing.map(category => edNames[category]).join(", ")}`
    : "All document categories have attachments.";
  edEl("edCards").innerHTML = entry.categories.map(category => {
    const files = entry.files.filter(file => file.category === category);
    return `<section class="ed-doc-card"><div class="ed-card-title"><strong>${edNames[category]}</strong><span class="meta">${files.length} attached</span></div>
      <div class="ed-paste" tabindex="0" role="textbox" aria-label="Paste screenshot for ${edNames[category]}" data-doc-paste="${category}">Click here and press Ctrl+V to paste a screenshot</div>
      <label class="btn secondary sm ed-upload-label">Upload from PC<input type="file" data-doc-upload="${category}" accept="application/pdf,image/png,image/jpeg,image/webp" multiple ${ed.busy ? "disabled" : ""}></label>
      <ul class="ed-file-list">${files.map(file => `<li><span title="${edEscape(file.filename)}">${edEscape(file.filename)} <small class="meta">(${Math.ceil(file.size / 1024)} KB)</small></span><div><button class="btn sm" data-doc-view="${file.id}">View</button> <button class="btn secondary sm" data-doc-download="${file.id}">Download</button> <button class="btn danger sm" data-doc-remove="${file.id}" ${ed.busy ? "disabled" : ""}>Remove</button></div></li>`).join("") || '<li class="meta">No files added.</li>'}</ul></section>`;
  }).join("");
}

function edSetBusy(busy) {
  ed.busy = busy;
  for (const id of ["edClient", "edRoute", "edSave", "edSearch", "edSearchButton"]) edEl(id).disabled = busy;
  edEl("edPrevious").disabled = busy || ed.offset === 0;
  edEl("edNext").disabled = busy || ed.offset + 50 >= ed.total;
  edEl("edCards").querySelectorAll('input,button[data-doc-remove]').forEach(input => input.disabled = busy);
}

async function edCreate(event) {
  event.preventDefault();
  if (ed.busy) return;
  const route = edEl("edRoute").value.trim();
  if (!route) { edStatus("Enter the DS no/Route No first.", true); return; }
  edSetBusy(true);
  try {
    ed.entry = await api(edBase, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({client_profile: edEl("edClient").value, route_no: route})});
    edEl("edRoute").value = ed.entry.route_no;
    edRender();
    edStatus("Entry saved. Upload documents or paste screenshots into their categories.");
    await edLoad();
  } catch (error) { edStatus(error.message, true); }
  finally { edSetBusy(false); }
}

async function edOpen(id) {
  if (ed.busy) return;
  edSetBusy(true);
  try {
    ed.entry = await api(`${edBase}/${id}`);
    edEl("edRoute").value = ed.entry.route_no;
    edEl("edClient").value = ed.entry.client_profile;
    edRender(); edStatus("Saved entry opened. Attachments save as you add them.");
    edEl("edEditor").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) { edStatus(error.message, true); }
  finally { edSetBusy(false); }
}

async function edUpload(category, files) {
  if (ed.busy || !ed.entry || !files.length) return;
  const entryId = ed.entry.id;
  edSetBusy(true);
  let saved = 0;
  try {
    for (const file of files) {
      if (file.size > 3 * 1024 * 1024) throw new Error(`${file.name}: each document must be 3 MB or smaller.`);
      const data = new FormData(); data.append("category", category); data.append("file", file, file.name);
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
  if (ed.busy || !ed.entry) return;
  if (!confirm("Remove this attachment from the document entry?")) return;
  edSetBusy(true);
  try {
    ed.entry = await api(`${edBase}/${ed.entry.id}/files/${fileId}`, {method: "DELETE"});
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
edEl("edClient").addEventListener("change", () => { ed.entry = null; edRender(); edStatus(""); edLoad(); });
edEl("edSearchButton").addEventListener("click", () => edLoad());
edEl("edSearch").addEventListener("keydown", event => { if (event.key === "Enter") edLoad(); });
edEl("edPrevious").addEventListener("click", () => { ed.offset = Math.max(0, ed.offset - 50); edLoad(false); });
edEl("edNext").addEventListener("click", () => { ed.offset += 50; edLoad(false); });
edEl("edEntries").addEventListener("click", event => { const button = event.target.closest("[data-doc-open]"); if (button) edOpen(button.dataset.docOpen); });
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
