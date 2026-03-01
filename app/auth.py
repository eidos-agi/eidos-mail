"""OIDC authentication for eidos-mail via Authentik SSO."""

import time
import httpx
from authlib.integrations.starlette_client import OAuth

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

from app.config import (
    OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET,
    OIDC_REDIRECT_URI, BASE_URL,
)


# ---------------------------------------------------------------------------
# Custom exception for web auth failures (caught by main.py exception handler)
# ---------------------------------------------------------------------------

class AuthRequired(Exception):
    pass


# ---------------------------------------------------------------------------
# OAuth client setup (authlib)
# ---------------------------------------------------------------------------

oauth = OAuth()

# Authentik OIDC discovery URL
_discovery_url = OIDC_ISSUER.rstrip("/") + "/.well-known/openid-configuration"

oauth.register(
    name="authentik",
    client_id=OIDC_CLIENT_ID,
    client_secret=OIDC_CLIENT_SECRET,
    server_metadata_url=_discovery_url,
    client_kwargs={"scope": "openid email profile"},
)


# ---------------------------------------------------------------------------
# JWKS cache for bearer token validation
# ---------------------------------------------------------------------------

_jwks_cache: dict = {"keys": [], "expires": 0}


async def _get_jwks() -> dict:
    """Fetch and cache JWKS from Authentik (1-hour TTL)."""
    now = time.time()
    if _jwks_cache["keys"] and now < _jwks_cache["expires"]:
        return _jwks_cache

    async with httpx.AsyncClient() as client:
        meta = (await client.get(_discovery_url)).json()
        jwks_uri = meta["jwks_uri"]
        resp = await client.get(jwks_uri)
        jwks = resp.json()

    _jwks_cache["keys"] = jwks.get("keys", [])
    _jwks_cache["expires"] = now + 3600
    return _jwks_cache


async def _validate_bearer(token: str) -> str:
    """Validate a bearer JWT against Authentik JWKS. Returns email or raises."""
    from authlib.jose import jwt as authlib_jwt, JoseError

    jwks = await _get_jwks()
    try:
        claims = authlib_jwt.decode(token, {"keys": jwks["keys"]})
        claims.validate()
    except (JoseError, Exception) as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    # Verify issuer and audience
    if claims.get("iss") != OIDC_ISSUER.rstrip("/"):
        raise HTTPException(status_code=401, detail="Invalid issuer")
    aud = claims.get("aud")
    if isinstance(aud, list):
        if OIDC_CLIENT_ID not in aud:
            raise HTTPException(status_code=401, detail="Invalid audience")
    elif aud != OIDC_CLIENT_ID:
        raise HTTPException(status_code=401, detail="Invalid audience")

    email = claims.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="No email in token")
    return email


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def require_web_auth(request: Request) -> str:
    """Dependency for web UI routes. Returns email or raises AuthRequired."""
    email = request.session.get("user_email")
    if not email:
        raise AuthRequired()
    return email


async def require_api_auth(request: Request) -> str:
    """Dependency for API routes. Returns email or raises HTTPException(401)."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header[7:]
    return await _validate_bearer(token)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    redirect_uri = OIDC_REDIRECT_URI or f"{BASE_URL}/auth/callback"
    return await oauth.authentik.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    token = await oauth.authentik.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        raise HTTPException(status_code=400, detail="No userinfo in token response")

    email = userinfo.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email in userinfo")

    request.session["user_email"] = email
    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()

    # RP-initiated logout via Authentik
    try:
        async with httpx.AsyncClient() as client:
            meta = (await client.get(_discovery_url)).json()
            end_session = meta.get("end_session_endpoint")
            if end_session:
                return RedirectResponse(
                    url=f"{end_session}?post_logout_redirect_uri={BASE_URL}",
                    status_code=302,
                )
    except Exception:
        pass

    return RedirectResponse(url="/", status_code=302)
