import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS printers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS firmware (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  printer_id INTEGER NOT NULL,
  discovered_at TEXT NOT NULL,
  download_url TEXT NOT NULL,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  filesize INTEGER NOT NULL,
  stored_zip_path TEXT NOT NULL,
  stored_extract_path TEXT NOT NULL,
  FOREIGN KEY(printer_id) REFERENCES printers(id),
  UNIQUE(printer_id, download_url)
);

CREATE INDEX IF NOT EXISTS idx_firmware_printer ON firmware(printer_id);
"""

def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    return con

def ensure_printer(con: sqlite3.Connection, name: str) -> int:
    con.execute("INSERT OR IGNORE INTO printers(name) VALUES(?)", (name,))
    row = con.execute("SELECT id FROM printers WHERE name=?", (name,)).fetchone()
    return int(row[0])

def has_url(con: sqlite3.Connection, printer_id: int, download_url: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM firmware WHERE printer_id=? AND download_url=?",
        (printer_id, download_url),
    ).fetchone()
    return row is not None

def insert_firmware(
    con: sqlite3.Connection,
    printer_id: int,
    discovered_at: str,
    download_url: str,
    filename: str,
    sha256: str,
    filesize: int,
    stored_zip_path: str,
    stored_extract_path: str,
) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO firmware(
          printer_id, discovered_at, download_url,
          filename, sha256, filesize, stored_zip_path, stored_extract_path
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            printer_id, discovered_at, download_url,
            filename, sha256, filesize, stored_zip_path, stored_extract_path
        ),
    )
    con.commit()
