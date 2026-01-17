from pathlib import Path
import shutil

def archive_extracted_only(printer_dir: Path, version: str) -> Path | None:
    """
    Copies the *contents* of current/extracted into old/<version>/ (no extra extracted folder),
    then deletes current/extracted.
    Does NOT move the zip.
    """
    current_dir = printer_dir / "current"

    if not current_dir.exists():
        return None

    # old/<version>/
    archive_dir = printer_dir / "old" / version
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Copy contents of extracted_dir into archive_dir
    for item in current_dir.iterdir():
        if item.name == "firmware.zip":
            continue
        dest = archive_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            archive_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


    return archive_dir
