from pathlib import Path
import shutil

def snapshot_current_to_old(printer_dir: Path, version: str) -> Path | None:
    """
    Copies the current extracted firmware contents from current/
    into old/<version>/.
    Does NOT delete anything from current/.
    Skips firmware.zip.
    """
    current_dir = printer_dir / "current"

    if not current_dir.exists():
        return None

    items_to_copy = [item for item in current_dir.iterdir() if item.name != "firmware.zip"]
    if not items_to_copy:
        return None

    archive_dir = printer_dir / "old" / version
    archive_dir.mkdir(parents=True, exist_ok=True)

    for item in items_to_copy:
        dest = archive_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    return archive_dir
