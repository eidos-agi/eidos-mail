"""Settings from environment variables."""

import os


DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Railway uses postgres:// but asyncpg needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.migadu.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.migadu.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")

# OIDC / SSO
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "https://sso.eidosagi.com/application/o/eidos-mail/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "eidos-mail")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
