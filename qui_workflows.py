import json
import os
import urllib.error
import urllib.request

from arr import normalize_arr_connections


class QuiWorkflowError(Exception):
    """Raised when a qui workflow request cannot be safely prepared or sent."""


def _require_qui_config(cfg):
    if (cfg.get("TORRENT_SOURCE") or "qbit") != "qui":
        raise QuiWorkflowError("Torrent source must be set to qui")

    host = (cfg.get("QUI_HOST") or "").strip()
    if not host:
        raise QuiWorkflowError("QUI_HOST is required")

    api_key = (cfg.get("QUI_API_KEY") or "").strip()
    if not api_key:
        raise QuiWorkflowError("QUI_API_KEY is required")

    return host.rstrip("/"), api_key


def resolve_media_scan_path(cfg, requested_path):
    media_root = (cfg.get("MEDIA_PATH") or "").strip()
    if not media_root:
        raise QuiWorkflowError("MEDIA_PATH is required")

    requested = str(requested_path or "").strip()
    if not requested:
        raise QuiWorkflowError("path is required")

    media_root_abs = os.path.abspath(os.path.normpath(media_root))
    candidate = requested if os.path.isabs(requested) else os.path.join(media_root_abs, requested)
    candidate_abs = os.path.abspath(os.path.normpath(candidate))

    try:
        within_media_root = os.path.commonpath([media_root_abs, candidate_abs]) == media_root_abs
    except ValueError:
        within_media_root = False

    if not within_media_root:
        raise QuiWorkflowError("path is outside MEDIA_PATH")

    return candidate_abs


def _dir_scan_payload(scan_path, service=None, download_client=None):
    client = str(download_client or "").strip()
    if not client:
        return {"path": scan_path}

    service = str(service or "").strip().lower()
    if service == "radarr":
        return {"downloadClient": client, "movie": {"folderPath": scan_path}}
    if service == "sonarr":
        return {"downloadClient": client, "series": {"path": scan_path}}
    raise QuiWorkflowError("service must be sonarr or radarr when download_client is set")


def _arr_download_client(cfg, service=None, connection_id=None):
    connection_id = str(connection_id or "").strip()
    if not connection_id:
        return ""

    service = str(service or "").strip().lower() or None
    for conn in normalize_arr_connections(cfg, service=service):
        if conn.get("id") == connection_id:
            return conn.get("qui_download_client", "")
    return ""


def trigger_dir_scan(cfg, scan_path, timeout=30, service=None, download_client=None):
    host, api_key = _require_qui_config(cfg)
    url = host + "/api/dir-scan/webhook/scan"
    payload = json.dumps(_dir_scan_payload(scan_path, service=service, download_client=download_client)).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status_code = getattr(resp, "status", getattr(resp, "code", 200))
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if getattr(e, "fp", None) else ""
        detail = e.reason or "qui request failed"
        if body:
            try:
                parsed = json.loads(body)
                detail = parsed.get("message") or parsed.get("error") or detail
            except Exception:
                detail = body.strip() or detail
        raise QuiWorkflowError(f"qui dir-scan webhook failed with HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise QuiWorkflowError(f"qui dir-scan webhook connection failed: {e.reason}") from e

    body = raw.decode("utf-8", errors="replace").strip()
    response = {}
    if body:
        try:
            response = json.loads(body)
        except json.JSONDecodeError:
            response = {"raw": body}

    return {
        "path": scan_path,
        "status_code": status_code,
        "response": response,
    }


def trigger_qui_dir_scan(cfg, requested_path, service=None, connection_id=None):
    download_client = _arr_download_client(cfg, service=service, connection_id=connection_id)
    return trigger_dir_scan(
        cfg,
        resolve_media_scan_path(cfg, requested_path),
        service=service,
        download_client=download_client,
    )
