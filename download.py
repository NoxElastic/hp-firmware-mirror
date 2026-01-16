import hashlib
import os
from pathlib import Path
import requests

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def download_stream(
    url: str,
    dest_dir: Path,
    session: requests.Session,
    timeout=(10, 600),
    filename: str | None = None,
) -> tuple[Path, str, int]:
    dest_dir.mkdir(parents=True, exist_ok=True)

    real_name = url.split("/")[-1].split("?")[0]
    out_name = filename or real_name

    tmp_path = dest_dir / (out_name + ".part")
    final_path = dest_dir / out_name

    with session.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with tmp_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)

    os.replace(tmp_path, final_path)

    digest = sha256_file(final_path)
    size = final_path.stat().st_size
    return final_path, digest, size
