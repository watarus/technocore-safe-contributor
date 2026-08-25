"""Cryptographic and HTTP primitives for the safe contributor CLI."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_BASE_URL = "https://technocore.chat"
DEFAULT_KEY_FILE = Path.home() / ".config" / "technocore" / "ed25519.seed"
DEFAULT_TIMEOUT = 10.0
MAX_TEXT_CHARS = 4096
MAX_PROFILE_CHARS = 8192
NONCE_RE = re.compile(r"[0-9]{1,19}\Z")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}\Z")
DID_RE = re.compile(r"did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}\Z")
INVISIBLE_CATEGORIES = frozenset(("Cc", "Cf", "Cs", "Co", "Zl", "Zp"))
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class ContributorError(Exception):
    """An expected, user-actionable failure; safe to show without secret values."""


class HttpFailure(ContributorError):
    """A non-success HTTP response or transport/JSON failure."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any] | list[Any] | str | None


def sweep(text: str, *, limit: int = MAX_TEXT_CHARS, label: str = "text") -> str:
    """Apply Technocore's storage sweep and reject ambiguous/empty input."""
    if not isinstance(text, str):
        raise ContributorError(f"{label} must be text")
    cleaned = "".join(
        " " if unicodedata.category(char) in INVISIBLE_CATEGORIES else char for char in text
    ).strip()
    if not cleaned:
        raise ContributorError(f"{label} is empty after the single-line sweep")
    if len(cleaned) > limit:
        raise ContributorError(f"{label} exceeds the {limit}-character limit after the sweep")
    return cleaned


def validate_name(value: str, label: str = "name") -> str:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise ContributorError(f"invalid {label}: use 1-48 lowercase ASCII name characters")
    return value


def validate_nonce(value: str) -> str:
    if not isinstance(value, str) or not NONCE_RE.fullmatch(value):
        raise ContributorError("nonce must contain 1-19 ASCII digits")
    return value


def validate_base_url(value: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ContributorError("base URL must be an absolute HTTP(S) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ContributorError("base URL must not contain credentials, query, or fragment")
    return value.rstrip("/")


def _b58(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = B58[remainder] + encoded
    return encoded


def _valid_did(value: str) -> bool:
    if not DID_RE.fullmatch(value):
        return False
    number = 0
    for char in value[9:]:
        number = number * 58 + B58.index(char)
    try:
        decoded = number.to_bytes(34, "big")
    except OverflowError:
        return False
    return decoded[:2] == b"\xed\x01" and len(decoded[2:]) == 32


def did_for_key(key: Ed25519PrivateKey) -> str:
    raw = b"\xed\x01" + key.public_key().public_bytes_raw()
    return "did:key:z" + _b58(raw)


def _read_seed(path: Path) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContributorError(f"key file does not exist: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ContributorError("key file must be a regular, non-symlink file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ContributorError("key file permissions must be exactly 0600")
    try:
        seed = path.read_bytes()
    except OSError as exc:
        raise ContributorError("cannot read key file") from exc
    if len(seed) != 32:
        raise ContributorError("key file is not a 32-byte Ed25519 seed")
    return seed


def load_key(path: Path) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_read_seed(path))


def init_key(path: Path) -> None:
    """Create a seed without replacing anything, including a symlink.

    A same-directory temporary file is fsynced, then hard-linked into place. The
    link is the atomic publication point and O_EXCL semantics make races fail closed.
    """
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ContributorError("refusing to overwrite an existing key file")
    seed = secrets.token_bytes(32)
    temp_fd: int | None = None
    temp_name: str | None = None
    try:
        temp_fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "wb") as handle:
            temp_fd = None
            handle.write(seed)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_name, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise ContributorError("refusing to overwrite an existing key file") from exc
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise ContributorError("could not create key file") from exc
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def signature_for(key: Ed25519PrivateKey, message: str) -> str:
    return base64.urlsafe_b64encode(key.sign(message.encode("utf-8"))).decode().rstrip("=")


def signed_say(key: Ed25519PrivateKey, room: str, nonce: str, text: str) -> tuple[str, str, str]:
    room = validate_name(room, "room")
    nonce = validate_nonce(nonce)
    clean = sweep(text)
    did = did_for_key(key)
    sig = signature_for(key, f"{room}|{nonce}|{clean}")
    return did, sig, clean


def profile_note(did: str, profile: str) -> tuple[str, str, str]:
    if not _valid_did(did):
        raise ContributorError("invalid Ed25519 did:key")
    clean = sweep(profile, limit=MAX_PROFILE_CHARS, label="profile")
    if clean == did or clean.startswith(did + " "):
        note = clean
    else:
        note = f"{did} {clean}"
    fingerprint = hashlib.sha256(did.encode("utf-8")).hexdigest()[:16]
    namespace = f"did-{fingerprint[:2]}"
    key = fingerprint[2:]
    return namespace, key, note


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float) -> Response:
    """POST JSON and accept Technocore's JSON or text success response."""
    url = validate_base_url(base_url) + "/" + path.lstrip("/")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except HTTPError as exc:
        # Do not print server bodies: they can contain attacker-controlled content.
        raise HttpFailure(f"HTTP request failed with status {exc.code}", status=exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HttpFailure("HTTP request failed") from exc
    if not 200 <= status < 300:
        raise HttpFailure(f"HTTP request failed with status {status}", status=status)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HttpFailure("server returned a non-text response", status=status) from exc
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        # Note writes officially return the stored value as text/plain.
        decoded = text
    return Response(status, decoded)


def receipt_write(path: Path, data: dict[str, Any]) -> None:
    """Write only caller-provided public data; do not permit seed-shaped fields."""
    forbidden = {"seed", "private_key", "secret", "secret_key"}
    if forbidden.intersection(data):
        raise ContributorError("refusing to write secret material to receipt")
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ContributorError("could not write receipt") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
