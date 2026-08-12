"""
auth.py - Google OAuth for Drive and Docs.

Runs the installed-app flow once, caches the refresh token in token.json,
and reuses it silently thereafter. Delete token.json to force re-consent.

Neither credentials.json nor token.json should ever enter a git repo.
"""

from __future__ import annotations

import tomllib
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
_profile: str | None = None


def _profile_from_config() -> str:
    """Read auth.account straight from config.toml.

    Deliberately not via pipeline.config: the profile has to resolve for
    every entry point, including the tools that never load config. Making
    it a side effect of config.load() meant seven of eleven tools silently
    acted as the wrong account — including one that trashes files.
    """
    path = BASE_DIR / "config.toml"
    if not path.exists():
        return "default"
    try:
        with path.open("rb") as fh:
            return (tomllib.load(fh).get("auth", {})
                    .get("account", "default").strip() or "default")
    except (OSError, tomllib.TOMLDecodeError):
        return "default"


def set_profile(name: str) -> None:
    """Override the configured profile. Rarely needed."""
    global _profile
    _profile = (name or "default").strip() or "default"


def active_profile() -> str:
    global _profile
    if _profile is None:
        _profile = _profile_from_config()
    return _profile


def token_path() -> Path:
    name = active_profile()
    if name == "default":
        return _TOKEN_PATH
    return BASE_DIR / f"token-{name}.json"


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
    print(f"Authorising profile '{active_profile()}' — sign in as the intended account.")
    creds = flow.run_local_server(port=0)
    tp.write_text(creds.to_json())
    tp.chmod(0o600)
    return creds


def drive_service():
    return build("drive", "v3", credentials=get_credentials(), cache_discovery=False)


def docs_service():
    return build("docs", "v1", credentials=get_credentials(), cache_discovery=False)
