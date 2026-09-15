# CheckniAuto: free, operator-driven beta

This version prepares listing data without calling Gemini, OpenAI, Grok or
OpenRouter. A human operator starts the review in ChatGPT. The website cannot
invoke a ChatGPT subscription or run an unattended Pro worker.

## State and scope

The implementation is in the Render deployment branch `v2`. The default mode is
`chatgpt_beta`. Cloudflare resources and the ChatGPT connection still need to be
set up in the owner's accounts. A code deploy alone does not create them.

```
Render: URL/manual listing → scrape → all photos → labelled 2x2 collages
                      → optional bounded SK/CZ search cards
                      → upload every asset → WAITING_FOR_AI
Cloudflare: D1 queue + private R2 files + authenticated MCP endpoint
ChatGPT: operator prompt → read sources/images → claim → validate/publish
Render: /analysis/beta-<random-id> → report + complete available gallery
```

The existing API-provider implementation is preserved for an explicit rollback,
but new beta analyses do not enter it. Previous report/dashboard GET routes stay
available. Their older locally stored data is NOT migrated automatically.

## Free allowances are conditional, not an unlimited free service

Use **Cloudflare Workers Free**, one D1 database and a **Standard R2** bucket.
R2 requires account activation/checkout and can bill above its free allowance.
The setup script never activates that subscription or upgrades any paid plan.
Review the checkout in your own account first. Without activating R2, this
particular storage backend cannot run. No Render persistent disk is required.

Application admission limits are intentionally much smaller than the allowances:

| Guard | Limit |
| --- | ---: |
| Accepted jobs | 10 in a rolling 24-hour window |
| Retained jobs, including failed ones | 50 |
| Assets per job | 40 MB and 100 files |
| Photos per job | 60, with explicit rejection above the limit |
| Worker requests | 5,000 per UTC day |
| Local preprocessing | 1 at a time |
| Review lease | 90 minutes |

The retained asset budget is at most 2 GB, plus tiny metadata/probe overhead.
R2 usage is gated through the Worker; do not enable `r2.dev` or a public bucket.
The service pauses intake at capacity rather than deleting photographs or
silently falling back to a paid model. There is no automatic photo/report purge
in this beta. Review storage and export needed jobs before a future retention
change. Account-wide usage by other applications is outside these guards.
Workers Free CPU/request limits, D1 limits and Render Free sleeping/restarts still
apply. This is not an uptime or zero-bill guarantee for the entire account.

Current provider documentation (review before activating):
- https://developers.cloudflare.com/r2/pricing/
- https://developers.cloudflare.com/r2/get-started/
- https://developers.cloudflare.com/d1/platform/pricing/
- https://developers.cloudflare.com/workers/platform/limits/

## One-time Cloudflare setup

1. Sign in to Cloudflare, keep Workers on the Free plan, choose a `workers.dev`
   subdomain under Workers & Pages, and activate R2 only after reviewing checkout.
2. Create an account-scoped Cloudflare API token with the permissions needed to
   manage Workers scripts, D1 databases and R2 buckets. Typical permission names:
   **Workers Scripts: Edit**, **D1: Edit**, **Workers R2 Storage: Edit**. Account
   Workers settings/subdomain access may also be required by your account policy.
   Restrict it to this account and revoke it after setup if not needed further.
3. Clone/download branch `v2` to your computer. With Python and `requests` installed:

   ```sh
   python -m pip install requests
   python scripts/setup_free_beta.py
   ```

   The script prompts for your Account ID and privately for the API token.
   It creates one database, the schema, one private bucket, and one Worker.
   It refuses to overwrite an unrelated Worker with the same name. Rerunning
   with its saved state updates this installation without replacing its keys.
4. Three randomly generated secrets and resource identifiers are written to:

   ```text
   ~/.config/checkniauto/beta-credentials.json
   ```

   Keep this file private and backed up. It is not uploaded to GitHub. The
   Cloudflare API token itself is not saved. Do not paste credentials into chat.
   The script uses permission-restricted files on systems that support them;
   additionally secure the containing user account/directory on Windows.
5. Verify the script's `/v1/ready` test succeeds. It checks D1 and does a tiny R2
   write/read/delete round trip. If it fails, do not enable intake yet.

`cloudflare/wrangler.jsonc` is an alternative deployment reference, not a second
setup you must run. Do not deploy its placeholder IDs or public origin unchanged.
The Worker uses native APIs and ES modules, without a paid service dependency.

## Render configuration

Keep the existing `web_server:app` start command and deployment branch **v2**.
Set these environment variables from the local setup file:

| Render setting | Local JSON value |
| --- | --- |
| `CHECKNI_CLOUD_URL` | `cloud_url` |
| `CHECKNI_INGEST_TOKEN` | `render_token` |
| `CHECKNI_AI_MODE` | literal `chatgpt_beta` |

Optional: `CHECKNI_BETA_MARKET_SEARCH=false` disables the two direct Bazoš searches.
No new AI API key is required. Existing model keys are unused by beta preparation.
Missing cloud settings produce a clear setup message and reject intake before any
scraper/provider work. `/healthz` remains healthy for deployment and reports
`cloud_configured: false` separately; it is not a remote-storage guarantee.

Do not copy the admin token into the ingestion token field. They have separate
permissions. The ingestion credential cannot claim jobs or publish reports.

## Connect to ChatGPT Pro

The MCP endpoint is the **Cloudflare Worker** URL plus `/mcp`, not the Render URL.
This keeps MCP reads independent of Render cold starts.

Enable developer mode in ChatGPT web and create a custom MCP plugin/app using
that URL and OAuth. Depending on the interface, developer mode is under Security
and login or Apps advanced settings. The service supports dynamic registration,
PKCE S256, audience-bound access tokens and rotating refresh tokens. During the
CheckniAuto authorization form, enter the setup file's **admin_token**, not your
ChatGPT password or a model API key.

The OAuth callback allowlist supports the documented ChatGPT stable callback and
callback-ID form. Discovery returns tools publicly but all job/image tools require
valid OAuth access. No analysis listing or raw snapshot is public without it.

OpenAI's developer documentation and Help Center have given different eligibility
information for full write tools on Pro. Treat the live account connection test
as required, not assumed. If your account only permits read tools, set the Worker
`MCP_READ_ONLY=true` and use the manual report import below. A successful local
OAuth test does not prove your ChatGPT account can connect or execute write tools.

Documentation:
- https://developers.openai.com/api/docs/guides/developer-mode
- https://developers.openai.com/plugins/build/auth
- https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt

Seven tools, with write actions explicitly marked:

```text
checkniauto_list_pending         read, no reservation
checkniauto_get_analysis         read snapshot, manifest and report template
checkniauto_get_collage          read actual image content plus photo mapping
checkniauto_get_photo            read actual individual image content
checkniauto_claim_analysis       write, 90-minute review lease
checkniauto_complete_analysis    write, validated customer-visible report
checkniauto_fail_analysis        write, short failure explanation
```

Start with one job:

> Spracuj ďalšiu čakajúcu CheckniAuto analýzu. Najprv ju prevezmi. Prečítaj
> pôvodný inzerát, pozri všetky prehľadové koláže a podľa potreby detaily.
> Rozlišuj tvrdenia predajcu, viditeľné zistenia a externé dôkazy. Neoverené
> údaje neprikrášľuj. Ulož report len cez validovaný complete nástroj.

Give the operator `CHATGPT_BETA_INSTRUCTIONS.md` as the review instructions.
A ChatGPT prompt is still required for each review/batch. No browser automation,
ChatGPT session-cookie reuse, polling of a Pro chat, or subscription-to-API bridge
is used. The existing Pro subscription is a separate cost already borne by you.

## Manual fallback without MCP write access

1. Open `/beta-admin` and enter **admin_token**. The page keeps it only in memory
   for the current tab, never in localStorage or a URL.
2. Open a waiting job and select **Prevziať analýzu na 90 minút**.
3. Download its ZIP bundle and upload it in ChatGPT. Ask it to follow
   `INSTRUCTIONS.md` and return a completed `report-template.json`.
4. Paste the completed JSON into the still-open admin tab and submit.
5. The same backend validation and lease checks apply. An unchanged template,
   invalid photo IDs, missing inspection labels or invented source references
   are rejected. A lost/expired review lease can be reclaimed after expiry.

This path does not require the website to call any model API either.

## Evidence and gallery semantics

- Every available listing photo is preserved in the final gallery, including
  visually similar photos. All available photos appear in labelled 2x2 sheets.
- Stored photos are EXIF-stripped JPEGs with a maximum 1600 px long side, not
  archival copies of the camera originals. The manifest records both dimensions.
- Unreadable/download-failed entries remain as explicit placeholders. Missing
  earlier photos do not shift known photo numbers.
- Preparing a collage does **not** mean AI inspected it. Initial status is always
  `not_inspected`; the completed report declares `overview` or `detail` only for
  images actually reviewed.
- Direct market scraping returns **unverified search-card candidates**. It is
  not an independent vehicle-detail check, verified median or a VIN report.
  When no candidates are available, review continues with an explicit gap.
- The server validates report structure, source-ID consistency and photo-ID
  consistency. It cannot prove a human/model actually viewed a photo or that
  an external source supports a sentence; the operator must verify that.
- Source text/images are untrusted input. Embedded requests to reveal secrets,
  change instructions or modify another job must be ignored.

## Durability, access and limitations

D1 is authoritative for state and the completed report; R2 holds prepared assets.
`WAITING_FOR_AI` is set only after every manifest asset was uploaded and checked.
A later Render restart does not delete those cloud objects. Processing interrupted
**before** that point does not auto-resume: after 15 minutes a stale PREPARING job
shows `PREPARATION_INTERRUPTED`, and the operator/user must submit it again.

Public report/photo access uses an unguessable 128-bit job link, not customer
accounts. Anyone holding that link can read it. Keep it private. There is no
public index; raw snapshots, queues and review tools require operator access.
The public pages are noindex. Do not accept sensitive documents in this beta.

Reports cannot overwrite a different completed report; identical completion
retries are idempotent. Concurrent reviews require one valid lease. Admission
counts failed jobs too, intentionally conservative for cost control.

## Tests and rollback

```sh
python -m unittest -v test_free_beta test_beta_preparation
node --test cloudflare/worker.test.mjs
node --check web/assets/beta.js
```

Worker tests execute real SQLite statements with a fake private object store,
including queue publication, incomplete uploads, limits, concurrency, report
validation, access separation, OAuth, image content and MCP read/write behavior.
CI also runs the existing scraper/photo/URL regressions and entrypoint smoke test.
No real API credentials or private listing bundles are used in those tests.
Cloudflare provisioning, live ChatGPT OAuth/write access, and one real end-to-end
job must still be tested in the owner's accounts after setup.

Rollback is explicit: `CHECKNI_AI_MODE=legacy_api` restores old new-analysis
routes, and **may incur AI API charges** if keys are configured. There is never an
automatic paid fallback when storage, OAuth or review fails.
