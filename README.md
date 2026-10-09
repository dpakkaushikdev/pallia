# Automation Bot for Document Data Entry

A Python-first FastAPI application that:

1. Accepts an uploaded document image
2. Runs OCR (EasyOCR) and extracts structured fields
3. Shows extracted fields to the user for confirm / edit / reject
4. After confirmation, queues a Playwright job that logs into the Pallia
   TMS portal and submits the record
5. Handles multiple documents FIFO without dropping queued jobs across
   restarts

The codebase is structured so it can later swap SQLite -> PostgreSQL
and asyncio-queue -> Celery+Redis with no API changes.

---

## Project layout

```
.
+-- app/
|   +-- main.py                 FastAPI entrypoint + lifespan
|   +-- config.py               Pydantic settings (loads .env)
|   +-- database.py             SQLAlchemy engine / session
|   +-- models.py               Job ORM model (8 status states)
|   +-- schemas.py              Pydantic request/response models
|   +-- api/
|   |   +-- upload.py           POST /api/upload (OCR + extraction)
|   |   +-- jobs.py             GET/POST /api/jobs/...
|   +-- services/
|   |   +-- ocr.py              EasyOCR wrapper (lazy-loaded)
|   |   +-- extraction.py       Regex field extractor
|   |   +-- queue.py            Restart-safe FIFO queue
|   |   +-- tms_automation.py   Playwright skeleton (TODO selectors)
|   +-- workers/
|   |   +-- job_worker.py       Background async worker(s)
|   +-- utils/
|   |   +-- logging_config.py   loguru setup
|   +-- static/
|       +-- index.html          Single-page UI
+-- tests/
|   +-- test_extraction.py
+-- requirements.txt
+-- .env.example
+-- run.py                       Dev entrypoint
```

---

## Run it locally (Windows / Mac / Linux)

### 0. Prerequisites
- Python 3.10 or 3.11 (EasyOCR + numpy wheels are best on these)
- ~2 GB free disk for the EasyOCR model
- Git (optional)

### 1. Set up a virtual environment
```powershell
cd "C:\Users\Admin\Documents\Claude\Projects\Biling Automation Bot"
python -m venv .venv
.\.venv\Scripts\activate           # PowerShell on Windows
# or:  source .venv/bin/activate   # macOS / Linux
```

### 2. Install Python dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```
The first install pulls torch (~700 MB) because EasyOCR depends on it.
This is the only heavy download.

### 3. Install Playwright browsers
```bash
python -m playwright install chromium
```

### 4. Create your `.env`
```bash
copy .env.example .env             # Windows
# or:  cp .env.example .env        # macOS / Linux
```
Open `.env` and at minimum set:
- `TMS_USERNAME` and `TMS_PASSWORD` (only used once selectors are filled in)
- Leave the rest as defaults for now

### 5. Run the app
```bash
python run.py
```

You should see:
```
INFO     Logging initialised (level=INFO)
INFO     Starting Automation Bot v0.1.0
INFO     Loading EasyOCR reader ...   (first time only, ~30s)
INFO     EasyOCR reader ready.
INFO     Worker #1 started.
INFO     Uvicorn running on http://0.0.0.0:8000
```

Open http://localhost:8000/ in your browser, drop an image, and you'll
see the extracted fields render in the UI.

### 6. Try the OCR + extraction without the portal
After uploading, the job will sit in **awaiting_confirmation**. When
you click **Confirm**, the worker will try to run Playwright. Because
the TMS selectors are still stubbed, the job will move to **failed**
with a clear `NotImplementedError` in the error_message — this is
expected at this stage.

---

## EEE-Taxi batches on Vercel — the browser drives the work

Two constraints shape this flow, and both come from running on Vercel:

- **A serverless instance is suspended as soon as it sends the response.** Work
  started in a background thread stops getting CPU, so the batch stalls with no
  error. Generation therefore happens one invoice per HTTP request.
- **The server cannot reach a USB token** plugged into someone's desk, so the
  DSC signature has to be produced on that PC.

The browser orchestrates both steps:

1. `POST /api/eee-taxi/batch` records the batch and one row per invoice
   (`pending`) and stores the inputs needed to rebuild any row later: the CSV,
   the re-uploaded calculated CSV, the chosen card-fare rows and a snapshot of
   the rate card. Nothing is generated yet.
2. `POST /api/eee-taxi/batch/{id}/generate-next` generates **one** invoice and
   returns how many remain. The browser calls it in a loop. Each call rebuilds
   its row from the stored inputs, because consecutive requests may land on
   different instances and nothing survives in memory between them.
   In **USB DSC** mode the invoice becomes `awaiting_signature`; in **Dummy
   (Test)** mode it is stamped server-side and becomes `done`.
3. For each awaiting invoice the browser downloads the unsigned PDF, posts it to
   **PalliaSignHelper.exe** on `http://127.0.0.1:7777/sign` with the PIN and the
   signature box, and uploads the result to
   `POST /api/eee-taxi/batch/{id}/invoice/{inv}/signed`.
4. The server stores the signed bytes, marks the invoice `done`, and flips the
   batch to `completed` / `partial` once nothing is left to sign.

The PIN stays on the user's PC and the server never sees it. PDF bytes are
kept in the database rather than on disk, because a serverless disk is
per-instance and ephemeral; the unsigned copy is dropped once the signed one
arrives.

Because the browser is the driver, **the tab must stay open** until the batch
finishes. Closing it mid-run leaves the remaining invoices `pending` or
`awaiting_signature`.

### EEE-Taxi documents

Open **EEE-Taxi > Document**, enter **DS no/Route No**, select EY or PWC,
and click **Open / Create entry**. Existing entries with the same client and
route number reopen without creating duplicates.

EY entries have Invoice, DS, Parking, Toll / MCD, GPS and Email Screenshot.
PWC entries have the same categories except Email Screenshot. Each category
accepts multiple PDFs, PNG, JPG or WebP images (up to 3 MB per file). Click a
category's paste area and press Ctrl+V to attach a clipboard screenshot, or
choose **Upload from PC**. Attachments save immediately in the database and
can be viewed or downloaded when the entry is reopened. Finish with **Save**
to make an entry eligible for invoice matching. Past entries require the Masters
edit password before changing or removing attachments.
Access follows the EEE-Taxi permission, including attachment downloads.

Document entries require **Save** after uploading; saving clears the form for
another DS/Route entry. The saved list shows ten records per page, total counts,
and filters for DS/Route, client and updated date (India time). Past-entry edits
and deletion require the shared Masters edit password. The latest editor/date
and a separate audit log are stored; deleting source files preserves the audit.

Invoice generation matches saved documents by client and normalized DS/Route
number, copies the files into the invoice record, and marks the entry **used**.
The two billing ZIP downloads contain either invoices only or invoices plus
supporting files grouped by DS/Route and invoice number. Supporting files remain
separate from signed PDFs, preserving the original DSC signature. **Document
History** lists used entries for password-protected bulk deletion of source
uploads; completed invoice attachment copies remain available. Saving later
edits makes the changed document revision ready for another attachment.

### Installing the signing helper on a PC

```powershell
cd signing_helper
build.bat                       # produces dist\PalliaSignHelper.exe
copy dist\PalliaSignHelper.exe %LOCALAPPDATA%\PalliaSignHelper\
%LOCALAPPDATA%\PalliaSignHelper\PalliaSignHelper.exe
```

The helper sits in the system tray and registers itself to start with Windows.
It needs the token driver (`C:\Windows\System32\CryptoIDA_pkcs11.dll`) that the
DSC vendor's software installs. Helper 1.4.0 or newer is required. It supports
invoice `sig_box` signing and the Billing dashboard's ZIP workflow. The page
first inspects the ZIP and lists its invoice PDFs. After the user starts
signing, it shows per-invoice progress and downloads the signed archive when
all PDFs are done. Other files and folders are preserved, and the source ZIP
remains unchanged. ZIPs are limited to 100 MB compressed, 200 MB uncompressed
and 500 entries. The helper trims invalid non-PDF bytes after a valid PDF EOF
marker in its temporary signing copy; originals remain unchanged. Rebuild and
copy the helper after updating this repository; the dashboard checks its
version before inspection.

Accounts ZIP signing requires helper **1.10.0 or newer**. Accounts reviews
each PDF for a single page and both Pallia footer lines, then places the
signature between those lines. Each uploaded ZIP produces a downloadable
ZIP containing the signed PDFs, unchanged unsigned PDFs and other files,
and `accounts-signing-summary.txt` with unsigned filenames and reasons.
The completion screen also shows signed/unsigned counts and reasons.
Accounts accepts up to 50 PDFs and 100 MB of PDF contents per review.
After replacing the helper executable, stop the old tray helper and start
the new executable so the browser uses the updated version.

Helper **1.11.0** also supports compact Accounts footers with at least
20 points between the two lines. DSC selection uses a current signing
certificate with a matching private key, excluding issuer certificates,
expired certificates and encryption-only certificates. If multiple signing
identities remain, it reports an explicit selection error instead of guessing.
The PIN forms offer a connected DSC device selector, remembered separately
for EEE, Pallia Billing and Pallia Accounts on this browser. Select the intended
device and refresh the list after swapping tokens. A disconnected saved
device never falls back to another token or sends its PIN to another device.

Helper **1.12.0** uses a plain black signature appearance with the signer name
in capitals on two whole-word lines (for example, RAMASHANKAR above SHARMA),
without the decorative red initial. Certificate details stay in the right column.

Helper **1.13.0** also supports Mahindra landscape invoices with a left-side
Pallia footer. It locates both footer lines per PDF and keeps the signature
on their side, following changing vertical positions. Billing ZIP signing
rejects missing or cramped footer anchors before opening the DSC session.
Tata and Accounts right-side footer placement remains supported.

Helper **1.14.0** names connected signing certificates and enforces the
intended signer: EEE = JISHNU NANDA, Pallia Billing (Tata and Mahindra) = VINOD,
and Pallia Accounts = RAMASHANKAR SHARMA. The public certificate is checked
before attempting a PIN, and the actual signing certificate is checked again
before signing. Other-flow devices are disabled in each DSC selector. Token
refresh queries only currently occupied reader slots, avoiding empty-slot
errors after a USB device is unplugged.

Helper **1.15.0** refreshes the CryptoID driver when listing or selecting a
token, so USB swaps replace the previous cached signer. Refresh and signing
share a lock: the driver is never reset while a PDF is being signed.

---

## Next step: fill in the real TMS selectors

Once the local pipeline is verified end-to-end, we'll record the real
portal flow:

```bash
python -m playwright codegen https://pallia.tmslive.in/
```

Playwright launches Chromium, you log in and walk through one entry,
and it prints the exact `locator(...)` calls. Paste those into the
TODO blocks in `app/services/tms_automation.py`:
- `_login(page)`
- `_navigate_to_form(page)`
- `_fill_form(page, fields)`
- `_save(page)`

Once filled in, set `TMS_HEADLESS=false` and `TMS_SLOW_MO_MS=300` in
`.env` while debugging so you can watch the browser drive itself; then
flip back to `TMS_HEADLESS=true` once it's working.

---

## Run the test
```bash
pytest -q
```
Currently covers field extraction — pure regex, no heavy dependencies.

---

## API reference (auto-generated)

With the server running, open http://localhost:8000/docs for an
interactive Swagger UI.

Key endpoints:
- `POST /api/upload` — multipart form, field `file`. Runs OCR
  synchronously and returns the new Job.
- `GET  /api/jobs` — list recent jobs.
- `GET  /api/jobs/{id}` — single job detail.
- `POST /api/jobs/{id}/confirm` — body `{"fields": {...}}`. Optionally
  edit fields before queueing; missing body uses extracted fields as-is.
- `POST /api/jobs/{id}/reject` — body `{"reason": "..."}`.

---

## Architecture (at a glance)

```
   User uploads image
          v
   /api/upload  ----- OCR (EasyOCR) ----- field extraction
          v
   Job row: status = awaiting_confirmation
          v
   User clicks Confirm in UI
          v
   /api/jobs/{id}/confirm  ---->  asyncio.Queue (FIFO)
                                       v
                              background worker(s)
                                       v
                         Playwright -> Pallia TMS portal
                                       v
                          Job row: success | failed
```

Key properties:
- The user can keep uploading while a job is processing — uploads are
  fully synchronous (OCR is fast), and only the portal submission goes
  through the worker.
- Worker concurrency is configurable (`WORKER_CONCURRENCY` in `.env`).
  Default `1` = strict FIFO.
- On restart, any job that was `confirmed`, `in_queue`, or `processing`
  is re-enqueued automatically.

---

## Future (after local verification)

These are intentionally deferred:
- **Docker / docker-compose** — once the local flow works end-to-end
  we'll add a `Dockerfile` so it runs the same on the GCP VM.
- **GCP VM deployment** — `gcloud compute instances create ...` + systemd
  unit for headless run.
- **Postgres + Celery + Redis** — only needed once multi-instance / true
  durability is required.
- **LLM-based field extraction** — `extraction.py` is intentionally a
  single function so we can plug in an LLM call later without touching
  the API layer.

---

## Configuration reference

All settings come from environment variables (see `.env.example`):

| Variable | Default | Notes |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | Bind address |
| `APP_PORT` | `8000` | Bind port |
| `LOG_LEVEL` | `INFO` | DEBUG for verbose |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Postgres later |
| `UPLOAD_DIR` | `./data/uploads` | |
| `LOG_DIR` | `./logs` | Rotated daily |
| `OCR_LANGUAGES` | `en` | Comma-list, e.g. `en,hi` |
| `OCR_USE_GPU` | `false` | EasyOCR CUDA toggle |
| `TMS_BASE_URL` | `https://pallia.tmslive.in/` | |
| `TMS_USERNAME` | `changeme` | |
| `TMS_PASSWORD` | `changeme` | |
| `TMS_HEADLESS` | `true` | `false` while debugging |
| `TMS_SLOW_MO_MS` | `0` | Add e.g. 300 to watch the browser |
| `WORKER_CONCURRENCY` | `1` | FIFO if 1 |
| `JOB_MAX_RETRIES` | `2` | Per portal-side failure |
