# website at https://mail.eidosagi.com

**Target:** https://mail.eidosagi.com
**Type:** website
**Tested as:** Software Engineer
**Last tested:** 2026-03-09 07:25 UTC
**Status:** HEALTHY (15/15 checks passed)
**Rounds:** 1
**Test suite:** `/Users/dshanklinbv/repos-eidos-agi/eidos-mail/.test-forge/suites/mail-eidosagi-com.yaml`

## What "working" means
health endpoint responds, auth-protected routes redirect to login, public routes return 200

## What's working
- **health endpoint returns ok** — passed (297.0ms) — Baseline connectivity and service status
- **root redirects unauthenticated to login** — passed (295.5ms) — Verify auth gate on root path
- **inbox requires auth (302 redirect)** — passed (293.5ms) — Core page must enforce auth
- **trash folder requires auth** — passed (286.9ms) — Trash folder access must be auth-gated
- **POST /delete/1 requires auth** — passed (282.1ms) — Delete action must enforce auth, not expose data
- **POST /undelete/1 requires auth** — passed (212.0ms) — Undelete (restore from trash) must enforce auth
- **POST /api/emails/1/delete returns 404 without auth** — passed (287.4ms) — API delete endpoint should hide behind 404 when unauthenticated
- **POST /api/emails/1/undelete returns 404 without auth** — passed (298.7ms) — API undelete endpoint should hide behind 404 when unauthenticated
- **POST /mark-read requires auth** — passed (300.2ms) — Mark-read action must enforce auth
- **static manifest.json accessible without auth** — passed (233.9ms) — PWA manifest must be publicly accessible
- **openapi.json disabled in production** — passed (214.0ms) — API spec should not be exposed in production
- **GET on POST-only /sync returns 405** — passed (216.4ms) — Method enforcement — sync is POST-only
- **auth login initiates OAuth redirect** — passed (300.1ms) — Login should redirect to OAuth provider
- **sensitive path /.env returns 404** — passed (217.9ms) — Environment file must never be served
- **unknown path returns 404** — passed (286.7ms) — Verify proper 404 handling for unknown routes

## Watch out for
- 7 endpoints return 302 (redirect to login). This is NORMAL for auth-protected routes without a bearer token.
- Endpoints ['GET on POST-only /sync returns 405'] return 405 (Method Not Allowed). These are likely POST/PUT/DELETE endpoints tested with GET.
- 302 on authenticated endpoints is correct behavior, not a failure
- 405 means wrong HTTP method, not a broken endpoint
- 422 on POST without body means validation is working correctly

## For the next robot

**Test suite:** `/Users/dshanklinbv/repos-eidos-agi/eidos-mail/.test-forge/suites/mail-eidosagi-com.yaml` — run `test_this("https://mail.eidosagi.com")` to execute it.
The suite is the artifact. It persists. Add to it, don't start over.

**To retest:** `test_this("https://mail.eidosagi.com")` or `test_this("https://mail.eidosagi.com", playbook="security")`
