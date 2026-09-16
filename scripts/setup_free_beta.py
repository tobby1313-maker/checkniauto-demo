#!/usr/bin/env python3
"""Interactive, idempotent Cloudflare bootstrap. Never enables a paid plan/subscription.
Run locally after reviewing README_FREE_BETA.md. Uses the installed requests library.
"""
from __future__ import annotations
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import requests

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".config" / "checkniauto" / "beta-credentials.json"
API = "https://api.cloudflare.com/client/v4"


def save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(state,file,ensure_ascii=False,indent=2)
    os.replace(temporary,STATE)
    os.chmod(STATE,0o600)


def main():
    print("CheckniAuto free beta setup")
    print("Keep Cloudflare Workers on FREE. R2 must already be activated in your account.")
    print("R2 charges above its free allowances. This app caps its own use, not other applications in your account.")
    print("This script will NOT enable a paid plan or complete the R2 checkout for you.")
    if input("Type SETUP to create/update only this beta's resources: ").strip() != "SETUP":
        return
    token = os.environ.get("CLOUDFLARE_API_TOKEN") or getpass.getpass("Cloudflare API token (not saved): ")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or input("Cloudflare Account ID: ").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}",account):
        raise ValueError("Invalid account ID")
    session = requests.Session()
    session.headers.update(Authorization="Bearer "+token)
    base = f"/accounts/{account}"

    def call(method,path,**kwargs):
        response = session.request(method, API + path, timeout=(10,90),**kwargs)
        try: data=response.json()
        except ValueError: raise RuntimeError(f"Cloudflare returned HTTP {response.status_code}") from None
        if not response.ok or data.get("success") is not True:
            # Cloudflare error messages only; never print request headers or tokens.
            message="; ".join(str(x.get("message",x)) for x in data.get("errors",[]))
            raise RuntimeError(f"Cloudflare HTTP {response.status_code}: {message[:600]}")
        return data.get("result")

    state=json.loads(STATE.read_text()) if STATE.exists() else {}
    if state and state.get("account_id")!=account:
        raise ValueError(f"Saved setup uses another account. Review {STATE}; do not overwrite it blindly.")
    subdomain=call("GET",base+"/workers/subdomain").get("subdomain")
    if not subdomain:
        raise RuntimeError("Open Cloudflare Workers & Pages once and choose a workers.dev subdomain, then rerun.")
    name=state.get("worker_name") or "checkni-auto-beta"
    if not state:
        existing=session.get(API+base+f"/workers/scripts/{name}/settings",timeout=20)
        if existing.ok:
            raise RuntimeError("A Worker with this name already exists and is not managed by this setup. Refusing to overwrite it.")
        if existing.status_code!=404:
            raise RuntimeError("Cannot check whether the Worker already exists; verify API permissions.")
        state={"account_id":account,"worker_name":name,"render_token":secrets.token_urlsafe(48),
               "admin_token":secrets.token_urlsafe(48),"signing_key":secrets.token_urlsafe(48)}
        save(state)
    origin=f"https://{name}.{subdomain}.workers.dev"
    state["cloud_url"]=origin
    if not state.get("database_id"):
        databases=call("GET",base+"/d1/database",params={"name":name})
        matching=[d for d in databases if d.get("name")==name]
        db=matching[0] if matching else call("POST",base+"/d1/database",json={"name":name})
        state["database_id"]=db["uuid"]
        save(state)
    schema=(ROOT / "cloudflare/schema.sql").read_text(encoding="utf-8")
    call("POST",base+f"/d1/database/{state['database_id']}/query",json={"sql":schema})
    # A missing R2 subscription fails here; it is never silently activated.
    buckets=call("GET",base+"/r2/buckets")
    if not any(b.get("name")==name for b in buckets.get("buckets",[])):
        call("POST",base+"/r2/buckets",json={"name":name})
    state["bucket_name"]=name
    metadata={"main_module":"worker.mjs","compatibility_date":"2025-06-18","compatibility_flags":["nodejs_compat"],
              "bindings":[
                  {"name":"DB","type":"d1","id":state["database_id"]},
                  {"name":"BUCKET","type":"r2_bucket","bucket_name":name},
                  {"name":"PUBLIC_ORIGIN","type":"plain_text","text":origin},
                  {"name":"MCP_READ_ONLY","type":"plain_text","text":"false"},
                  *({"name":binding,"type":"secret_text","text":state[key]} for binding,key in
                    [("RENDER_TOKEN","render_token"),("ADMIN_TOKEN","admin_token"),("SIGNING_KEY","signing_key")])
              ]}
    files={"metadata":(None,json.dumps(metadata),"application/json")}
    for module in ["worker.mjs","store.mjs","oauth.mjs"]:
        files[module]=(module,(ROOT / "cloudflare" / module).read_bytes(),"application/javascript+module")
    call("PUT",base+f"/workers/scripts/{name}",files=files)
    call("POST",base+f"/workers/scripts/{name}/subdomain",json={"enabled":True})
    call("PUT",base+f"/workers/scripts/{name}/schedules",json=[{"cron":"15 0 * * *"}])
    save(state)
    # Ready verifies bindings; an actual tiny object round trip is also checked below.
    headers={"Authorization":"Bearer "+state["render_token"]}
    r=requests.get(origin+"/v1/ready",headers=headers,timeout=30)
    r.raise_for_status()
    print("\nWorker deployed:",origin)
    print("MCP endpoint:",origin+"/mcp")
    print("Secrets are stored ONLY on this computer in:",STATE)
    print("Set these Render environment variables using values from that file:")
    print("  CHECKNI_CLOUD_URL = cloud_url")
    print("  CHECKNI_INGEST_TOKEN = render_token")
    print("  CHECKNI_AI_MODE = chatgpt_beta")
    print("Use admin_token to sign in at /beta-admin and during the MCP OAuth connection.")
    print("Do not paste this credentials file into a chat or commit it to GitHub.")
    print("Next: connect the MCP endpoint in ChatGPT, then test one real listing end to end.")


if __name__=="__main__":
    try: main()
    except (ValueError,RuntimeError,requests.RequestException) as exc:
        print("Setup stopped:",str(exc),file=sys.stderr)
        raise SystemExit(1)
