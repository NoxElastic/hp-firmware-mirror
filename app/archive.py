from pathlib import Path
import shutil

def archive_extracted_only(printer_dir: Path, version: str) -> Path | None:
    """
    Copies the *contents* of current/ into old/<version>/ (no extra extracted folder),
    then deletes the copied contents from current/.
    Does NOT move the zip.
    """
    current_dir = printer_dir / "current"

    if not current_dir.exists():
        return None

    items_to_archive = [item for item in current_dir.iterdir() if item.name != "firmware.zip"]
    if not items_to_archive:
        return None

    # old/<version>/
    archive_dir = printer_dir / "old" / version
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Copy contents of current_dir into archive_dir, then remove them from current_dir
    for item in items_to_archive:
        dest = archive_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
            shutil.rmtree(item)
        else:
            shutil.copy2(item, dest)
            item.unlink(missing_ok=True)

    return archive_dir
    
