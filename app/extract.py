from pathlib import Path
import zipfile

def _is_within_directory(base: Path, target: Path) -> bool:
    base = base.resolve()
    target = target.resolve()
    return str(target).startswith(str(base) + "/") or target == base

def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        for member in z.infolist():
            out_path = dest_dir / member.filename
            if not _is_within_directory(dest_dir, out_path):
                raise RuntimeError(f"Unsafe zip path detected: {member.filename}")
        z.extractall(dest_dir)

def remove_txt_files(root_dir: Path) -> int:
    removed = 0
    for p in root_dir.rglob("*.txt"):
        try:
            p.unlink()
            removed += 1
        except Exception:
            pass
    return removed
