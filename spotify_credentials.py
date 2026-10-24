from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


CREDENTIAL_TARGET = "SpotifyArtistManager.ClientCredentials.v1"
_CREDENTIAL_NOT_FOUND = 1168
_CREDENTIAL_BLOB_LIMIT = 5 * 512


class CredentialValidationError(ValueError):
    pass


class CredentialStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpotifyCredentials:
    client_id: str
    client_secret: str = field(repr=False)


def validate_credentials(client_id: str, client_secret: str) -> SpotifyCredentials:
    normalized_client_id = (client_id or "").strip()
    normalized_client_secret = (client_secret or "").strip()
    if not normalized_client_id:
        raise CredentialValidationError("Enter your Spotify Client ID.")
    if not normalized_client_secret:
        raise CredentialValidationError("Enter your Spotify Client Secret.")
    return SpotifyCredentials(normalized_client_id, normalized_client_secret)


def choose_startup_credentials(
    environment: Mapping[str, str],
    saved: SpotifyCredentials | None,
) -> tuple[SpotifyCredentials | None, str, str, str]:
    environment_id = environment.get("SPOTIPY_CLIENT_ID", "").strip()
    environment_secret = environment.get("SPOTIPY_CLIENT_SECRET", "").strip()
    if environment_id and environment_secret:
        return (
            SpotifyCredentials(environment_id, environment_secret),
            "environment",
            environment_id,
            environment_secret,
        )
    if saved is not None:
        return saved, "saved", saved.client_id, saved.client_secret
    return None, "none", environment_id, environment_secret


def token_cache_path(client_id: str) -> Path:
    """Return an app-data cache path isolated from other Spotify app credentials."""
    client_fingerprint = hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:20]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        cache_root = Path(local_app_data) / "Spotify Artist Manager"
    elif sys.platform == "win32":
        cache_root = Path.home() / "AppData" / "Local" / "Spotify Artist Manager"
    else:
        cache_root = Path.home() / ".local" / "share" / "Spotify Artist Manager"
    return cache_root / f"spotipy-{client_fingerprint}.cache"


class WindowsCredentialStore:
    """Store credentials with the current Windows user's Credential Manager."""

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def load(self) -> SpotifyCredentials | None:
        if not self.available:
            return None

        credential_type = _WindowsCredential
        advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
        credential_pointer = ctypes.POINTER(credential_type)()
        advapi32.CredReadW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.POINTER(credential_type)),
        ]
        advapi32.CredReadW.restype = ctypes.c_bool
        advapi32.CredFree.argtypes = [ctypes.c_void_p]
        advapi32.CredFree.restype = None

        if not advapi32.CredReadW(
            CREDENTIAL_TARGET, 1, 0, ctypes.byref(credential_pointer)
        ):
            error_code = ctypes.get_last_error()
            if error_code == _CREDENTIAL_NOT_FOUND:
                return None
            raise CredentialStoreError(
                f"Windows Credential Manager could not read the saved credentials "
                f"(error {error_code})."
            )

        try:
            credential = credential_pointer.contents
            raw_blob = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            values = json.loads(raw_blob.decode("utf-16-le"))
            if not isinstance(values, dict):
                raise ValueError
            return validate_credentials(
                values.get("client_id", ""), values.get("client_secret", "")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            raise CredentialStoreError(
                "Saved credentials could not be read. Replace them in the connection "
                "dialog."
            ) from None
        finally:
            advapi32.CredFree(credential_pointer)

    def save(self, credentials: SpotifyCredentials) -> None:
        if not self.available:
            raise CredentialStoreError(
                "Secure credential storage is available only on Windows."
            )

        validated = validate_credentials(
            credentials.client_id, credentials.client_secret
        )
        raw_blob = json.dumps(
            {
                "client_id": validated.client_id,
                "client_secret": validated.client_secret,
            },
            separators=(",", ":"),
        ).encode("utf-16-le")
        if len(raw_blob) > _CREDENTIAL_BLOB_LIMIT:
            raise CredentialStoreError(
                "These credentials exceed the Windows secure-storage size limit."
            )

        advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
        advapi32.CredWriteW.argtypes = [ctypes.POINTER(_WindowsCredential), ctypes.c_uint32]
        advapi32.CredWriteW.restype = ctypes.c_bool
        blob_buffer = ctypes.create_string_buffer(raw_blob)
        credential = _WindowsCredential(
            Flags=0,
            Type=1,
            TargetName=CREDENTIAL_TARGET,
            Comment="Spotify Artist Manager credentials",
            LastWritten=_WindowsFileTime(0, 0),
            CredentialBlobSize=len(raw_blob),
            CredentialBlob=ctypes.cast(
                blob_buffer, ctypes.POINTER(ctypes.c_ubyte)
            ),
            Persist=2,
            AttributeCount=0,
            Attributes=None,
            TargetAlias=None,
            UserName="Spotify Artist Manager",
        )
        if not advapi32.CredWriteW(ctypes.byref(credential), 0):
            error_code = ctypes.get_last_error()
            raise CredentialStoreError(
                f"Windows Credential Manager could not save the credentials "
                f"(error {error_code})."
            )

    def delete(self) -> None:
        if not self.available:
            raise CredentialStoreError(
                "Secure credential storage is available only on Windows."
            )

        advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
        advapi32.CredDeleteW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        advapi32.CredDeleteW.restype = ctypes.c_bool
        if not advapi32.CredDeleteW(CREDENTIAL_TARGET, 1, 0):
            error_code = ctypes.get_last_error()
            if error_code != _CREDENTIAL_NOT_FOUND:
                raise CredentialStoreError(
                    f"Windows Credential Manager could not remove the saved "
                    f"credentials (error {error_code})."
                )


if sys.platform == "win32":
    class _WindowsFileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


    class _WindowsCredential(ctypes.Structure):
        _fields_ = [
            ("Flags", ctypes.c_uint32),
            ("Type", ctypes.c_uint32),
            ("TargetName", ctypes.c_wchar_p),
            ("Comment", ctypes.c_wchar_p),
            ("LastWritten", _WindowsFileTime),
            ("CredentialBlobSize", ctypes.c_uint32),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", ctypes.c_uint32),
            ("AttributeCount", ctypes.c_uint32),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", ctypes.c_wchar_p),
            ("UserName", ctypes.c_wchar_p),
        ]
else:
    class _WindowsFileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


    class _WindowsCredential(ctypes.Structure):
        _fields_ = []
