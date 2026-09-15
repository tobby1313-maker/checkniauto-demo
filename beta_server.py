"""Free beta HTTP interface. Preparation only; no model SDK or API call path."""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import zipfile
from urllib.parse import quote, urlsplit

import requests
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, stream_with_context
from werkzeug.exceptions import HTTPException

from scrapper_demo.beta_prepare import PreparationError, normalize_url, prepare_assets, scrape

ROOT = Path(__file__).resolve().parent
JOB_RE = re.compile(r"^beta-[a-f0-9]{32}$")


class HubError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class CloudHub:
    def __init__(self, url, token):
        self.url, self.token = str(url or "").rstrip("/"), str(token or "")

    @property
    def configured(self):
        p = urlsplit(self.url)
        return (p.scheme == "https" and bool(p.hostname) and not p.username and not p.password
                and not p.query and not p.fragment and p.path in {"", "/"} and len(self.token) >= 32)

    def call(self, method, path, *, payload=None, data=None, token=None, binary=False):
        if not self.configured:
            raise HubError("Beta úložisko ešte nie je pripojené. Platené AI volania sú vypnuté.")
        if not path.startswith("/"):
            raise ValueError("Absolute hub path required")
        headers = {"Authorization": "Bearer " + (self.token if token is None else token)}
        try:
            response = requests.request(method, self.url + path, headers=headers, json=payload,
                                        data=data, timeout=(5, 30), allow_redirects=False)
        except requests.RequestException:
            raise HubError("Úložisko je dočasne nedostupné. Skús to neskôr; AI API nebolo volané.") from None
        if not 200 <= response.status_code < 300:
            try:
                message = str(response.json().get("error") or "Storage error")[:1000]
            except ValueError:
                message = "Úložisko je dočasne nedostupné."
            raise HubError(message, response.status_code if response.status_code in {400,401,403,404,409,413,429} else 503)
        return response.content if binary else response.json()


def create_beta_app(config=None, *, hub=None):
    app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="")
    app.config.update(MAX_CONTENT_LENGTH=40_000_000,
                      CHECKNI_CLOUD_URL=os.environ.get("CHECKNI_CLOUD_URL", ""),
                      CHECKNI_INGEST_TOKEN=os.environ.get("CHECKNI_INGEST_TOKEN", ""),
                      CHECKNI_BETA_MARKET_SEARCH=os.environ.get("CHECKNI_BETA_MARKET_SEARCH", "true").lower()=="true")
    if config:
        app.config.update(config)
    storage = hub or CloudHub(app.config["CHECKNI_CLOUD_URL"], app.config["CHECKNI_INGEST_TOKEN"])
    app.extensions["beta_hub"] = storage
    slots = threading.BoundedSemaphore(1)

    @app.after_request
    def privacy(response):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = ("private, max-age=3600"
            if request.method=="GET" and re.fullmatch(r"/_beta/jobs/beta-[a-f0-9]{32}/photos/p[0-9]{3}", request.path)
            else "no-store")
        return response

    @app.errorhandler(HubError)
    @app.errorhandler(PreparationError)
    def known_error(exc):
        return jsonify(error=str(exc), ai_api_calls=0, chargeable=False), getattr(exc,"status",400)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error="Požiadavka je neplatná alebo príliš veľká.", code=exc.code), exc.code

    @app.get("/")
    @app.get("/beta-admin")
    @app.get("/analysis/<job_id>")
    def page(job_id=None):
        if job_id and not JOB_RE.fullmatch(job_id):
            return jsonify(error="Analysis not found."), 404
        return send_from_directory(ROOT / "web", "beta.html")

    @app.get("/healthz")
    def health():
        return jsonify(ok=True, mode="chatgpt_beta", ai_api_calls_enabled=False,
                       cloud_configured=bool(storage.configured), version="free-beta-1")

    @app.get("/_beta/config")
    def config_route():
        return jsonify(mode="chatgpt_beta", cloud_configured=bool(storage.configured),
                       ai_api_calls_enabled=False, max_photos=60, max_jobs_per_day=10,
                       max_retained_jobs=50, requires_operator=True)

    @app.get("/_beta/jobs/<job_id>")
    def job(job_id):
        if not JOB_RE.fullmatch(job_id):
            return jsonify(error="Analysis not found."), 404
        return jsonify(storage.call("GET", "/public/jobs/" + job_id))

    @app.get("/_beta/jobs/<job_id>/photos/<photo_id>")
    def photo(job_id, photo_id):
        if not JOB_RE.fullmatch(job_id) or not re.fullmatch(r"p[0-9]{3}", photo_id):
            return jsonify(error="Photo not found."), 404
        data = storage.call("GET", f"/public/jobs/{job_id}/photos/{photo_id}", binary=True)
        return Response(data, mimetype="image/jpeg")

    def sse(payload):
        return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    def submit(manual):
        if not storage.configured:
            raise HubError("Beta úložisko ešte nie je pripojené. Platené AI volania sú vypnuté.")
        data = request.form if manual else request.get_json(silent=True)
        if not isinstance(data, dict) and not manual:
            raise PreparationError("Požiadavka musí obsahovať JSON objekt.")
        language = str(data.get("output_language") or "sk")
        if language not in {"sk","cs","en"}:
            raise PreparationError("Nepodporovaný jazyk.")
        url = str(data.get("source_url" if manual else "url") or "").strip()
        uploads = request.files.getlist("images") if manual else []
        if manual:
            if len(uploads)>60:
                raise PreparationError("Bezplatná beta prijme najviac 60 fotografií.")
            title, description = str(data.get("title") or "").strip(), str(data.get("description") or "").strip()
            if not title or len(title)>500 or len(description)<30 or len(description)>20000 or len(url)>2000:
                raise PreparationError("Doplň názov a popis inzerátu (30 až 20 000 znakov).")
        else:
            url = normalize_url(url)
        if not slots.acquire(blocking=False):
            raise HubError("Práve pripravujeme iný inzerát. Skús to o chvíľu.",429)
        job_id = "beta-" + secrets.token_hex(16)
        temporary_files = None
        try:
            temporary_files = tempfile.TemporaryDirectory(prefix="checkni-beta-")
            if manual:
                # Request-owned upload streams may close before SSE iteration.
                # Copy them while this view still owns the live request.
                folder = Path(temporary_files.name) / "manual"
                (folder / "images").mkdir(parents=True)
                raw = {"title":title,"description":description,"url":url,
                       "photos_count":len(uploads),"parameters":{},"source":"manual"}
                (folder / "raw_data.json").write_text(json.dumps(raw,ensure_ascii=False),encoding="utf-8")
                for number, upload in enumerate(uploads,1):
                    # Never use user-provided filenames or paths.
                    upload.save(folder / "images" / f"{number:03d}.upload")
            storage.call("GET","/v1/ready")
            storage.call("POST","/v1/jobs",payload={"id":job_id,"source_url":url,"language":language})
        except Exception:
            if temporary_files is not None:
                temporary_files.cleanup()
            slots.release()
            raise

        def events():
            queued = False
            try:
                yield sse({"status":"PREPARING","slug":job_id,"message":"Pripravujeme podklady, bez AI API volaní."})
                with temporary_files as temporary:
                    work = Path(temporary)
                    if manual:
                        folder = work / "manual"
                    else:
                        yield sse({"status":"SCRAPING","message":"Načítavame inzerát a fotografie."})
                        folder = scrape(url,work)
                    yield sse({"status":"PREPARING","message":"Pripravujeme očíslované koláže a dostupné trhové podklady."})
                    result = prepare_assets(folder,market=app.config["CHECKNI_BETA_MARKET_SEARCH"])
                    manifest, directory = result["manifest"],result["directory"]
                    yield sse({"status":"UPLOADING","message":"Ukladáme podklady do trvalého úložiska."})
                    for item in manifest["files"]:
                        storage.call("PUT",f"/v1/jobs/{job_id}/assets?name={quote(item['name'],safe='')}",
                                     data=(directory / item["name"]).read_bytes())
                    storage.call("POST",f"/v1/jobs/{job_id}/ready",payload=manifest)
                    queued = True
                    yield sse({"status":"WAITING_FOR_AI","slug":job_id,"message":"Podklady sú uložené. Analýza čaká na spracovanie v ChatGPT."})
                    yield "data: [DONE]\n\n"
            except GeneratorExit:
                if not queued:
                    try: storage.call("POST",f"/v1/jobs/{job_id}/fail",payload={"reason":"Príprava bola prerušená zatvorením spojenia. Vlož inzerát znova."})
                    except HubError: pass
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc,(PreparationError,HubError)) else "Príprava zlyhala. AI API nebolo volané."
                if not queued:
                    try: storage.call("POST",f"/v1/jobs/{job_id}/fail",payload={"reason":message[:1000]})
                    except HubError: pass
                app.logger.warning("Beta preparation failure for %s (%s)", job_id, type(exc).__name__)
                yield sse({"error":message,"slug":job_id,"ai_api_calls":0,"chargeable":False})
            finally:
                temporary_files.cleanup()
                slots.release()
        return Response(stream_with_context(events()),mimetype="text/event-stream",
                        headers={"X-Accel-Buffering":"no","Cache-Control":"no-cache"})

    app.add_url_rule("/api/demo/analyze", "submit_url", lambda:submit(False), methods=["POST"])
    app.add_url_rule("/api/demo/analyze-manual", "submit_manual", lambda:submit(True), methods=["POST"])

    @app.route("/_beta/operator/<path:operation>",methods=["GET","POST"])
    def operator(operation):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if len(token)<32:
            raise HubError("Zadaj beta administračný kľúč.",401)
        if operation == "jobs" and request.method == "GET":
            return jsonify(storage.call("GET","/v1/jobs",token=token))
        match = re.fullmatch(r"jobs/(beta-[a-f0-9]{32})(?:/(claim|complete|fail|bundle))?", operation)
        if not match:
            raise HubError("Endpoint not found.",404)
        job_id, action = match.groups()
        if action == "bundle" and request.method == "GET":
            detail = storage.call("GET",f"/v1/jobs/{job_id}",token=token)
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,"w",compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("analysis.json",json.dumps(detail,ensure_ascii=False,indent=2))
                z.writestr("report-template.json",json.dumps(detail["report_template"],ensure_ascii=False,indent=2))
                z.write(ROOT / "CHATGPT_BETA_INSTRUCTIONS.md","INSTRUCTIONS.md")
                for item in detail["manifest"]["files"]:
                    # Cloudflare validates names; validate again before making a ZIP.
                    name=item["name"]
                    if ".." in name or name.startswith(("/","\\")):
                        raise HubError("Invalid bundle asset.")
                    z.writestr(name,storage.call("GET",f"/v1/jobs/{job_id}/assets?name={quote(name,safe='')}",token=token,binary=True))
            buffer.seek(0)
            return send_file(buffer,mimetype="application/zip",as_attachment=True,download_name=f"{job_id}.zip")
        if action is None and request.method == "GET":
            return jsonify(storage.call("GET",f"/v1/jobs/{job_id}",token=token))
        if action in {"claim","complete","fail"} and request.method == "POST":
            return jsonify(storage.call("POST",f"/v1/jobs/{job_id}/{action}",token=token,payload=request.get_json(silent=True) or {}))
        raise HubError("Method not allowed.",400)

    return app


class BetaRouter:
    """Keep old report/dashboard reads, but route all new analysis work to free beta."""
    def __init__(self, legacy, beta):
        self.legacy, self.beta = legacy, beta

    def __call__(self,environ,start_response):
        path,method = environ.get("PATH_INFO", "/"),environ.get("REQUEST_METHOD","GET")
        beta_route = (path in {"/","/healthz","/beta-admin","/api/demo/analyze","/api/demo/analyze-manual"}
                      or path.startswith("/_beta/") or path.startswith("/analysis/beta-")
                      or path in {"/assets/beta.js", "/assets/beta.css"})
        if beta_route:
            return self.beta(environ,start_response)
        if path.startswith("/api/") and method not in {"GET","HEAD","OPTIONS"}:
            return Response(json.dumps({"error":"Paid AI API operations are disabled in chatgpt_beta mode.","ai_api_calls":0}),
                            status=403,mimetype="application/json")(environ,start_response)
        return self.legacy(environ,start_response)
