from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from urllib.request import Request

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from technocore_safe_contributor import cli
from technocore_safe_contributor.core import (
    ContributorError,
    Response,
    did_for_key,
    init_key,
    load_key,
    profile_note,
    receipt_write,
    signed_say,
)


def test_init_is_private_and_does_not_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "seed"
    init_key(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert len(target.read_bytes()) == 32
    with pytest.raises(ContributorError):
        init_key(target)


def test_init_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "seed"
    target.symlink_to(tmp_path / "other")
    with pytest.raises(ContributorError):
        init_key(target)


def test_did_and_signature_canonical_text_reach_verifier(tmp_path: Path) -> None:
    path = tmp_path / "seed"
    init_key(path)
    key = load_key(path)
    did, signature, text = signed_say(key, "lobby", "7", " hi\nthere\u200b ")
    assert did == did_for_key(key)
    assert text == "hi there"
    key.public_key().verify(
        __import__("base64").urlsafe_b64decode(signature + "=="), b"lobby|7|hi there"
    )


def test_key_permission_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "seed"
    path.write_bytes(os.urandom(32))
    path.chmod(0o644)
    with pytest.raises(ContributorError):
        load_key(path)


def test_profile_uses_official_shard() -> None:
    did = did_for_key(Ed25519PrivateKey.from_private_bytes(bytes(range(32))))
    namespace, key, note = profile_note(did, "mailbox:mb-p-example")
    import hashlib

    fingerprint = hashlib.sha256(did.encode()).hexdigest()[:16]
    assert namespace == "did-" + fingerprint[:2]
    assert key == fingerprint[2:]
    assert note == did + " mailbox:mb-p-example"
    with pytest.raises(ContributorError):
        profile_note(did + "x", "mailbox:mb-p-example")


def test_receipt_rejects_secret_fields(tmp_path: Path) -> None:
    with pytest.raises(ContributorError):
        receipt_write(tmp_path / "receipt.json", {"seed": "must-not-appear"})


def test_receipt_is_json_and_public_only(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    receipt_write(path, {"did": "did:key:z6Mkexample", "status": 200})
    assert json.loads(path.read_text()) == {"did": "did:key:z6Mkexample", "status": 200}


def test_bootstrap_wires_public_did_signature_payload_to_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / "seed"
    receipt = tmp_path / "receipt.json"
    init_key(key_path)
    requests: list[tuple[str, dict[str, object]]] = []

    def fake_post(base: str, path: str, payload: dict[str, object], timeout: float) -> Response:
        requests.append((path, payload))
        return Response(201, {"ok": True})

    monkeypatch.setattr(cli, "post_json", fake_post)
    args = cli.parser().parse_args(
        [
            "bootstrap",
            "--key-file",
            str(key_path),
            "--base-url",
            "http://test.invalid",
            "--nonce",
            "9",
            "--receipt",
            str(receipt),
            "--greeting",
            "hello\nthere",
            "mailbox:mb-p-example",
        ]
    )
    cli.run(args)
    assert requests[0][0].startswith("/kv/did-")
    assert requests[0][1]["value"].startswith("did:key:z6Mk")
    assert requests[1][0] == "/r/lobby"
    greeting = requests[1][1]
    assert greeting["did"] == requests[0][1]["value"].split(" ", 1)[0]
    assert greeting["text"] == "hello there"
    assert isinstance(greeting["sig"], str) and len(greeting["sig"]) == 86
    saved = json.loads(receipt.read_text())
    assert saved["did"] == greeting["did"]
    assert "seed" not in json.dumps(saved).lower()


def test_post_json_requires_json_and_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from technocore_safe_contributor import core

    calls: list[Request] = []

    class FakeResponse:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_open(request: Request, timeout: float):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr(core, "urlopen", fake_open)
    result = core.post_json("https://example.invalid", "/r/lobby", {"text": "x"}, 1.0)
    assert result.status == 201
    assert len(calls) == 1
    assert calls[0].get_header("Accept") == "application/json"
    assert calls[0].get_header("Content-type") == "application/json"

    class RedirectResponse(FakeResponse):
        status = 302

    monkeypatch.setattr(core, "urlopen", lambda request, timeout: RedirectResponse())
    with pytest.raises(core.HttpFailure):
        core.post_json("https://example.invalid", "/r/lobby", {"text": "x"}, 1.0)
