from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .device_identification import identify_device
from .exterior_analysis import analyze_exterior
from .grading_rules import grade_from_atomic_damage
from .local_valuation import local_fallback_valuation
from .market_evidence import exterior_grade_error_response, normalize_exterior_grade, parse_storage_gb, retrieve_current_market_evidence
from .phone_catalogue import confirm_device, get_catalogue_device, search_catalogue
from .schemas import ImageInput, MergedDamage, YoloDetection
from .valuation import value_from_market_observations

APP_DIR = Path(__file__).resolve().parents[2] / "app"
APP_HTML = APP_DIR / "ReValue-AI-Prototype-v1.16.1-predeployment-preview.html"
VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor"
SESSION_TTL_SECONDS = 60 * 60
REMOTE_VIEWS = ["front", "back", "camera", "left_side", "right_side"]
REMOTE_VIEW_LABELS = {
    "front": "Front",
    "back": "Back",
    "camera": "Camera Close-up",
    "left_side": "Left Side",
    "right_side": "Right Side",
}
REMOTE_SESSIONS: dict[str, dict[str, Any]] = {}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
    raw = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _file_response(handler: BaseHTTPRequestHandler, path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    content_types = {
        ".html": "text/html; charset=utf-8",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
    }
    raw = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_types.get(path.suffix.lower(), "application/octet-stream"))
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)
    return True


def _cleanup_sessions() -> None:
    now = time.time()
    expired = [token for token, session in REMOTE_SESSIONS.items() if session["expires_at_epoch"] < now]
    for token in expired:
        REMOTE_SESSIONS.pop(token, None)


def _new_session(origin: str) -> dict[str, Any]:
    _cleanup_sessions()
    token = f"RVA-LIVE-{secrets.token_hex(4).upper()}"
    now = time.time()
    session = {
        "session_id": token,
        "created_at_epoch": now,
        "expires_at_epoch": now + SESSION_TTL_SECONDS,
        "status": "open",
        "views": {view: {"view": view, "label": REMOTE_VIEW_LABELS[view], "status": "empty"} for view in REMOTE_VIEWS},
        "session_url": f"{origin.rstrip('/')}/scan/{quote(token)}",
        "completed_at_epoch": None,
    }
    REMOTE_SESSIONS[token] = session
    return _public_session(session, include_images=False)


def _public_session(session: dict[str, Any], *, include_images: bool = True) -> dict[str, Any]:
    views: dict[str, Any] = {}
    for view, item in session["views"].items():
        public_item = {key: value for key, value in item.items() if include_images or key not in {"bytes_b64"}}
        views[view] = public_item
    received = sum(1 for item in session["views"].values() if item.get("status") == "complete")
    return {
        "status": session["status"],
        "session_id": session["session_id"],
        "session_url": session["session_url"],
        "expires_at_epoch": session["expires_at_epoch"],
        "received_count": received,
        "required_count": len(REMOTE_VIEWS),
        "complete": received == len(REMOTE_VIEWS) and session["status"] in {"ready", "complete"},
        "views": views,
    }


def _session_or_error(token: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    _cleanup_sessions()
    session = REMOTE_SESSIONS.get(token)
    if not session:
        return None, {"status": "error", "error": "scan_session_not_found_or_expired"}
    if session.get("status") == "expired":
        return None, {"status": "error", "error": "scan_session_expired"}
    return session, None


def _qr_svg(url: str) -> tuple[int, str, str]:
    try:
        if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
            sys.path.insert(0, str(VENDOR_DIR))
        import qrcode
        import qrcode.image.svg
    except Exception:
        safe = json.dumps(url)
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240" role="img" aria-label="QR dependency missing">
<rect width="240" height="240" rx="16" fill="#eafffb"/>
<text x="120" y="102" text-anchor="middle" font-family="Arial" font-size="13" font-weight="700" fill="#09252b">QR library required</text>
<text x="120" y="126" text-anchor="middle" font-family="Arial" font-size="10" fill="#1f4a52">Copy Link works now</text>
<script type="application/json" id="qr-target">{safe}</script>
</svg>"""
        return 503, svg, "image/svg+xml"

    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    return 200, image.to_string(encoding="unicode"), "image/svg+xml"


def _remote_capture_page(token: str) -> str:
    labels = json.dumps(REMOTE_VIEW_LABELS)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#061b20">
<title>ReValue AI Remote Exterior Scan</title>
<style>
:root{{--bg:#061b20;--panel:#0a252b;--line:#1f4a52;--teal:#16d9c5;--cyan:#83f7e9;--lime:#b9ff78;--gold:#f5c451;--ink:#f5fbfa;--muted:#9db8bb}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at 90% -10%,rgba(22,217,197,.25),transparent 34%),linear-gradient(180deg,#061b20,#041418);color:var(--ink);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{max-width:520px;margin:0 auto;padding:18px 16px 30px}}.brand{{font-weight:900;display:flex;align-items:center;gap:9px;margin-bottom:20px}}.mark{{width:34px;height:34px;border-radius:12px;background:linear-gradient(135deg,var(--teal),var(--lime));display:grid;place-items:center;color:#052126;font-weight:900}}
.eyebrow{{font-size:11px;letter-spacing:.12em;color:var(--teal);font-weight:900}}h1{{font-size:30px;line-height:1.05;margin:8px 0 10px}}.card{{border:1px solid var(--line);border-radius:22px;background:linear-gradient(145deg,rgba(16,52,60,.96),rgba(7,31,37,.96));padding:15px;box-shadow:0 18px 55px rgba(0,0,0,.34)}}
.progress{{display:flex;gap:7px;margin:12px 0 14px}}.dot{{flex:1;height:8px;border-radius:999px;background:#082229;border:1px solid rgba(131,247,233,.12)}}.dot.done{{background:linear-gradient(90deg,var(--teal),var(--lime))}}.dot.current{{background:var(--gold)}}.stage{{font-weight:900;color:var(--cyan);margin-bottom:10px}}
.camera{{position:relative;min-height:330px;border:1px solid rgba(131,247,233,.18);border-radius:20px;background:#031519;display:grid;place-items:center;overflow:hidden}}video,img{{width:100%;height:100%;min-height:330px;object-fit:cover;display:block}}.guide{{position:absolute;inset:30px;border:2px dashed rgba(131,247,233,.7);border-radius:30px;pointer-events:none;box-shadow:0 0 30px rgba(22,217,197,.15)}}.hint{{color:var(--muted);font-size:13px;text-align:center;padding:28px;line-height:1.45}}
button{{width:100%;border:0;border-radius:16px;padding:15px 16px;margin-top:11px;font:inherit;font-weight:900;cursor:pointer}}.primary{{background:linear-gradient(120deg,var(--teal),var(--lime));color:#052126}}.secondary{{background:#09252b;color:var(--ink);border:1px solid var(--line)}}.row{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.doneText{{display:none;text-align:center;padding:28px 10px;color:var(--lime);font-weight:900}}.doneText span{{display:block;color:var(--muted);font-size:13px;margin-top:8px;font-weight:600}}input{{display:none}}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand"><div class="mark">RV</div>ReValue AI</div>
  <div class="eyebrow">REMOTE CAMERA</div>
  <h1>Exterior Scan</h1>
  <section class="card" id="captureCard">
    <div class="stage" id="stage">1 of 5 Â· Front</div>
    <div class="progress" id="progress"></div>
    <div class="camera" id="cameraBox"><div class="hint">Use the camera or upload a clear photo.</div><div class="guide"></div></div>
    <input id="fileInput" type="file" accept="image/*" capture="environment">
    <button class="primary" type="button" onclick="openCamera()">Take Photo</button>
    <div class="row" id="previewActions" style="display:none"><button class="secondary" type="button" onclick="retake()">Retake</button><button class="primary" type="button" onclick="usePhoto()">Use Photo</button></div>
    <button class="primary" id="sendBtn" type="button" onclick="sendPhotos()" style="display:none">Send Photos</button>
  </section>
  <section class="card doneText" id="doneText">Photos sent successfully<span>You can return to the other device.</span></section>
</div>
<script>
const SESSION_ID={json.dumps(token)};
const VIEWS={json.dumps(REMOTE_VIEWS)};
const LABELS={labels};
let current=0, pending=null;
function render(){{document.getElementById('stage').textContent=`${{Math.min(current+1,VIEWS.length)}} of ${{VIEWS.length}} Â· ${{LABELS[VIEWS[current]]||''}}`;document.getElementById('progress').innerHTML=VIEWS.map((v,i)=>`<i class="dot ${{i<current?'done':i===current?'current':''}}"></i>`).join('');document.getElementById('sendBtn').style.display=current>=VIEWS.length?'block':'none'}}
function openCamera(){{document.getElementById('fileInput').click()}}
document.getElementById('fileInput').addEventListener('change',e=>{{const file=e.target.files&&e.target.files[0];if(!file)return;const reader=new FileReader();reader.onload=()=>{{pending={{name:file.name,type:file.type||'image/jpeg',data:String(reader.result)}};document.getElementById('cameraBox').innerHTML=`<img src="${{pending.data}}" alt="Preview"><div class="guide"></div>`;document.getElementById('previewActions').style.display='grid'}};reader.readAsDataURL(file)}});
function retake(){{pending=null;document.getElementById('previewActions').style.display='none';document.getElementById('cameraBox').innerHTML='<div class="hint">Use the camera or upload a clear photo.</div><div class="guide"></div>';document.getElementById('fileInput').value=''}}
async function usePhoto(){{if(!pending)return;const view=VIEWS[current];const bytes=pending.data.split(',')[1]||'';await fetch(`/remote-session/${{encodeURIComponent(SESSION_ID)}}/photo`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{view,filename:pending.name,mime_type:pending.type,bytes_b64:bytes}})}});current++;pending=null;document.getElementById('previewActions').style.display='none';document.getElementById('fileInput').value='';document.getElementById('cameraBox').innerHTML='<div class="hint">Use the camera or upload a clear photo.</div><div class="guide"></div>';render()}}
async function sendPhotos(){{await fetch(`/remote-session/${{encodeURIComponent(SESSION_ID)}}/complete`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{}}'}});document.getElementById('captureCard').style.display='none';document.getElementById('doneText').style.display='block'}}
render();
</script>
</body>
</html>"""


class ReValueHandler(BaseHTTPRequestHandler):
    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/app"}:
            if _file_response(self, APP_HTML):
                return
            fallback = APP_DIR / "ReValue-AI-Prototype-v1.15.0-live-exterior-preview.html"
            if _file_response(self, fallback):
                return
        if path.startswith("/scan/"):
            token = unquote(path.rsplit("/", 1)[-1])
            session, error = _session_or_error(token)
            if error:
                _text_response(self, 404, "<h1>Scan session expired</h1><p>Please create a new ReValue scan session.</p>")
                return
            _text_response(self, 200, _remote_capture_page(session["session_id"]))
            return
        if path.startswith("/remote-session/") and path.endswith("/qr.svg"):
            token = unquote(path.split("/")[2])
            session, error = _session_or_error(token)
            if error:
                _json_response(self, 404, error)
                return
            status, svg, content_type = _qr_svg(session["session_url"])
            _text_response(self, status, svg, content_type)
            return
        if path.startswith("/remote-session/"):
            token = unquote(path.rsplit("/", 1)[-1])
            session, error = _session_or_error(token)
            _json_response(self, 404 if error else 200, error or _public_session(session))
            return
        asset = APP_DIR / unquote(path.lstrip("/"))
        try:
            asset.resolve().relative_to(APP_DIR.resolve())
        except Exception:
            asset = Path()
        if asset and _file_response(self, asset):
            return
        if self.path == "/health":
            _json_response(self, 200, {"status": "ok", "service": "revalue-phase6a3-backend"})
            return
        if self.path == "/market-evidence/status":
            _json_response(self, 200, retrieve_current_market_evidence())
            return
        if path == "/catalogue/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            _json_response(self, 200, {"results": search_catalogue(query)})
            return
        if path.startswith("/catalogue/device/"):
            catalogue_id = unquote(path.rsplit("/", 1)[-1])
            device = get_catalogue_device(catalogue_id)
            _json_response(self, 200 if device else 404, {"device": device} if device else {"error": "not_found"})
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_json()
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/remote-session":
            origin = self.headers.get("Origin") or f"http://{self.headers.get('Host', '127.0.0.1:8765')}"
            _json_response(self, 200, _new_session(origin))
            return
        if path.startswith("/remote-session/") and path.endswith("/photo"):
            token = unquote(path.split("/")[2])
            session, error = _session_or_error(token)
            if error:
                _json_response(self, 404, error)
                return
            if session["status"] not in {"open", "ready"}:
                _json_response(self, 409, {"status": "error", "error": "scan_session_closed"})
                return
            view = payload.get("view")
            if view not in REMOTE_VIEWS:
                _json_response(self, 400, {"status": "error", "error": "valid_view_required"})
                return
            if not payload.get("bytes_b64"):
                _json_response(self, 400, {"status": "error", "error": "image_bytes_required"})
                return
            session["views"][view] = {
                "view": view,
                "label": REMOTE_VIEW_LABELS[view],
                "status": "complete",
                "filename": payload.get("filename") or f"{view}.jpg",
                "mime_type": payload.get("mime_type") or "image/jpeg",
                "bytes_b64": payload["bytes_b64"],
                "uploaded_at_epoch": time.time(),
            }
            if all(item.get("status") == "complete" for item in session["views"].values()):
                session["status"] = "ready"
            _json_response(self, 200, _public_session(session, include_images=False))
            return
        if path.startswith("/remote-session/") and path.endswith("/complete"):
            token = unquote(path.split("/")[2])
            session, error = _session_or_error(token)
            if error:
                _json_response(self, 404, error)
                return
            if not all(item.get("status") == "complete" for item in session["views"].values()):
                _json_response(self, 400, {"status": "error", "error": "all_five_views_required"})
                return
            session["status"] = "complete"
            session["completed_at_epoch"] = time.time()
            _json_response(self, 200, _public_session(session))
            return
        if self.path == "/identify-device":
            images = [ImageInput(**item) for item in payload.get("images", [])]
            _json_response(self, 200, asdict(identify_device(images)))
            return

        if self.path == "/confirm-device":
            _json_response(
                self,
                200,
                confirm_device(
                    catalogue_id=payload.get("catalogue_id", ""),
                    storage=payload.get("storage"),
                ),
            )
            return

        if self.path == "/analyze-exterior":
            images = [ImageInput(**item) for item in payload.get("images", [])]
            _json_response(self, 200, analyze_exterior(images))
            return

        if self.path == "/market-evidence/retrieve":
            if not normalize_exterior_grade(payload.get("exterior_grade")):
                _json_response(self, 200, exterior_grade_error_response())
                return
            _json_response(
                self,
                200,
                retrieve_current_market_evidence(
                    confirmed_device=payload.get("confirmed_device"),
                    exterior_grade=payload.get("exterior_grade"),
                ),
            )
            return

        if self.path == "/merge-and-grade":
            yolo_items = [YoloDetection(**item) for item in payload.get("yolo_detections", [])]
            damage = [
                MergedDamage(
                    damage_type=item.damage_type,
                    view=item.view,
                    severity="unknown",
                    support="yolo_only",
                    verification="confirmed",
                    grade_affecting=True,
                    manual_review=False,
                    confidence=item.confidence,
                    bounding_box_xyxy=item.bounding_box_xyxy,
                )
                for item in yolo_items
                if item.damage_type is not None
            ]
            _json_response(self, 200, asdict(grade_from_atomic_damage(damage)))
            return

        if self.path == "/live-valuation":
            confirmed_device = payload.get("confirmed_device")
            exterior_grade = payload.get("exterior_grade")
            if not normalize_exterior_grade(exterior_grade):
                _json_response(self, 200, exterior_grade_error_response())
                return
            if not confirmed_device:
                _json_response(
                    self,
                    200,
                    {
                        "status": "error",
                        "currency": "MYR",
                        "value_low": None,
                        "value_high": None,
                        "midpoint": None,
                        "exact_model_count": 0,
                        "condition_matched_count": 0,
                        "evidence_status": "error",
                        "distribution": {},
                        "trade_in_benchmark": [],
                        "repair_references": [],
                        "sources": [],
                        "limitations": ["Public live valuation requires confirmed_device and exterior_grade."],
                        "retryable": False,
                        "error": "confirmed_device_required",
                    },
                )
                return

            market_evidence = retrieve_current_market_evidence(
                confirmed_device=confirmed_device,
                exterior_grade=exterior_grade,
            )
            if market_evidence.get("status") != "ok" or not market_evidence.get("market_observations"):
                if market_evidence.get("error") not in {
                    "confirmed_device_required",
                    "catalogue_id_required",
                    "storage_required",
                    "catalogue_id_not_found",
                    "confirmed_device_identity_mismatch",
                    "storage_confirmation_required",
                    "valid_exterior_grade_required",
                }:
                    fallback_device = market_evidence.get("query_device") or confirmed_device
                    fallback = local_fallback_valuation(
                        confirmed_device=fallback_device,
                        exterior_grade=str(exterior_grade),
                        fallback_reason=str(market_evidence.get("status") or market_evidence.get("error") or "live_retrieval_unavailable"),
                    )
                    fallback["live_retrieval_status"] = market_evidence.get("status")
                    fallback["live_retrieval_error"] = market_evidence.get("error")
                    _json_response(self, 200, fallback)
                    return
                _json_response(
                    self,
                    200,
                    {
                        "status": market_evidence.get("status"),
                        "currency": "MYR",
                        "value_low": None,
                        "value_high": None,
                        "midpoint": None,
                        "exact_model_count": 0,
                        "condition_matched_count": 0,
                        "evidence_status": market_evidence.get("status"),
                        "distribution": {},
                        "trade_in_benchmark": market_evidence.get("trade_in_benchmarks", []),
                        "repair_references": market_evidence.get("repair_references", []),
                        "sources": [],
                        "limitations": market_evidence.get("notes", []),
                        "retryable": market_evidence.get("retryable", False),
                        "error": market_evidence.get("error"),
                        "grounded_sources": market_evidence.get("grounded_sources", []),
                    },
                )
                return
            query_device = market_evidence.get("query_device") or confirmed_device or {}
            storage_gb = parse_storage_gb(query_device.get("storage"))
            result = value_from_market_observations(
                observations=market_evidence.get("market_observations", []),
                brand=query_device.get("brand", ""),
                model=query_device.get("model", ""),
                storage_gb=storage_gb,
                condition_group=market_evidence.get("condition_group"),
                trade_in_benchmark=market_evidence.get("trade_in_benchmarks", []),
                repair_references=market_evidence.get("repair_references", []),
            )
            payload_result = asdict(result)
            payload_result["grounded_sources"] = market_evidence.get("grounded_sources", [])
            _json_response(self, 200, payload_result)
            return

        _json_response(self, 404, {"error": "not_found"})


def run(host: str = "0.0.0.0", port: int = 8765) -> None:
    port = int(os.environ.get("PORT", str(port)))
    server = HTTPServer((host, port), ReValueHandler)
    print(f"ReValue Phase 6A.3 backend listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()


