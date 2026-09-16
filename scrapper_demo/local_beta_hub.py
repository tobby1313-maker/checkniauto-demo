"""Ephemeral SQLite + files adapter, matching the existing beta storage API.

No external storage service, model SDK, API key, scheduled AI or network calls.
Each transaction uses its own connection; admission and leases are atomic.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

from .beta_contracts import (
    ASSET_RE, JOB_RE, MAX_JOB_BYTES, STORAGE_NOTICE, REVIEW_INSTRUCTIONS,
    HubError, encoded, require, report_template, review_policy,
    text, validate_manifest, validate_report,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, status TEXT NOT NULL, source_url TEXT NOT NULL,
 language TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 manifest TEXT, manifest_hash TEXT, report TEXT, report_hash TEXT,
 lease_token TEXT, lease_until INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created_at);
CREATE TABLE IF NOT EXISTS assets (
 job_id TEXT NOT NULL REFERENCES jobs(id), name TEXT NOT NULL,
 bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, ready INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(job_id,name)
);
CREATE TABLE IF NOT EXISTS counters (bucket TEXT PRIMARY KEY, used INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_clients (
 id TEXT PRIMARY KEY, redirect_uris TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
 hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
 challenge TEXT NOT NULL, resource TEXT NOT NULL, scope TEXT NOT NULL,
 expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
 hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, kind TEXT NOT NULL,
 resource TEXT NOT NULL, scope TEXT NOT NULL, expires_at INTEGER NOT NULL
);
"""


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def same_secret(a, b):
    return isinstance(a, str) and isinstance(b, str) and bool(b) and hmac.compare_digest(digest(a), digest(b))


class LocalHub:
    kind = "local_ephemeral"
    persistent = False
    min_token_length = 8
    notice = STORAGE_NOTICE

    def __init__(self, directory, admin_token="", *, clock=time.time):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / "queue.sqlite3"
        self.admin_token = str(admin_token or "")
        self.clock = clock
        with self.connection() as db:
            db.executescript(SCHEMA)
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @property
    def configured(self):
        return self.root.is_dir() and os.access(self.root, os.W_OK)

    @property
    def operator_configured(self):
        return len(self.admin_token) >= self.min_token_length

    def authorize(self, token):
        require(self.operator_configured, "Nastav CHECKNI_OPERATOR_TOKEN alebo existujúci ADMIN_DASHBOARD_TOKEN na Renderi.", 503)
        require(same_secret(token, self.admin_token), "Nesprávny administračný kľúč.", 401)

    def now(self):
        return int(self.clock())

    def quota(self, bucket, limit):
        with self.connection(True) as db:
            row = db.execute("INSERT INTO counters(bucket,used) VALUES(?,1) ON CONFLICT(bucket) DO UPDATE SET used=used+1 WHERE used < ? RETURNING used", (bucket, limit)).fetchone()
            require(row is not None, "Príliš veľa požiadaviek. Skús to neskôr.", 429)

    def signing_key(self):
        with self.connection(True) as db:
            db.execute("INSERT OR IGNORE INTO settings VALUES('oauth_signing_key',?)", (secrets.token_urlsafe(48),))
            return db.execute("SELECT value FROM settings WHERE name='oauth_signing_key'").fetchone()[0]

    def get_job(self, job_id, db=None):
        require(isinstance(job_id, str) and JOB_RE.fullmatch(job_id), "Analýza sa nenašla.", 404)
        if db is None:
            with self.connection() as conn:
                return self.get_job(job_id, conn)
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        require(row is not None, "Analýza sa nenašla. Po reštarte Renderu mohli dočasné údaje zmiznúť; vlož inzerát znova.", 404)
        return dict(row)

    def public_job(self, job):
        m = json.loads(job["manifest"]) if job["manifest"] else {}
        stale = job["status"] == "PREPARING" and job["updated_at"] < self.now() - 900
        return {"id": job["id"], "status": "PREPARATION_INTERRUPTED" if stale else job["status"],
                "title": job["title"], "created_at": job["created_at"], "updated_at": job["updated_at"],
                "language": job["language"], "source_url": job["source_url"],
                "error": "Príprava bola prerušená; vlož inzerát znova." if stale else job["error"],
                "photos": m.get("photos", []), "report": json.loads(job["report"]) if job["report"] else None,
                "warnings": [*m.get("warnings", []), self.notice],
                "storage_mode": self.kind, "storage_persistent": False,
                "chargeable": False, "ai_api_calls": 0}

    def create_job(self, p):
        require(isinstance(p, dict) and isinstance(p.get("id"), str) and JOB_RE.fullmatch(p["id"]), "Invalid analysis ID.")
        require(p.get("language") in {"sk", "cs", "en"} and isinstance(p.get("source_url"), str) and len(p["source_url"]) <= 2000, "Invalid job input.")
        with self.connection(True) as db:
            existing = db.execute("SELECT * FROM jobs WHERE id=?", (p["id"],)).fetchone()
            if existing:
                require(existing["source_url"] == p["source_url"] and existing["language"] == p["language"], "Idempotency conflict.", 409)
                return self.public_job(dict(existing))
            total, recent = db.execute("SELECT count(*), coalesce(sum(created_at>=?),0) FROM jobs", (self.now()-86400,)).fetchone()
            require(total < 50 and recent < 10, "Kapacita bety je naplnená (10 analýz/deň alebo 50 uložených). AI API nebolo volané.", 429)
            db.execute("INSERT INTO jobs(id,status,source_url,language,created_at,updated_at) VALUES(?,'PREPARING',?,?,?,?)", (p["id"], p["source_url"], p["language"], self.now(), self.now()))
            return self.public_job(self.get_job(p["id"], db))

    def asset_path(self, job_id, name):
        require(isinstance(job_id, str) and JOB_RE.fullmatch(job_id) and isinstance(name, str) and ASSET_RE.fullmatch(name), "Invalid asset path.")
        path = (self.root / "jobs" / job_id / name).resolve()
        require(path.is_relative_to(self.root / "jobs" / job_id), "Invalid asset path.")
        return path

    def upload_asset(self, job_id, name, data):
        path = self.asset_path(job_id, name)
        require(isinstance(data, bytes) and 0 < len(data) <= 4_000_000, "Asset exceeds the beta size limit.", 413)
        hashed = digest(data)
        with self.connection(True) as db:
            require(self.get_job(job_id, db)["status"] == "PREPARING", "Source files are immutable after preparation.", 409)
            old = db.execute("SELECT * FROM assets WHERE job_id=? AND name=?", (job_id, name)).fetchone()
            if old:
                require(old["sha256"] == hashed and old["bytes"] == len(data), "Asset content conflict.", 409)
                if old["ready"] and path.is_file():
                    return {"name": name, "bytes": len(data), "sha256": hashed}
            else:
                count, size = db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM assets WHERE job_id=?", (job_id,)).fetchone()
                require(count < 100 and size + len(data) <= MAX_JOB_BYTES, "Analysis exceeds the 40 MB / 100 asset limit.", 413)
                db.execute("INSERT INTO assets(job_id,name,bytes,sha256) VALUES(?,?,?,?)", (job_id, name, len(data), hashed))
        # Reserve the budget first, so a failed file write never creates uncounted capacity.
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
                temp = Path(f.name)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        with self.connection(True) as db:
            db.execute("UPDATE assets SET ready=1 WHERE job_id=? AND name=? AND sha256=?", (job_id, name, hashed))
        return {"name": name, "bytes": len(data), "sha256": hashed}

    def read_asset(self, job_id, name):
        path = self.asset_path(job_id, name)
        with self.connection() as db:
            row = db.execute("SELECT bytes,sha256 FROM assets WHERE job_id=? AND name=? AND ready=1", (job_id, name)).fetchone()
        require(row is not None and path.is_file(), "Súbor sa nenašiel alebo už expiroval.", 404)
        data = path.read_bytes()
        require(len(data) == row["bytes"] and digest(data) == row["sha256"], "Stored asset is incomplete.", 409)
        return data

    def ready(self, job_id, manifest):
        validate_manifest(manifest)
        serialized = encoded(manifest)
        with self.connection(True) as db:
            job = self.get_job(job_id, db)
            if job["manifest_hash"] == digest(serialized):
                return self.public_job(job)
            require(job["status"] == "PREPARING", "Analysis already prepared.", 409)
            assets = {r["name"]: dict(r) for r in db.execute("SELECT * FROM assets WHERE job_id=?", (job_id,))}
            require(len(assets) == len(manifest["files"]), "Upload incomplete; analysis was not queued.", 409)
            for f in manifest["files"]:
                a = assets.get(f["name"], {})
                require(a.get("ready") == 1 and a.get("bytes") == f["bytes"] and a.get("sha256") == f["sha256"], "Upload incomplete; analysis was not queued.", 409)
                require(digest(self.asset_path(job_id, f["name"]).read_bytes()) == f["sha256"], "Stored upload is incomplete.", 409)
            db.execute("UPDATE jobs SET status='WAITING_FOR_AI',manifest=?,manifest_hash=?,title=?,updated_at=? WHERE id=?", (serialized, digest(serialized), manifest["title"], self.now(), job_id))
            return self.public_job(self.get_job(job_id, db))

    def list_jobs(self):
        with self.connection() as db:
            jobs = [dict(r) for r in db.execute("SELECT id,status,title,created_at,updated_at,lease_until FROM jobs ORDER BY created_at,id LIMIT 50")]
        return {"jobs": jobs, "limits": {"daily": 10, "retained": 50, "bytes_per_job": MAX_JOB_BYTES}, "review_policy": review_policy(), "storage_notice": self.notice}

    def operator_job(self, job_id):
        job = self.get_job(job_id)
        require(job["manifest"], "The listing is not ready for review.", 409)
        m = json.loads(job["manifest"])
        return {**self.public_job(job), "manifest": m,
                "raw_listing": json.loads(self.read_asset(job_id, "raw_data.json")),
                "report_template": report_template(job_id, m),
                "review_policy": review_policy(), "review_instructions": REVIEW_INSTRUCTIONS}

    def claim(self, job_id):
        with self.connection(True) as db:
            self.get_job(job_id, db)
            token = secrets.token_urlsafe(48)
            row = db.execute("UPDATE jobs SET status='PROCESSING',lease_token=?,lease_until=?,updated_at=? WHERE id=? AND (status='WAITING_FOR_AI' OR (status='PROCESSING' AND lease_until<?)) RETURNING id,lease_token,lease_until", (token, self.now()+5400, self.now(), job_id, self.now())).fetchone()
            require(row is not None, "Analysis is not waiting, or another review has a valid lease.", 409)
            return dict(row)

    def complete(self, job_id, token, report):
        with self.connection(True) as db:
            job = self.get_job(job_id, db)
            require(job["manifest"], "Source data is not ready.", 409)
            validate_report(report, job_id, json.loads(job["manifest"]))
            data = encoded(report)
            require(same_secret(token, job["lease_token"]), "Review lease is invalid.", 409)
            if job["status"] == "DONE" and job["report_hash"] == digest(data):
                return self.public_job(job)
            require(job["status"] == "PROCESSING" and job["lease_until"] >= self.now(), "Review lease expired or report already completed.", 409)
            db.execute("UPDATE jobs SET status='DONE',report=?,report_hash=?,updated_at=? WHERE id=?", (data, digest(data), self.now(), job_id))
            return self.public_job(self.get_job(job_id, db))

    def fail(self, job_id, reason, token=None, *, ingest=False):
        require(text(reason, 1000), "A short failure reason is required.")
        with self.connection(True) as db:
            job = self.get_job(job_id, db)
            allowed = (job["status"] == "PREPARING" if ingest else
                       job["status"] == "PROCESSING" and same_secret(token, job["lease_token"]) and job["lease_until"] >= self.now())
            require(allowed, "Analysis cannot be failed in its current state.", 409)
            db.execute("UPDATE jobs SET status='FAILED',error=?,updated_at=? WHERE id=?", (reason, self.now(), job_id))
            return self.public_job(self.get_job(job_id, db))

    def call(self, method, path, *, payload=None, data=None, token=None, binary=False):
        """In-process compatibility interface. Not an unauthenticated HTTP endpoint."""
        del binary
        p = urlsplit(path)
        parts = p.path.strip("/").split("/")
        if path == "/v1/ready" and method == "GET":
            with self.connection(True) as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('last_ready',?)", (str(self.now()),))
            return {"ready": self.configured, "persistent": False}
        if parts[:2] == ["public", "jobs"] and len(parts) in {3, 5} and method == "GET":
            job = self.get_job(parts[2])
            if len(parts) == 3:
                return self.public_job(job)
            require(parts[3] == "photos", "Endpoint not found.", 404)
            m = json.loads(job["manifest"]) if job["manifest"] else {}
            photo = next((x for x in m.get("photos", []) if x["id"] == parts[4]), {})
            require(photo.get("status") == "available", "Photo unavailable.", 404)
            return self.read_asset(job["id"], photo["filename"])
        if token is not None:
            self.authorize(token)
        if path == "/v1/jobs":
            if method == "POST" and token is None:
                return self.create_job(payload)
            if method == "GET":
                self.authorize(token)
                return self.list_jobs()
        require(parts[:2] == ["v1", "jobs"] and len(parts) in {3, 4}, "Endpoint not found.", 404)
        job_id = parts[2]
        action = parts[3] if len(parts) == 4 else ""
        if action == "assets":
            name = parse_qs(p.query).get("name", [""])[0]
            if method == "PUT" and token is None:
                return self.upload_asset(job_id, name, data)
            self.authorize(token)
            require(method == "GET", "Method not allowed.", 405)
            return self.read_asset(job_id, name)
        if action == "ready" and method == "POST" and token is None:
            return self.ready(job_id, payload)
        require(payload is None or isinstance(payload, dict), "Invalid request body.")
        payload = payload or {}
        if action == "fail" and method == "POST":
            return self.fail(job_id, payload.get("reason"), payload.get("lease_token"), ingest=token is None)
        self.authorize(token)
        if not action and method == "GET":
            return self.operator_job(job_id)
        if action == "claim" and method == "POST":
            return self.claim(job_id)
        if action == "complete" and method == "POST":
            return self.complete(job_id, payload.get("lease_token"), payload.get("report"))
        raise HubError("Endpoint not found.", 404)
