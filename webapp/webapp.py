from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import yaml
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

# ================= CONFIG =================

APP_TITLE = "HP Firmware Mirror Web API"

PRINTERS_YAML = os.getenv("PRINTERS_YAML", "/opt/hp-firmware-mirror/printers.yaml")
SERVICE_NAME = os.getenv("SERVICE_NAME", "hp-firmware-mirror.service")
FIRMWARE_ROOT = os.getenv("FIRMWARE_ROOT", "/opt/hp-firmware-mirror/data/firmware/hp")
JOURNAL_LINES_DEFAULT = int(os.getenv("JOURNAL_LINES_DEFAULT", "200"))

app = FastAPI(title=APP_TITLE)

# ================= HELPERS =================


def run_cmd(cmd: List[str]) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=e.output)


def load_printers() -> dict:
    if not os.path.exists(PRINTERS_YAML):
        raise HTTPException(status_code=404, detail=f"printers.yaml not found: {PRINTERS_YAML}")
    with open(PRINTERS_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="Unsupported printers.yaml format (expected dict)")
    if "printers" not in data or not isinstance(data["printers"], list):
        # Normalize to expected structure
        data["printers"] = []
    return data


def save_printers(data: dict) -> None:
    tmp = PRINTERS_YAML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp, PRINTERS_YAML)


def _safe_join(root: str, rel: str) -> Path:
    root_p = Path(root).resolve()
    rel = (rel or "").lstrip("/").lstrip("\\")
    p = (root_p / rel).resolve()
    if root_p not in p.parents and p != root_p:
        raise HTTPException(status_code=400, detail="Invalid path")
    return p


# ================= MODELS =================

class PrinterUpdate(BaseModel):
    enabled: bool


class PrinterCreate(BaseModel):
    name: str = Field(..., min_length=1)
    series_oid: int
    enabled: bool = True


class BulkUpdate(BaseModel):
    enabled: bool


class RunNowResponse(BaseModel):
    started: bool
    message: str


# ================= BASIC =================

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    out = run_cmd([
        "systemctl",
        "show",
        SERVICE_NAME,
        "--property=ActiveState,SubState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp",
        "--no-page",
    ])
    # systemctl show returns Key=Value
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


@app.post("/api/jobs/run", response_model=RunNowResponse)
def run_now():
    # Requires sudoers rule for the service user running the webapp, e.g. hpweb:
    # hpweb ALL=NOPASSWD: /bin/systemctl start hp-firmware-mirror.service
    run_cmd(["sudo", "systemctl", "start", SERVICE_NAME])
    return RunNowResponse(started=True, message="Job started")


@app.get("/api/logs")
def logs(lines: int = JOURNAL_LINES_DEFAULT):
    lines = max(10, min(lines, 2000))
    out = run_cmd([
        "journalctl",
        "-u",
        SERVICE_NAME,
        "--no-pager",
        "-n",
        str(lines),
        "-o",
        "short-iso",
    ])
    return {"lines": out.splitlines()}


# ================= PRINTERS =================

@app.get("/api/printers")
def list_printers(search: Optional[str] = None):
    data = load_printers()
    printers = data.get("printers", [])

    items = []
    for p in printers:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "")
        if search and search.lower() not in str(name).lower():
            continue
        items.append(
            {
                "name": name,
                "series_oid": p.get("series_oid"),
                "enabled": bool(p.get("enabled")),
            }
        )

    # Stable sort by name then oid
    items.sort(key=lambda x: (str(x.get("name", "")).lower(), int(x.get("series_oid") or 0)))
    return {"count": len(items), "items": items}


@app.post("/api/printers")
def add_printer(newp: PrinterCreate):
    data = load_printers()
    printers = data["printers"]

    name = newp.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    # Prevent duplicate series_oid
    for p in printers:
        if isinstance(p, dict) and p.get("series_oid") == int(newp.series_oid):
            raise HTTPException(status_code=409, detail=f"series_oid already exists: {newp.series_oid}")

    printers.append(
        {
            "name": name,
            "series_oid": int(newp.series_oid),
            "enabled": bool(newp.enabled),
        }
    )

    save_printers(data)
    return {"ok": True, "added": {"name": name, "series_oid": int(newp.series_oid), "enabled": bool(newp.enabled)}}


@app.patch("/api/printers/{series_oid}")
def update_printer(series_oid: int, patch: PrinterUpdate):
    data = load_printers()
    for p in data.get("printers", []):
        if isinstance(p, dict) and p.get("series_oid") == series_oid:
            p["enabled"] = bool(patch.enabled)
            save_printers(data)
            return {"ok": True}

    raise HTTPException(status_code=404, detail="Printer not found")


@app.post("/api/printers/bulk")
def bulk_update(bulk: BulkUpdate):
    data = load_printers()
    count = 0
    for p in data.get("printers", []):
        if isinstance(p, dict):
            p["enabled"] = bool(bulk.enabled)
            count += 1
    save_printers(data)
    return {"updated": count, "enabled": bool(bulk.enabled)}


# ================= FILE BROWSER =================

@app.get("/api/browse")
def browse(path: str = ""):
    root = Path(FIRMWARE_ROOT).resolve()
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"FIRMWARE_ROOT not found: {root}")

    p = _safe_join(str(root), path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    rel = "" if p == root else str(p.relative_to(root))

    dirs: List[Dict[str, Any]] = []
    files: List[Dict[str, Any]] = []

    for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if c.is_dir():
            dirs.append({"name": c.name, "rel": str(c.relative_to(root))})
        elif c.is_file():
            st = c.stat()
            rel_file = str(c.relative_to(root))
            files.append(
                {
                    "name": c.name,
                    "rel": rel_file,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                    "download_url": f"/api/files/download?path={quote(rel_file)}",
                }
            )

    return {"root": str(root), "path": rel, "dirs": dirs, "files": files}


@app.get("/api/files/download")
def download_file(path: str):
    root = Path(FIRMWARE_ROOT).resolve()
    p = _safe_join(str(root), path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(p), filename=p.name, media_type="application/octet-stream")


# ================= UI =================

@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>HP Firmware Mirror</title>
  <style>
    body { font-family: system-ui,-apple-system,Segoe UI,Roboto; max-width: 900px; margin: 40px auto; }
    a { text-decoration: none; }
  </style>
</head>
<body>
  <h1>HP Firmware Mirror</h1>
  <ul>
    <li><a href="/ui/printers">Printers</a></li>
    <li><a href="/ui/files">Firmware Files</a></li>
    <li><a href="/docs">API Docs</a></li>
    <li><a href="/api/status">Status (JSON)</a></li>
  </ul>
</body>
</html>
"""


@app.get("/ui/printers", response_class=HTMLResponse)
def ui_printers():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Printers</title>
  <style>
    body { font-family: system-ui,-apple-system,Segoe UI,Roboto; max-width: 1100px; margin: 40px auto; }
    .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    input { padding:6px; }
    button { padding:6px 10px; cursor:pointer; }
    table { width:100%; border-collapse: collapse; margin-top: 14px; }
    th, td { border-bottom: 1px solid #ddd; padding: 10px; text-align: left; }
    .muted { color:#666; font-size:12px; }
  </style>
</head>
<body>
  <div class="row">
    <h1 style="margin:0;">Printers</h1>
    <span class="muted" id="meta"></span>
    <a href="/" style="margin-left:auto;">Home</a>
  </div>

  <div class="row" style="margin-top:12px;">
    <input id="q" placeholder="Search name (e.g. E586)" style="min-width:260px;">
    <button onclick="load()">Search</button>
    <button onclick="bulk(true)">Enable all</button>
    <button onclick="bulk(false)">Disable all</button>
    <button onclick="runNow()">Run job now</button>
  </div>

  <hr>

  <h3 style="margin-bottom:8px;">Add printer</h3>
  <div class="row">
    <input id="new_name" placeholder='Name (e.g. "HP PageWide E586")' style="min-width:360px;">
    <input id="new_oid" placeholder="series_oid (e.g. 7835692)" style="min-width:220px;">
    <label style="display:flex; align-items:center; gap:6px;">
      <input type="checkbox" id="new_enabled" checked> enabled
    </label>
    <button onclick="addPrinter()">Add</button>
  </div>
  <p class="muted">series_oid måste vara unik.</p>

  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th style="width:140px;">OID</th>
        <th style="width:110px;">Enabled</th>
        <th style="width:220px;">Action</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

<script>
async function load(){
  const q = document.getElementById("q").value.trim();
  const url = q ? ("/api/printers?search=" + encodeURIComponent(q)) : "/api/printers";
  const r = await fetch(url);
  if(!r.ok) return alert(await r.text());
  const d = await r.json();

  document.getElementById("meta").textContent = "Count: " + d.count;

  const tbody = document.getElementById("rows");
  tbody.innerHTML = "";
  for(const p of d.items){
    tbody.innerHTML += `
      <tr>
        <td><b>${p.name}</b></td>
        <td>${p.series_oid}</td>
        <td>${p.enabled}</td>
        <td>
          <button onclick="setEnabled(${p.series_oid}, true)">On</button>
          <button onclick="setEnabled(${p.series_oid}, false)">Off</button>
        </td>
      </tr>`;
  }
}

async function setEnabled(id,val){
  const r = await fetch('/api/printers/' + id, {
    method:'PATCH',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled: val })
  });
  if(!r.ok) return alert(await r.text());
  load();
}

async function bulk(val){
  if(!confirm('Are you sure you want to set enabled=' + val + ' for ALL printers?')) return;
  const r = await fetch('/api/printers/bulk', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled: val })
  });
  if(!r.ok) return alert(await r.text());
  load();
}

async function addPrinter(){
  const name = document.getElementById("new_name").value.trim();
  const oidStr = document.getElementById("new_oid").value.trim();
  const enabled = document.getElementById("new_enabled").checked;

  if(!name) return alert("Name required");
  if(!oidStr || isNaN(Number(oidStr))) return alert("series_oid must be a number");

  const r = await fetch('/api/printers', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ name: name, series_oid: Number(oidStr), enabled: enabled })
  });

  if(!r.ok) return alert(await r.text());

  document.getElementById("new_name").value = "";
  document.getElementById("new_oid").value = "";
  document.getElementById("new_enabled").checked = true;

  load();
}

async function runNow(){
  const r = await fetch('/api/jobs/run', { method:'POST' });
  if(!r.ok) return alert(await r.text());
  alert("Job triggered.");
}

load();
</script>
</body>
</html>
"""


@app.get("/ui/files", response_class=HTMLResponse)
def ui_files():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Firmware Files</title>
  <style>
    body { font-family: system-ui,-apple-system,Segoe UI,Roboto; max-width: 1100px; margin: 40px auto; }
    table { width:100%; border-collapse: collapse; margin-top: 14px; }
    th, td { border-bottom: 1px solid #ddd; padding: 10px; text-align: left; }
    a { text-decoration: none; }
    .muted { color:#666; font-size:12px; }
    .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    button { padding:6px 10px; cursor:pointer; }
  </style>
</head>
<body>
  <div class="row">
    <h1 style="margin:0;">Firmware Files</h1>
    <span class="muted" id="meta"></span>
    <a href="/" style="margin-left:auto;">Home</a>
  </div>

  <div class="row" style="margin-top:10px;">
    <button onclick="up()">Up</button>
    <div class="muted" id="path"></div>
  </div>

  <table>
    <tbody id="rows"></tbody>
  </table>

<script>
let cur = '';

async function nav(p=''){
  cur = p;
  const r = await fetch('/api/browse?path=' + encodeURIComponent(p));
  if(!r.ok) return alert(await r.text());
  const d = await r.json();

  document.getElementById("meta").textContent = "Root: " + d.root;
  document.getElementById("path").textContent = "/" + (d.path || "");

  const rows = document.getElementById("rows");
  rows.innerHTML = "";

  if(p){
    rows.innerHTML += `<tr><td>📁 <a href="#" onclick="up();return false;">[..]</a></td></tr>`;
  }

  for(const f of d.dirs){
    rows.innerHTML += `<tr><td>📁 <a href="#" onclick="nav('${f.rel}');return false;">${f.name}</a></td></tr>`;
  }

  for(const f of d.files){
    rows.innerHTML += `<tr><td>📄 ${f.name} — <a href="${f.download_url}">Download</a></td></tr>`;
  }
}

function up(){
  if(!cur) return;
  const p = cur.split('/').filter(Boolean);
  p.pop();
  nav(p.join('/'));
}

nav();
</script>
</body>
</html>
"""