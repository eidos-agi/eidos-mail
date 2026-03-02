"""Settings from environment variables."""

import os


DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Railway uses postgres:// but asyncpg needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Vault (per-user credential storage — replaces single-user EMAIL_ADDRESS/EMAIL_PASSWORD)
VAULT_URL = os.environ.get("VAULT_URL", "https://vault.eidosagi.com")
VAULT_SERVICE_TOKEN = os.environ.get("VAULT_SERVICE_TOKEN", "")

# OIDC / SSO
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "https://sso.eidosagi.com/application/o/eidos-mail/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "eidos-mail")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
