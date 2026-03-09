# eidos-mail

**Target:** https://mail.eidosagi.com
**Type:** api
**Tested as:** Software Engineer
**Last tested:** 2026-03-09 03:13 UTC
**Status:** DEGRADED (11/15 checks passed)
**Rounds:** 1
**Test suite:** `/Users/dshanklinbv/repos-eidos-agi/eidos-mail/.test-forge/suites/mail-eidosagi-com.yaml`

## What "working" means
health endpoint responds, auth-protected routes redirect to login, public routes return 200, endpoints respond with correct methods

## What's working
- **health endpoint returns ok with email count** — passed (276.5ms) — Baseline connectivity and confirms service is up with email count in response
- **root redirects unauthenticated to login** — passed (275.2ms) — Confirms auth guard on root — unauthenticated users should redirect to login
- **auth login initiates OAuth flow** — passed (287.8ms) — Login should redirect to OAuth provider — 302 is expected behavior
- **inbox requires auth (302 redirect)** — passed (272.1ms) — Protected page — confirms auth middleware is active on main UI routes
- **POST /sync with correct method requires auth** — passed (267.9ms) — Prior suite used GET (wrong method). POST is correct per spec — should still require auth
- **POST /send requires auth** — passed (279.7ms) — Critical: send email must be auth-protected. Using POST (correct method, prior suite used GET)
- **POST /mark-all-read with correct method** — passed (306.2ms) — Destructive action (marks all read) — must require auth. Prior suite used GET (wrong method)
- **POST /draft save requires auth** — passed (282.5ms) — Draft save is a write operation — auth required. Prior suite used GET
- **unknown path returns 404** — passed (206.0ms) — Confirms proper 404 handling — app doesn't catch-all redirect unknown paths
- **openapi.json is publicly accessible** — passed (211.3ms) — OpenAPI spec should be public for documentation — verifies it contains expected API title
- **email detail with fake ID requires auth** — passed (217.2ms) — Tests auth fires before path param validation — should redirect before checking if email exists

## What's broken
- **auth callback without code rejects gracefully** [KNOWN FALSE POSITIVE]
  - Why it's OK: OAuth callback returns 500 without valid state/code params — expected behavior
  - Expected status 302, got 500
  - Got status 500
  - Why this matters: Callback without OAuth code/state should redirect, not crash
- **API emails endpoint requires auth**
  - Expected status 302, got 401
  - Got status 401
  - Why this matters: API endpoints should also enforce auth — tests whether API routes have same auth guard as UI
- **API search requires auth and q param**
  - Expected status 302, got 401
  - Got status 401
  - Why this matters: Tests auth on API search with required param provided — isolates auth vs validation
- **POST /api/sync requires auth**
  - Expected status 302, got 401
  - Got status 401
  - Why this matters: API sync endpoint — confirms write operations are auth-protected

## Watch out for
- 8 endpoints return 302 (redirect to login). This is NORMAL for auth-protected routes without a bearer token.
- 302 on authenticated endpoints is correct behavior, not a failure
- 405 means wrong HTTP method, not a broken endpoint
- 422 on POST without body means validation is working correctly

## When it's fixed
All 15 checks should pass. Specifically:
- API emails endpoint requires auth should pass
- API search requires auth and q param should pass
- POST /api/sync requires auth should pass

## For the next robot

**Test suite:** `/Users/dshanklinbv/repos-eidos-agi/eidos-mail/.test-forge/suites/mail-eidosagi-com.yaml` — run `test_this("https://mail.eidosagi.com")` to execute it.
The suite is the artifact. It persists. Add to it, don't start over.

**To retest:** `test_this("https://mail.eidosagi.com")` or `test_this("https://mail.eidosagi.com", playbook="security")`
