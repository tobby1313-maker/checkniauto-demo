# CheckniAuto free beta

The current `v2` default is the **local Render beta**, with temporary SQLite and
files on the existing Render instance. No Cloudflare, Supabase, persistent disk,
or model API credential is required. Data can disappear when Render resets its
filesystem; the operator explicitly accepts that limitation for internal tests.

Follow [README_LOCAL_BETA.md](README_LOCAL_BETA.md) for setup, manual ChatGPT model
selection, the Render-hosted `/mcp` endpoint and ZIP/JSON review fallback.

Preferred manual reviewer: **GPT-6 Pro**, then **GPT-5.6 Sol / Extra High** if the
operator selects it in ChatGPT. The website cannot switch chat models or invoke
a Pro subscription; no paid API fallback is enabled.

The previous optional Cloudflare architecture is preserved in
[README_CLOUDFLARE_BETA.md](README_CLOUDFLARE_BETA.md). It now requires explicit
`CHECKNI_STORAGE_MODE=cloudflare`; do not follow that setup for the local beta.
