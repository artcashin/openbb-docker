# Feedback for OpenBB: "Validate widgets" hard-fails on unconfigured providers

Filed 2026-08-04:
- Finding 1 → https://github.com/OpenBB-finance/OpenBB/issues/7628
- Finding 2 → https://github.com/OpenBB-finance/OpenBB/issues/7629
- Findings 3–5 (Workspace UX) → not filed; Workspace is closed-source, send via
  support@openbb.finance / the in-app feedback form.

Context: self-hosted widget backend built with `openbb-platform-api` (OpenBB
Platform 4.7.2, 442 auto-generated widgets), connected to OpenBB Workspace
(pro.openbb.co, v6.2.1). With **Validate widgets: Yes**, the Connect/Test flow
live-fires every widget endpoint; any failure blocks adding the connection with
a generic "Server error occurred (Status: 500)". Providers without an API key
configured (EIA, BLS in our case) make validation fail every time even though
the backend is healthy.

## Finding 1 — missing provider credential surfaces as a 500 (Platform)

`GET /api/v1/commodity/short_term_energy_outlook?provider=eia` with no
`eia_api_key` configured returns:

```json
{"detail": "Error fetching data from the EIA API -> API_KEY_MISSING -> No api_key was supplied. ..."}
```

as an HTTP **500**. A missing credential is an expected configuration state,
not a server fault.

**Recommendation:** map missing-credential conditions to a structured non-5xx
response — e.g. `400`/`424` with a machine-readable code:

```json
{"detail": {"code": "missing_credentials", "provider": "eia", "credential": "eia_api_key"}}
```

so clients (including Workspace validation) can classify the widget as
"unconfigured" instead of "broken".

## Finding 2 — validation probes omit required params and hit a 500 (both sides)

The validation sweep calls
`GET /api/v1/economy/survey/bls_search?provider=bls&include_extras=false&include_code_map=false`
without the required `category` parameter. The backend returns **500**:

```json
{"detail": "Unexpected Error -> ValidationError -> 1 validation error for BlsSearchQueryParams\ncategory\n  Field required ..."}
```

Two layered issues:

- **Platform:** the pydantic `ValidationError` raised inside the fetcher is
  mapped to `500 Unexpected Error`; parameter-validation failures should map
  to **422** like FastAPI-level validation does.
- **Workspace:** the validator should either supply required params (they are
  declared in widgets.json / OpenAPI) or skip probing widgets whose required
  params have no defaults, rather than firing a request known to be invalid.

## Finding 3 — validation UX and cost (Workspace)

- A single failing widget out of 442 blocks the **Add** button with a generic
  "(Status: 500)" and no indication of which widget failed (a subsequent run
  did enumerate failures, but the first showed only the generic error).
- Validation issues should behave like the existing **App warnings** (e.g.
  "Missing widgets used in tab ..."): warn, list the affected widgets, and let
  the user add the connection anyway — the backend itself is demonstrably
  reachable when widgets.json/apps.json load.
- Live-firing every widget endpoint executes ~440 real data requests against
  metered provider APIs on every Test click. Consider schema-level validation
  by default, with data probes opt-in or sampled.

## Suggestion — let the backend flag unconfigured widgets (openbb-platform-api)

`openbb-platform-api` runs in-process with the Platform and can see which
provider credentials are unset. It could annotate generated widgets
(e.g. `"disabled": true` or `"missingCredentials": ["eia_api_key"]`) or offer
an opt-in flag to exclude such widgets from `widgets.json` entirely. Then
Workspace validation has nothing to trip over, and users see accurate
capability instead of widgets that can never return data.

## Reproduction summary

1. Build a backend with `openbb-platform-api`; leave `eia_api_key` and
   `bls_api_key` unset.
2. Workspace → Connect backend → endpoint URL → Validate widgets: **Yes** →
   Test.
3. Observe generic 500, blocked Add; with Validate: **No**, the same backend
   connects and serves data (verified with the eodhd provider) without issue.
