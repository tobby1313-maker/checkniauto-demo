# Buyer Guide V2: model knowledge plus the actual listing

This change targets the local Render/ChatGPT beta on deployment branch `v2`.
No storage migration, OAuth change, new service or paid AI fallback is required.

## What changes

New templates use report `schema_version: 2` with a required `buyer_guide` containing
nine structured research sections: identity, configuration verdict, engine,
transmission, drivetrain, owner experiences, model risks, buying checks and useful
ownership context. The original top-level verdict still concerns the particular
advertised vehicle. A model's reputation and the condition of one car are not the
same conclusion.

MCP initialization, `get_analysis` instructions and the website's copyable prompt
now explicitly request applicable model research using available conversation web
tools. The backend does not conduct or charge for an automatic AI search.
The ZIP instruction file and manual report template follow the same contract.

The UI shows two verdict cards, then the model guide and then listing/photo findings.
All available photos remain in the gallery with actual inspection labels. Sources
show their type, access date and configuration applicability. Text is rendered via
DOM textContent, not generated HTML.

## Evidence and completeness

The pure Python `scrapper_demo/buyer_guide.py` is the schema/validation source for
new MCP reports. No new model SDK or runtime dependency is introduced.

- RESEARCHED sections need non-listing sources and substantive content.
- LIMITED/UNAVAILABLE sections need specific gaps. Unavailable content has LOW
  confidence and no invented factual items. Empty work is not successful research.
- Repeated owner experiences need at least two distinct owner-source URLs; tracking
  links or fragments do not multiply evidence. Independence and applicability still
  require human/model judgement. Source types alone do not establish factual truth.
- Fixed maintenance intervals require manufacturer/regulatory source references.
- Risk entries distinguish model evidence from the unverified state of this car.
- Optional cost ranges must be sourced, scoped, dated and numerically valid.
- Cited images must actually be labelled inspected in this report.

A job's DONE status means its validated report was saved. It does not mean all
research succeeded. The UI derives a separate coverage label from all nine section
states. Coverage is not a reliability score or probability of a fault.

The validator checks structure, enums, reference consistency and source categories;
it does not fetch pages or prove that a cited source is true, independently authored
or actually relevant. The review instructions require the reviewer to do that work.

## Compatibility and first test

V1 reports and JSON imports remain supported and visible as legacy/listing-only.
They are not silently augmented, regraded or overwritten. New templates are V2.
The optional historical Cloudflare backend still serves its V1 contract; this guide
update is for the currently selected local beta backend, not a cloud migration.

After deploying, reload the website. Refresh the custom app's tool definitions so
`checkniauto_complete_analysis` advertises schema_version 2 and buyer_guide. OAuth
client ID, callbacks, credentials and MCP URL are unchanged. Reconnect only if
Render has cleared its temporary tokens, not to change any authorization rules.

Submit a new listing and use its new copyable ChatGPT prompt. The default template
starts with UNAVAILABLE research, so a genuinely missing web tool yields an honest
partial report rather than an invented engine opinion. When the tool schema is
cached, use the V2 ZIP/JSON manual-import path until it is refreshed.

Render may discard the previous jobs/reports on deploy. This is the accepted
limitation of the temporary beta, not data migration by this update. Export anything
needed before deployment.

## Tests

```sh
python -m unittest -v test_buyer_guide test_buyer_guide_integration
CHECKNI_BROWSER_TESTS=1 python -m unittest -v test_buyer_guide_browser
```

Browser tests use the actual production HTML/CSS/JS with explicitly injected data
and fetch/location collaborators, offline. They cover mobile/desktop width, legacy
rendering, partial coverage, all nine sections, two verdicts and HTML injection.
Integration tests use the actual local queue, Flask routes and OAuth/MCP transport.
All fixtures are synthetic and do not diagnose any real vehicle. Existing OAuth,
photo, URL and legacy beta regression tests must still pass before deployment.
