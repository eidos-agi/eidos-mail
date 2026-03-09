"""OIDC authentication for eidos-mail via Authentik SSO + eidos-vault JWTs."""

import base64
import json
import os
import time
import httpx
from authlib.integrations.starlette_client import OAuth

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

from app.config import (
    OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET,
    OIDC_REDIRECT_URI, BASE_URL, VAULT_URL,
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
# JWKS caches for bearer token validation (Authentik + eidos-vault)
# ---------------------------------------------------------------------------

_authentik_jwks: dict = {"keys": [], "expires": 0}
_vault_jwks: dict = {"keys": [], "expires": 0}

VAULT_ISSUER = VAULT_URL.rstrip("/")  # https://vault.eidosagi.com
_vault_jwks_url = f"{VAULT_ISSUER}/.well-known/jwks.json"


async def _get_authentik_jwks() -> dict:
    """Fetch and cache JWKS from Authentik (1-hour TTL)."""
    now = time.time()
    if _authentik_jwks["keys"] and now < _authentik_jwks["expires"]:
        return _authentik_jwks

    async with httpx.AsyncClient() as client:
        meta = (await client.get(_discovery_url)).json()
        jwks_uri = meta["jwks_uri"]
        resp = await client.get(jwks_uri)
        jwks = resp.json()

    _authentik_jwks["keys"] = jwks.get("keys", [])
    _authentik_jwks["expires"] = now + 3600
    return _authentik_jwks


async def _get_vault_jwks() -> dict:
    """Fetch and cache JWKS from eidos-vault (1-hour TTL)."""
    now = time.time()
    if _vault_jwks["keys"] and now < _vault_jwks["expires"]:
        return _vault_jwks

    async with httpx.AsyncClient() as client:
        resp = await client.get(_vault_jwks_url)
        jwks = resp.json()

    _vault_jwks["keys"] = jwks.get("keys", [])
    _vault_jwks["expires"] = now + 3600
    return _vault_jwks


def _peek_jwt_issuer(token: str) -> str | None:
    """Extract issuer from JWT payload without verification (for routing)."""
    try:
        payload = token.split(".")[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("iss")
    except Exception:
        return None


async def _validate_bearer(token: str) -> str:
    """Validate a bearer JWT against the appropriate JWKS. Returns email or raises."""
    from authlib.jose import jwt as authlib_jwt, JoseError

    issuer = _peek_jwt_issuer(token)

    if issuer == VAULT_ISSUER:
        # Vault-issued JWT (from eidos CLI or service key exchange)
        jwks = await _get_vault_jwks()
        try:
            claims = authlib_jwt.decode(token, {"keys": jwks["keys"]})
            claims.validate()
        except (JoseError, Exception) as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

        if claims.get("iss") != VAULT_ISSUER:
            raise HTTPException(status_code=401, detail="Invalid issuer")

        email = claims.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="No email in token")
        return email

    else:
        # Authentik OIDC JWT (default)
        jwks = await _get_authentik_jwks()
        try:
            claims = authlib_jwt.decode(token, {"keys": jwks["keys"]})
            claims.validate()
        except (JoseError, Exception) as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

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
    dev_user = os.environ.get("DEV_USER")
    if dev_user:
        return dev_user
    email = request.session.get("user_email")
    if not email:
        raise AuthRequired()
    return email


async def require_api_auth(request: Request) -> str:
    """Dependency for API routes. Returns 404 to hide endpoint existence."""
    dev_user = os.environ.get("DEV_USER")
    if dev_user:
        return dev_user
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=404, detail="Not Found")
    token = auth_header[7:]
    try:
        return await _validate_bearer(token)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Not Found")


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
    try:
        token = await oauth.authentik.authorize_access_token(request)
    except Exception:
        return RedirectResponse(url="/auth/login", status_code=302)

    userinfo = token.get("userinfo")
    if not userinfo:
        return RedirectResponse(url="/auth/login", status_code=302)

    email = userinfo.get("email")
    if not email:
        return RedirectResponse(url="/auth/login", status_code=302)

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
