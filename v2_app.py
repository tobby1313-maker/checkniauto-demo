from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from PIL import Image
from werkzeug.utils import secure_filename

from v2_pipeline import (
    MAX_VISION_IMAGES,
    PipelineError,
    SUPPORTED_HOSTS,
    TEXT_MODEL,
    VISION_MODEL,
    is_supported_url,
    normalize_language,
    run_analysis_pipeline,
    utc_now,
)

ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web_v2"
LEGACY_ASSETS_DIR = ROOT_DIR / "web" / "assets"
DATA_DIR = Path(
    os.environ.get("CHECKNI_DATA_DIR")
    or os.environ.get("SCRAPPER_DATA_DIR")
    or (Path(tempfile.gettempdir()) / "checkni-auto-v2")
)
JOBS_DIR = DATA_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = max(5, int(os.environ.get("CHECKNI_MAX_UPLOAD_MB", "30")))
JOB_TTL_HOURS = max(1, int(os.environ.get("CHECKNI_JOB_TTL_HOURS", "24")))
MAX_CONCURRENT_JOBS = max(1, min(8, int(os.environ.get("CHECKNI_MAX_CONCURRENT_JOBS", "2"))))
MAX_PENDING_JOBS = max(MAX_CONCURRENT_JOBS, int(os.environ.get("CHECKNI_MAX_PENDING_JOBS", "6")))
RATE_LIMIT_PER_DAY = max(1, int(os.environ.get("CHECKNI_RATE_LIMIT_PER_IP", "5")))
ACCESS_MODE = os.environ.get("CHECKNI_ACCESS_MODE", "beta").strip().lower()
PRICE_EUR = float(os.environ.get("CHECKNI_PRICE_EUR", "1.99"))

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/v2-static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "checkni-auto-v2-dev-change-me")

_job_file_lock = threading.RLock()
_pending_lock = threading.Lock()
_pending_count = 0
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="checkni-v2")
_rate_lock = threading.Lock()
_rate_counts: dict[str, dict[str, int]] = defaultdict(dict)

TERMINAL_STATES = {"done", "failed"}
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}


def _job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("Invalid job id")
    return JOBS_DIR / job_id


def _job_file(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_job(job_id: str) -> dict[str, Any] | None:
    try:
        path = _job_file(job_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    with _job_file_lock:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return value if isinstance(value, dict) else None


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _job_file_lock:
        current = _read_job(job_id)
        if current is None:
            raise FileNotFoundError(job_id)
        current.update(changes)
        current["updated_at"] = utc_now()
        current["revision"] = int(current.get("revision", 0)) + 1
        _atomic_write_json(_job_file(job_id), current)
        return current


def _create_job(source_type: str, source_url: str, language: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=False)
    now = utc_now()
    job = {
        "id": job_id,
        "schema_version": "2.0",
        "status": "queued",
        "stage": "queued",
        "progress": 2,
        "message": "Analýza je zaradená.",
        "source_type": source_type,
        "source_url": source_url,
        "language": normalize_language(language),
        "created_at": now,
        "updated_at": now,
        "revision": 1,
        "listing_preview": None,
        "report": None,
        "error": None,
    }
    _atomic_write_json(job_dir / "job.json", job)
    return job


def _public_job(job: dict[str, Any], include_report: bool = True) -> dict[str, Any]:
    allowed = {
        "id",
        "schema_version",
        "status",
        "stage",
        "progress",
        "message",
        "source_type",
        "source_url",
        "language",
        "created_at",
        "updated_at",
        "revision",
        "listing_preview",
        "error",
    }
    result = {key: job.get(key) for key in allowed}
    if include_report and job.get("status") == "done":
        result["report"] = job.get("report")
    return result


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:80]
    return (request.remote_addr or "unknown")[:80]


def _consume_rate_limit() -> Response | None:
    if ACCESS_MODE in {"open", "development"}:
        return None
    day = datetime.now(timezone.utc).date().isoformat()
    ip = _client_ip()
    with _rate_lock:
        count = _rate_counts[ip].get(day, 0)
        if count >= RATE_LIMIT_PER_DAY:
            return jsonify(
                {
                    "error": "Denný limit beta analýz bol vyčerpaný.",
                    "code": "rate_limit",
                }
            ), 429
        _rate_counts[ip] = {day: count + 1}
    return None


def _reserve_pending_slot() -> bool:
    global _pending_count
    with _pending_lock:
        if _pending_count >= MAX_PENDING_JOBS:
            return False
        _pending_count += 1
        return True


def _release_pending_slot() -> None:
    global _pending_count
    with _pending_lock:
        _pending_count = max(0, _pending_count - 1)


def _progress_callback(job_id: str):
    def update(stage: str, progress: int, message: str, payload: dict[str, Any] | None) -> None:
        changes: dict[str, Any] = {
            "status": "running",
            "stage": stage,
            "progress": progress,
            "message": message,
        }
        if payload:
            if "listing_preview" in payload:
                changes["listing_preview"] = payload["listing_preview"]
            if "report" in payload:
                changes["report"] = payload["report"]
        _update_job(job_id, **changes)

    return update


def _start_job(
    job: dict[str, Any],
    source_url: str,
    existing_listing_dir: Path | None = None,
) -> bool:
    if not _reserve_pending_slot():
        return False

    job_id = job["id"]
    job_dir = _job_dir(job_id)

    def worker() -> None:
        try:
            _update_job(
                job_id,
                status="running",
                stage="starting",
                progress=5,
                message="Pripravujem bezpečnú analýzu.",
            )
            report = run_analysis_pipeline(
                job_id=job_id,
                job_dir=job_dir,
                language=job.get("language", "sk"),
                callback=_progress_callback(job_id),
                source_url=source_url,
                existing_listing_dir=existing_listing_dir,
            )
            _update_job(
                job_id,
                status="done",
                stage="complete",
                progress=100,
                message="Analýza je hotová.",
                report=report,
                error=None,
            )
        except PipelineError as exc:
            _update_job(
                job_id,
                status="failed",
                stage="failed",
                progress=100,
                message="Analýzu sa nepodarilo dokončiť.",
                error={"code": "pipeline_error", "message": str(exc)},
            )
        except Exception as exc:
            (job_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            _update_job(
                job_id,
                status="failed",
                stage="failed",
                progress=100,
                message="Analýzu sa nepodarilo dokončiť.",
                error={
                    "code": "internal_error",
                    "message": "Nastala neočakávaná chyba. Kredit sa pri platenom režime nesmie odpočítať.",
                },
            )
        finally:
            _release_pending_slot()

    _executor.submit(worker)
    return True


def _cleanup_old_jobs() -> None:
    cutoff = time.time() - JOB_TTL_HOURS * 3600
    for child in JOBS_DIR.iterdir():
        if not child.is_dir() or not JOB_ID_RE.fullmatch(child.name):
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def _recover_interrupted_jobs() -> None:
    for child in JOBS_DIR.iterdir():
        if not child.is_dir() or not JOB_ID_RE.fullmatch(child.name):
            continue
        job = _read_job(child.name)
        if job and job.get("status") in {"queued", "running"}:
            try:
                _update_job(
                    child.name,
                    status="failed",
                    stage="failed",
                    progress=100,
                    message="Server bol počas analýzy reštartovaný.",
                    error={
                        "code": "interrupted",
                        "message": "Analýza bola prerušená reštartom servera. Spustite ju znova; kredit sa nemá odpočítať.",
                    },
                )
            except Exception:
                pass


def _valid_optional_url(value: str) -> bool:
    if not value:
        return True
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _save_uploaded_images(files: list[Any], destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    saved = 0
    for uploaded in files[:MAX_VISION_IMAGES]:
        if not uploaded or not uploaded.filename:
            continue
        original = secure_filename(uploaded.filename) or f"photo-{saved + 1}.jpg"
        extension = Path(original).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            continue
        temporary = destination / f".upload-{uuid.uuid4().hex}{extension}"
        uploaded.save(temporary)
        try:
            with Image.open(temporary) as image:
                image.verify()
            final_name = f"{saved + 1:02d}_{Path(original).stem[:60]}{extension}"
            temporary.replace(destination / final_name)
            saved += 1
        except Exception:
            temporary.unlink(missing_ok=True)
    return saved


def _build_manual_listing(
    job_dir: Path,
    title: str,
    price: str,
    currency: str,
    source_url: str,
    manual_text: str,
    files: list[Any],
) -> Path:
    listing_dir = job_dir / "listings" / "manual"
    images_dir = listing_dir / "images"
    listing_dir.mkdir(parents=True, exist_ok=True)
    photo_count = _save_uploaded_images(files, images_dir)
    amount_digits = re.sub(r"\D", "", price or "")
    amount = int(amount_digits) if amount_digits else 0
    safe_currency = "CZK" if currency.upper() == "CZK" else "EUR"

    lines = [
        f"# {title.strip() or 'Manuálne vložené vozidlo'}",
        "",
    ]
    if source_url:
        lines.extend([f"**Source:** {source_url}", ""])
    if amount:
        lines.extend(["## Price", f"- **Price:** {amount} {safe_currency}", ""])
    lines.extend(
        [
            "## Seller Note (Poznamka)",
            "",
            manual_text.strip() or "Údaje neboli doplnené.",
            "",
            "## Photos",
            f"- **Downloaded:** {photo_count}",
            "",
        ]
    )
    (listing_dir / "car_info.md").write_text("\n".join(lines), encoding="utf-8")
    return listing_dir


@app.after_request
def _security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none';",
    )
    return response


@app.route("/")
def index() -> Response:
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/assets/<path:filename>")
def legacy_asset(filename: str) -> Response:
    return send_from_directory(LEGACY_ASSETS_DIR, filename)


@app.route("/robots.txt")
def robots() -> Response:
    return Response("User-agent: *\nAllow: /\n", mimetype="text/plain")


@app.route("/healthz")
def healthz() -> Response:
    return jsonify(
        {
            "status": "ok",
            "version": "2.0",
            "access_mode": ACCESS_MODE,
            "models": {"text": TEXT_MODEL, "vision": VISION_MODEL},
        }
    )


@app.route("/api/v2/config")
def config() -> Response:
    return jsonify(
        {
            "version": "2.0",
            "supported_hosts": list(SUPPORTED_HOSTS),
            "max_manual_images": MAX_VISION_IMAGES,
            "price_eur": PRICE_EUR,
            "access_mode": ACCESS_MODE,
            "checkout_enabled": False,
            "daily_beta_limit": RATE_LIMIT_PER_DAY,
        }
    )


@app.route("/api/v2/jobs", methods=["POST"])
def create_url_job() -> Response:
    payload = request.get_json(silent=True) or {}
    url = str(payload.get("url") or "").strip()
    language = normalize_language(str(payload.get("language") or "sk"))
    if not url:
        return jsonify({"error": "Vložte odkaz na inzerát.", "code": "missing_url"}), 400
    if not is_supported_url(url):
        return jsonify(
            {
                "error": "Automaticky podporujeme Autobazar.eu, Autobazar.sk a Bazoš SK/CZ. Pre iný portál použite manuálny režim.",
                "code": "unsupported_url",
            }
        ), 400

    rate_error = _consume_rate_limit()
    if rate_error:
        return rate_error

    _cleanup_old_jobs()
    job = _create_job("url", url, language)
    if not _start_job(job, source_url=url):
        shutil.rmtree(_job_dir(job["id"]), ignore_errors=True)
        return jsonify(
            {
                "error": "Kapacita analýz je momentálne naplnená. Skúste požiadavku zopakovať.",
                "code": "capacity",
            }
        ), 429
    return jsonify(_public_job(job, include_report=False)), 202


@app.route("/api/v2/jobs/manual", methods=["POST"])
def create_manual_job() -> Response:
    title = str(request.form.get("title") or "").strip()
    price = str(request.form.get("price") or "").strip()
    currency = str(request.form.get("currency") or "EUR").strip()
    source_url = str(request.form.get("source_url") or "").strip()
    manual_text = str(request.form.get("manual_text") or "").strip()
    language = normalize_language(str(request.form.get("language") or "sk"))
    files = request.files.getlist("images")

    if not title and len(manual_text) < 20:
        return jsonify(
            {
                "error": "Doplňte aspoň názov auta alebo podrobnejší text inzerátu.",
                "code": "insufficient_manual_data",
            }
        ), 400
    if source_url and not _valid_optional_url(source_url):
        return jsonify({"error": "Zdrojový odkaz nie je platný.", "code": "invalid_url"}), 400
    if len(files) > MAX_VISION_IMAGES:
        return jsonify(
            {
                "error": f"Nahrajte najviac {MAX_VISION_IMAGES} fotografií.",
                "code": "too_many_images",
            }
        ), 400

    rate_error = _consume_rate_limit()
    if rate_error:
        return rate_error

    _cleanup_old_jobs()
    job = _create_job("manual", source_url, language)
    job_dir = _job_dir(job["id"])
    try:
        listing_dir = _build_manual_listing(
            job_dir,
            title,
            price,
            currency,
            source_url,
            manual_text,
            files,
        )
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(
            {"error": "Manuálne údaje sa nepodarilo bezpečne uložiť.", "code": "upload_failed"}
        ), 400

    if not _start_job(job, source_url=source_url, existing_listing_dir=listing_dir):
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(
            {
                "error": "Kapacita analýz je momentálne naplnená. Skúste požiadavku zopakovať.",
                "code": "capacity",
            }
        ), 429
    return jsonify(_public_job(job, include_report=False)), 202


@app.route("/api/v2/jobs/<job_id>")
def get_job(job_id: str) -> Response:
    job = _read_job(job_id)
    if job is None:
        return jsonify({"error": "Analýza nebola nájdená.", "code": "not_found"}), 404
    return jsonify(_public_job(job))


@app.route("/api/v2/jobs/<job_id>/events")
def job_events(job_id: str) -> Response:
    if _read_job(job_id) is None:
        return jsonify({"error": "Analýza nebola nájdená.", "code": "not_found"}), 404

    @stream_with_context
    def stream():
        last_revision = -1
        last_ping = time.monotonic()
        while True:
            job = _read_job(job_id)
            if job is None:
                payload = {"error": "Analýza už nie je dostupná.", "code": "not_found"}
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break

            revision = int(job.get("revision", 0))
            if revision != last_revision:
                state = _public_job(job)
                event_name = "complete" if job.get("status") == "done" else "failed" if job.get("status") == "failed" else "update"
                yield f"event: {event_name}\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"
                last_revision = revision
                last_ping = time.monotonic()

            if job.get("status") in TERMINAL_STATES:
                break
            if time.monotonic() - last_ping >= 12:
                yield ": keep-alive\n\n"
                last_ping = time.monotonic()
            time.sleep(0.65)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/v2/jobs/<job_id>/report")
def download_report(job_id: str) -> Response:
    job = _read_job(job_id)
    if job is None:
        return jsonify({"error": "Analýza nebola nájdená.", "code": "not_found"}), 404
    if job.get("status") != "done" or not isinstance(job.get("report"), dict):
        return jsonify({"error": "Report ešte nie je hotový.", "code": "not_ready"}), 409
    response = jsonify(job["report"])
    response.headers["Content-Disposition"] = f'attachment; filename="checkni-auto-{job_id[:8]}.json"'
    return response


_recover_interrupted_jobs()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
