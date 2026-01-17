import re
from urllib.parse import urlparse

def score_url(url: str) -> tuple:
    """
    Sort newest-first using numeric tokens + fs token.
    """
    fn = urlparse(url).path.split("/")[-1]

    nums = [int(x) for x in re.findall(r"(\d{6,})", fn)]
    max_num = max(nums) if nums else 0

    fs = ""
    m = re.search(r"(fs[\d\.]+)", fn, re.IGNORECASE)
    if m:
        fs = m.group(1).lower()

    return (max_num, fs, fn)

def pick_best_link(links: list[str]) -> str | None:
    if not links:
        return None
    return sorted(links, key=score_url, reverse=True)[0]


FS_VER_RE = re.compile(r"_fs(\d+(?:\.\d+)+)_fw_", re.IGNORECASE)

def firmware_version_from_url(url: str) -> str | None:
    m = FS_VER_RE.search(url)
    return m.group(1) if m else None
