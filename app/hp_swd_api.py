import re
import requests
from typing import Any, Dict, List, Optional, Tuple

BASE = "https://support.hp.com"

FW_FILENAME_RE = re.compile(
    r"\b([A-Za-z0-9_]+)_fs\d+(?:\.\d+)+_fw_[A-Za-z0-9_.-]+?\.zip\b",
    re.IGNORECASE
)
FTP_ZIP_RE = re.compile(
    r"https?://ftp\.hp\.com/pub/softlib/[A-Za-z0-9/_\.\-]+?\.zip",
    re.IGNORECASE
)

def _request_json(session: requests.Session, method: str, url: str, **kwargs) -> Dict[str, Any]:
    r = session.request(method, url, timeout=(10, 60), **kwargs)
    r.raise_for_status()
    j = r.json()
    if not isinstance(j, dict):
        return {}
    return j

def _extract_firmware_urls(obj: Any) -> List[str]:
    s = str(obj)
    urls = FTP_ZIP_RE.findall(s)
    out = list(dict.fromkeys(urls))
    for m in FW_FILENAME_RE.finditer(s):
        folder = m.group(1)
        fn = m.group(0)
        out.append(f"https://ftp.hp.com/pub/softlib/software13/printers/{folder}/{fn}")
    # de-dup
    seen = set()
    uniq = []
    for u in out:
        u = re.sub(r"^http://", "https://", u, flags=re.IGNORECASE)
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq

def _walk(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield None, v
            yield from _walk(v)

def _find_first(obj: Any, keys: List[str]) -> Optional[Any]:
    keys_l = {k.lower() for k in keys}
    for k, v in _walk(obj):
        if isinstance(k, str) and k.lower() in keys_l:
            if v not in (None, "", "null"):
                return v
    return None

def _get_product_oids_from_pdp(session: requests.Session, locale: str, series_oid: int) -> Dict[str, Any]:
    url = f"{BASE}/wcc-services/pdp/attributes/{locale}"
    j = _request_json(session, "GET", url, params={"oid": str(series_oid)})

    data = j.get("data")
    if not isinstance(data, dict):
        return {}

    details = data.get("attributeDetails")
    kv: Dict[str, Any] = {}
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict):
                n = item.get("name")
                val = item.get("value")
                if isinstance(n, str) and val not in (None, "", "null"):
                    kv[n] = val
    elif isinstance(details, dict):
        kv.update(details)

    def pick(*names):
        for n in names:
            if n in kv and kv[n] not in (None, "", "null"):
                return kv[n]
        for n in names:
            if n in data and data[n] not in (None, "", "null"):
                return data[n]
        return None

    return {
        "productLineCode": pick("productLineCode", "ProductLineCode") or "",
        "productNumberOid": pick("productNumberOid", "ProductNumberOid"),
        "productSeriesOid": pick("productSeriesOid", "ProductSeriesOid") or series_oid,
        "productNameOid": pick("productNameOid", "ProductNameOid"),
        "seriesContext": bool(data.get("seriesContext", True)),
    }

def _get_product_oids_from_warranty_specs(session: requests.Session, cc: str, lc: str, series_oid: int) -> Dict[str, Any]:
    """
    Uses the endpoint you captured earlier:
      POST /wcc-services/profile/devices/warranty/specs?cache=true&authState=anonymous&template=SWDClosure_Manual
    This endpoint typically returns richer identifiers.
    """
    url = f"{BASE}/wcc-services/profile/devices/warranty/specs"
    params = {"cache": "true", "authState": "anonymous", "template": "SWDClosure_Manual"}

    body = {
        "cc": cc,
        "lc": lc,
        "utcOffset": "P0100",
        "devices": [{
            "seriesOid": int(series_oid),
            "modelOid": None,
            "serialNumber": None,
            "displayProductNumber": None,
            "countryOfPurchase": cc,
        }],
        "captchaToken": ""
    }

    j = _request_json(session, "POST", url, params=params, json=body)

    # Try to find IDs anywhere in the response
    product_line_code = _find_first(j, ["productLineCode", "productLine", "productLineCd", "product_line_code"])
    product_number_oid = _find_first(j, ["productNumberOid", "productNumberOID", "productNumberId", "productNumber"])
    product_series_oid = _find_first(j, ["productSeriesOid", "productSeriesOID", "seriesOid"])
    product_name_oid = _find_first(j, ["productNameOid", "productNameOID", "productNameId"])

    return {
        "productLineCode": str(product_line_code) if product_line_code else "",
        "productNumberOid": product_number_oid,
        "productSeriesOid": int(product_series_oid) if isinstance(product_series_oid, (int, str)) and str(product_series_oid).isdigit() else series_oid,
        "productNameOid": product_name_oid,
        "seriesContext": True,
    }

def _pick_os_version(os_json: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Your structure:
      data.osAvailablePlatformsAnsOS.osPlatforms[].{id,name,osVersions[].id}
    driverDetails expects:
      platformId, osName, osTMSId (= versionId)
    """
    data = os_json.get("data") or {}
    container = data.get("osAvailablePlatformsAnsOS") or {}
    platforms = container.get("osPlatforms") or []
    if not platforms:
        return None, None, None

    def rank(p: Dict[str, Any]) -> int:
        name = str(p.get("name", "")).lower()
        if "linux" in name:
            return 0
        if "windows" in name:
            return 1
        return 9

    platforms = sorted(platforms, key=rank)
    p0 = platforms[0]
    platform_id = p0.get("id")
    platform_name = p0.get("name")

    os_versions = p0.get("osVersions") or []
    if not os_versions:
        return platform_id, platform_name, None

    v0 = os_versions[0]
    version_id = v0.get("id")
    return platform_id, platform_name, version_id

def discover_firmware_urls_swd(
    session: requests.Session,
    series_oid: int,
    cc: str = "se",
    lc: str = "sv",
    debug: bool = False,
) -> List[str]:
    locale = f"{cc}-{lc}"

    session.headers.setdefault("Referer", "https://support.hp.com/")
    session.headers.setdefault("Origin", "https://support.hp.com")
    session.headers.setdefault("Accept", "application/json, text/plain, */*")

    # 1) Product identifiers (try PDP first, then warranty/specs)
    prod = _get_product_oids_from_pdp(session, locale=locale, series_oid=series_oid)
    if not prod.get("productNumberOid"):
        prod2 = _get_product_oids_from_warranty_specs(session, cc=cc, lc=lc, series_oid=series_oid)
        # merge, prefer warranty/specs values
        prod = {**prod, **{k: v for k, v in prod2.items() if v not in (None, "", "null")}}

    if debug:
        print("[DBG] product oids:", prod)

    if not prod.get("productNumberOid"):
        if debug:
            print("[DBG] Still missing productNumberOid even after warranty/specs")
        return []

    # 2) OS version data
    os_url = f"{BASE}/wcc-services/swd-v2/osVersionData"
    os_json = _request_json(session, "GET", os_url, params={"cc": cc, "lc": lc, "productOid": str(series_oid)})
    platform_id, platform_name, version_id = _pick_os_version(os_json)

    if debug:
        print("[DBG] picked:", platform_name, platform_id, "versionId:", version_id)

    if not (platform_id and platform_name and version_id):
        return []

    # 3) EXACT payload from JS for driverDetails
    drv_url = f"{BASE}/wcc-services/swd-v2/driverDetails"
    payload = {
        "productLineCode": prod.get("productLineCode", "") or "",
        "lc": lc,
        "cc": cc,
        "osTMSId": version_id,
        "osName": platform_name,
        "productNumberOid": prod["productNumberOid"],
        "productSeriesOid": prod.get("productSeriesOid", series_oid),
        "platformId": platform_id,
    }
    if not prod.get("seriesContext", True) and prod.get("productNameOid"):
        payload["productNameOid"] = prod["productNameOid"]

    r = session.post(drv_url, json=payload, timeout=(10, 60))
    if debug:
        print("[DBG] driverDetails status:", r.status_code, "len:", len(r.content))
        print("[DBG] driverDetails preview:", r.text[:250])
    r.raise_for_status()

    drv_json = r.json()
    data = drv_json.get("data")
    if data is None:
        return []

    return _extract_firmware_urls(data)
