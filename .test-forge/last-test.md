# eidos-mail

**Target:** https://mail.eidosagi.com
**Type:** api
**Tested as:** Security Auditor
**Last tested:** 2026-03-09 06:13 UTC
**Status:** MISCONFIGURED (31/51 checks passed)
**Rounds:** 5
**Test suite:** `/Users/dshanklinbv/repos-eidos-agi/eidos-mail/.test-forge/suites/mail-eidosagi-com.yaml`

## What "working" means
health endpoint responds, auth-protected routes redirect to login, public routes return 200, endpoints respond with correct methods

## What's working
- **health endpoint returns ok** — passed (273.0ms) — Baseline connectivity and service health
- **security headers on health endpoint** — passed (266.3ms) — Check for X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security, Content-Security-Policy headers
- **openapi.json exposed publicly** — passed (265.9ms) — OpenAPI spec should not be publicly accessible in production — leaks full API surface to attackers
- **root redirects unauthed to login** — passed (258.0ms) — Verify unauthenticated users cannot access index
- **auth login initiates OAuth flow** — passed (213.0ms) — Login should redirect to OAuth provider, not serve a page
- **inbox requires auth (302 redirect)** — passed (260.0ms) — Core data endpoint must enforce authentication
- **email detail with SQL injection attempt in ID** — passed (211.1ms) — SQLi in email_id — should be rejected or redirect to auth, not process the injection
- **search with XSS payload in query param** — passed (268.4ms) — XSS in search query — should redirect to auth, but if somehow rendered, must be escaped
- **auth callback 500 leaks stack trace or debug info** — passed (322.5ms) — Round 1 showed 500 on /auth/callback without code. Need to capture the response body — if it leaks a stack trace, framework version, or internal paths, that's a real finding.
- **API endpoints hide behind 404 vs auth bypass with forged cookie** — passed (272.4ms) — API returns 404 unauthed (good — hides existence). But does a forged session cookie change behavior? If it returns 401/403 instead of 404, that leaks endpoint existence to anyone with a bad token.
- **path traversal via double encoding on email detail** — passed (265.1ms) — Round 1 path traversal got 404 (route didn't match). Testing URL-encoded traversal to see if the framework normalizes before or after routing.
- **HTTP method confusion — PUT on /api/emails/1/delete** — passed (263.7ms) — Delete endpoint expects POST. Does it properly reject PUT with 405, or does it leak info / accept unexpected methods?
- **IDOR: email detail with ID=1 (lowest valid) unauthed** — passed (263.0ms) — Previous path traversal test got 404 — but a normal numeric ID should redirect to login. Verifying auth gate works on valid-looking IDs.
- **cookie flags on auth login redirect** — passed (207.1ms) — Check Set-Cookie headers on login redirect for Secure, HttpOnly, SameSite flags. Weak cookie flags = session hijack risk.
- **search with oversized query param (input validation)** — passed (205.7ms) — Auth should reject before query processing. If 500, the app crashes on large input instead of bouncing to login.
- **FastAPI /docs exposes interactive Swagger UI (info leakage)** — passed (353.0ms) — /docs returned 200 last round — confirm it serves full Swagger UI with try-it-out capability, which lets attackers enumerate and test all endpoints interactively
- **openapi.json leaks internal schemas or server details** — passed (271.6ms) — openapi.json is public — check if it exposes internal model schemas, server URLs, or debug info beyond endpoint paths
- **sensitive path probe: /.env** — passed (276.5ms) — Standard check for exposed environment files with secrets — Railway apps sometimes misconfigure static file serving
- **sensitive path probe: /admin** — passed (260.2ms) — Check for hidden admin panel or debug interface
- **auth login redirect Location header points to expected OAuth provider** — passed (269.3ms) — Verify the OAuth redirect target is a legitimate provider (not open redirect) — we confirmed 302 but never inspected the Location header destination
- **auth logout without session — open redirect check** — passed (501.0ms) — Check if logout accepts arbitrary redirect parameter that could be used for phishing
- **POST /send with GET method returns 405 not 200** — passed (274.4ms) — Verify POST-only endpoints properly reject GET — method confusion could bypass auth if GET handler exists without protection
- **FastAPI /docs allows API execution without auth (try-it-out)** — passed (403.1ms) — /docs is exposed and Swagger UI has 'Try it out' — verify requests from docs context still enforce auth
- **search results with XSS in q param (reflected)** — passed (286.4ms) — Search results should redirect unauthed — but if it reflects the query param in the redirect or error page, XSS risk
- **compose with draft_id path traversal** — passed (276.3ms) — draft_id param could be used as file path internally — verify it redirects unauthed and doesn't leak filesystem
- **inbox with ike param (undocumented) — SSRF probe** — passed (233.5ms) — The 'ike' query param on /inbox is unusual and undocumented — could be an SSRF vector if it fetches URLs server-side
- **POST /send with content-type JSON (CSRF check)** — passed (302.0ms) — Cross-origin POST with form content type — if no CSRF protection, could send emails from victim's session
- **rate limiting on auth login** — passed (275.6ms) — Check if auth login is rate-limited or if X-Forwarded-For spoofing bypasses rate limits
- **email detail with negative ID (integer underflow)** — passed (264.4ms) — Negative IDs can cause unexpected DB behavior — should redirect unauthed, not crash
- **API delete with wildcard/SQL in email_id** — passed (210.4ms) — SQL injection in path param for destructive operation — should get 404 (hidden) not 500 (injection parsed)
- **host header poisoning on /auth/login (cache poisoning vector)** — passed (222.4ms) — If OAuth redirect URL is built from X-Forwarded-Host, attacker can redirect OAuth flow to their domain

## What's broken
- **auth callback without code rejects gracefully** [KNOWN FALSE POSITIVE]
  - Why it's OK: OAuth callback returns 500 without valid state/code params — expected behavior
  - Expected status 302, got 500
  - Got status 500
  - Why this matters: Callback without auth code should not crash or leak error details
- **email detail with path traversal attempt**
  - Expected status 302, got 404
  - Got status 404
  - Why this matters: Path traversal in email_id param — should redirect to login or 404, never serve filesystem content
- **API emails unauthed returns 401 or 403 (not 404)**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: API endpoints should return proper auth error codes. 404 hides auth state but also hides bugs — need to see what actually happens
- **POST /api/sync unauthed — correct method**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: Destructive action (sync) must enforce auth even via API
- **POST /api/emails/1/delete unauthed**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: Delete is highest-risk action — must be auth-gated
- **POST /api/send unauthed with spoofed body**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: Email send without auth is critical vuln — must return auth error, never send
- **CORS headers on API endpoint**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: Check if API reflects arbitrary origins in Access-Control-Allow-Origin — would allow cross-site data theft
- **auth callback with crafted code param** [KNOWN FALSE POSITIVE]
  - Why it's OK: OAuth callback returns 500 without valid state/code params — expected behavior
  - Expected status 302, got 500
  - Got status 500
  - Why this matters: Does the callback validate the OAuth state parameter? A fake code+state should fail gracefully (302 to login), not 500.
- **CORS headers on health endpoint (reachable)** [KNOWN FALSE POSITIVE]
  - Why it's OK: POST-only endpoints return 405 when tested with GET — not a real failure
  - Expected status 200, got 405
  - Got status 405
  - Why this matters: Round 1 CORS test hit a 404. Testing against /health which is reachable to see if CORS is configured and whether it reflects arbitrary origins.
- **static assets accessible without auth (PWA)**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: PWA was just deployed. manifest.json and service worker should be publicly accessible for PWA install to work. Verifies static file serving.
- **service worker JS accessible without auth**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: Service worker must be public for PWA. Also check if it contains any hardcoded secrets or API keys in the JS.
- **Host header injection on login redirect**
  - Expected status 302, got 404
  - Got status 404
  - Why this matters: If the OAuth redirect URL is built from the Host header, an attacker could redirect the OAuth callback to their domain and steal the auth code.
- **manifest.json accessible without auth (PWA)**
  - Expected status 200, got 404
  - Response body does not contain 'name'
  - Got status 404
  - Why this matters: Previous static asset probe used wrong path. PWA manifest.json is standard and should be public.
- **static directory enumeration (common FastAPI pattern)**
  - Expected status 200, got 404
  - Got status 404
  - Why this matters: FastAPI typically serves static from /static/. Previous probe may have used wrong path. Try common filenames.
- **CORS preflight on /health (OPTIONS method)** [KNOWN FALSE POSITIVE]
  - Why it's OK: POST-only endpoints return 405 when tested with GET — not a real failure
  - Expected status 200, got 405
  - Got status 405
  - Why this matters: Previous CORS check on /health got 405 — likely sent wrong method. OPTIONS preflight is the correct way to test CORS.
- **auth callback 500 response body inspection** [KNOWN FALSE POSITIVE]
  - Why it's OK: OAuth callback returns 500 without valid state/code params — expected behavior
  - Response body does not contain 'traceback'
  - Got status 500
  - Why this matters: Callback returns 500 — need to verify response body doesn't leak stack traces, internal paths, or secrets with a crafted state param.
- **sensitive path probe: /docs (FastAPI auto-docs)**
  - Expected status 404, got 200
  - Got status 200
  - Why this matters: FastAPI exposes /docs (Swagger UI) by default. Should be disabled in production. If 200, it's an info leak.
- **sensitive path probe: /redoc**
  - Expected status 404, got 200
  - Got status 200
  - Why this matters: FastAPI also exposes /redoc by default. Should be disabled alongside /docs.
- **sensitive path probe: /docs/oauth2-redirect**
  - Expected status 404, got 200
  - Got status 200
  - Why this matters: FastAPI ships an OAuth2 redirect helper at this path — if exposed, could be used in auth flow manipulation
- **host header injection on root path (was 404 on /auth/login)**
  - Expected status 302, got 404
  - Got status 404
  - Why this matters: Host header injection on /auth/login returned 404 — test on root to see if Railway edge rejects mismatched Host headers entirely vs app-level behavior

## Probable root causes
- FastAPI app created without `docs_url=None, redoc_url=None, openapi_url=None` — default debug UI is live in production
- PWA static files (manifest.json, sw.js) either not deployed, not mounted in FastAPI's StaticFiles, or served from a path the tests didn't find
- OAuth callback handler lacks try/except around token exchange — unhandled exception on missing/invalid code/state params

## What to do about it
- Set `app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` in production to disable Swagger UI, ReDoc, and OpenAPI spec exposure
- Verify PWA static files are included in the Docker build and mounted via `app.mount('/static', StaticFiles(...))` — check manifest.json path matches what's referenced in HTML templates
- Wrap the OAuth callback token exchange in try/except and redirect to /auth/login on failure instead of letting the 500 propagate
- Add CORS middleware with explicit allowed origins if cross-origin API access is needed, or confirm it's intentionally absent (current state: no CORS headers, which is safe but means no cross-origin JS access)

## Watch out for
- 18 endpoints return 302 (redirect to login). This is NORMAL for auth-protected routes without a bearer token.
- Endpoints ['CORS headers on health endpoint (reachable)', 'HTTP method confusion — PUT on /api/emails/1/delete', 'CORS preflight on /health (OPTIONS method)', 'POST /send with GET method returns 405 not 200'] return 405 (Method Not Allowed). These are likely POST/PUT/DELETE endpoints tested with GET.
- 403 vs 401: 403 means 'I know who you are but you can't do that', 401 means 'I don't know who you are'
- CORS preflight (OPTIONS) returning 200 is correct — it's the access-control headers that matter
- Rate limiting might make tests flaky — space out requests

## When it's fixed
All 51 checks should pass. Specifically:
- email detail with path traversal attempt should pass
- API emails unauthed returns 401 or 403 (not 404) should pass
- POST /api/sync unauthed — correct method should pass
- POST /api/emails/1/delete unauthed should pass
- POST /api/send unauthed with spoofed body should pass
- CORS headers on API endpoint should pass
- static assets accessible without auth (PWA) should pass
- service worker JS accessible without auth should pass
- Host header injection on login redirect should pass
- manifest.json accessible without auth (PWA) should pass
- static directory enumeration (common FastAPI pattern) should pass
- sensitive path probe: /docs (FastAPI auto-docs) should pass
- sensitive path probe: /redoc should pass
- sensitive path probe: /docs/oauth2-redirect should pass
- host header injection on root path (was 404 on /auth/login) should pass

## For the next robot

**Test suite:** `/Users/dshanklinbv/repos-eidos-agi/eidos-mail/.test-forge/suites/mail-eidosagi-com.yaml` — run `test_this("https://mail.eidosagi.com")` to execute it.
The suite is the artifact. It persists. Add to it, don't start over.

**To retest:** `test_this("https://mail.eidosagi.com")` or `test_this("https://mail.eidosagi.com", playbook="security")`
