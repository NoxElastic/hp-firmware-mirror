#!/usr/bin/env python3
import argparse
import datetime as dt
import shutil
import time
from pathlib import Path

import requests
import yaml

from db import connect, ensure_printer, has_url, insert_firmware
from download import download_stream
from extract import safe_extract_zip, remove_txt_files
from hp_swd_api import discover_firmware_urls_swd
from archive import snapshot_current_to_old
from hp import pick_best_link, firmware_version_from_url

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours


def safe_folder(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name).strip("_")


def load_printers(config_path: Path) -> list[dict]:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return [p for p in data.get("printers", []) if p.get("enabled", True)]


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "hp-firmware-mirror/1.0",
        "Accept": "application/json, text/plain, */*",
        "Connection": "keep-alive",
    })
    return s


def clear_current_dir(current_dir: Path) -> None:
    if not current_dir.exists():
        return

    for item in current_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink(missing_ok=True)


def check_one(printer: dict, session: requests.Session, db_con, base_data: Path) -> None:
    name = printer.get("name", "UNKNOWN")
    series_oid = printer.get("series_oid")
    if series_oid is None:
        print(f"[WARN] {name}: missing series_oid in printers.yaml")
        return

    printer_id = ensure_printer(db_con, name)

    # Discover firmware candidates via SWD API
    try:
        urls = discover_firmware_urls_swd(session, series_oid=int(series_oid), cc="se", lc="sv", debug=False)
    except Exception as e:
        print(f"[WARN] {name}: SWD discovery failed ({type(e).__name__}: {e})")
        return

    chosen_url = pick_best_link(urls)
    if not chosen_url:
        print(f"[WARN] {name}: SWD API returned no firmware candidates")
        return

    if has_url(db_con, printer_id, chosen_url):
        print(f"[OK]   {name}: no change (latest link already stored)")
        return

    version = firmware_version_from_url(chosen_url) or "unknown"
    printer_dir = base_data / "firmware" / "hp" / safe_folder(name)
    current_dir = printer_dir / "current"
    current_dir.mkdir(parents=True, exist_ok=True)

    # Always start with a clean current/ before downloading a new version
    clear_current_dir(current_dir)

    # Download to current/firmware.zip
    try:
        zip_path, digest, size = download_stream(
            chosen_url,
            current_dir,
            session,
            timeout=(10, 600),
            filename="firmware.zip",
        )
    except Exception as e:
        print(f"[WARN] {name}: download failed ({type(e).__name__}: {e})")
        return

    # Extract and remove .txt
    try:
        safe_extract_zip(zip_path, current_dir)
        removed = remove_txt_files(current_dir)
        zip_path.unlink(missing_ok=True)
    except Exception as e:
        print(f"[WARN] {name}: extract/cleanup failed ({type(e).__name__}: {e})")
        return

    # Save a versioned snapshot immediately after successful extraction
    archived_to = snapshot_current_to_old(printer_dir, version)
    if archived_to:
        print(f"[INFO] {name}: snapshotted extracted firmware -> {archived_to}")

    # Mark what version is currently active in current/
    (current_dir / "VERSION").write_text(version, encoding="utf-8")

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    insert_firmware(
        db_con,
        printer_id=printer_id,
        discovered_at=now,
        download_url=chosen_url,
        filename="",
        sha256=digest,
        filesize=size,
        stored_zip_path="",
        stored_extract_path=str(current_dir),
    )

    print(f"[NEW]  {name}: downloaded new firmware")
    print(f"       url: {chosen_url}")
    print(f"       zip: {zip_path} ({size} bytes) sha256={digest[:12]}...")
    print(f"       extracted: {current_dir} (removed {removed} .txt file(s))")


def run_once(config: Path, db_path: Path, data_dir: Path, rate_limit_sec: float = 1.0) -> None:
    start = time.monotonic()

    printers = load_printers(config)
    con = connect(db_path)
    session = build_session()

    for i, p in enumerate(printers):
        check_one(p, session, con, data_dir)
        if i != len(printers) - 1:
            time.sleep(rate_limit_sec)

    elapsed = time.monotonic() - start

    hours, rem = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(rem, 60)

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    print(f"[INFO] run completed in {' '.join(parts)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="printers.yaml")
    ap.add_argument("--db", default="data/firmware.db")
    ap.add_argument("--data", default="data")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    ap.add_argument("--rate-limit", type=float, default=1.0)
    args = ap.parse_args()

    config = Path(args.config)
    db_path = Path(args.db)
    data_dir = Path(args.data)

    if args.once or not args.loop:
        run_once(config, db_path, data_dir, rate_limit_sec=args.rate_limit)
        return

    while True:
        run_once(config, db_path, data_dir, rate_limit_sec=args.rate_limit)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
    
