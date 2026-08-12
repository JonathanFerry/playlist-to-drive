"""
auth.py - Google OAuth for Drive and Docs.

Runs the installed-app flow once, caches the refresh token in token.json,
and reuses it silently thereafter. Delete token.json to force re-consent.

Neither credentials.json nor token.json should ever enter a git repo.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

# Anchored to the repository, not to a fixed home directory path.
# Hardcoding ~/Documents made this work only on one machine, and
# put working state inside a cloud-synced folder — which once
# evicted a temp file mid-write and ended an eight-hour run.
BASE_DIR = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"

# One token per named profile, so switching which Google account the tool
# acts as is a config change rather than deleting a file and re-authorising.
# Tokens for every profile persist, so switching back does not re-prompt.
_TOKEN_PATH = BASE_DIR / "token.json"          # legacy, still honoured
_profile = "default"


def set_profile(name: str) -> None:
    """Select which stored account to act as. Called from config load."""
    global _profile
    _profile = (name or "default").strip() or "default"


def token_path() -> Path:
    if _profile == "default":
        return _TOKEN_PATH
    return BASE_DIR / f"token-{_profile}.json"


def get_credentials() -> Credentials:
    """Return valid credentials, running the consent flow only if needed."""
    creds = None

    tp = token_path()
    if tp.exists():
        creds = Credentials.from_authorized_user_file(str(tp), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            tp.write_text(creds.to_json())
            return creds
        except (RefreshError, TransportError, OSError) as exc:
            # A revoked or expired refresh token, a network failure, or an
            # unwritable token file. Anything else is a bug in this code and
            # should surface rather than be papered over with a login prompt.
            print(f"Token refresh failed ({exc}); re-authorizing.")
            creds = None

    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"Missing {CREDENTIALS_PATH}\n"
            "Download the Desktop app OAuth client JSON from Google Cloud "
            "Console and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    print(f"Authorising profile '{_profile}' — sign in as the intended account.")
    creds = flow.run_local_server(port=0)
    tp.write_text(creds.to_json())
    tp.chmod(0o600)
    return creds


def drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def docs_service():
    return build("docs", "v1", credentials=get_credentials(), cache_discovery=False)
