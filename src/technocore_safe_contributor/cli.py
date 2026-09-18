"""Command line interface for a deliberately small, fail-closed client."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .core import (
    DEFAULT_BASE_URL,
    DEFAULT_KEY_FILE,
    DEFAULT_TIMEOUT,
    ContributorError,
    HttpFailure,
    did_for_key,
    init_key,
    load_key,
    next_nonce,
    post_json,
    profile_note,
    receipt_write,
    signed_say,
    validate_base_url,
    validate_name,
    validate_nonce,
)

# A room write answers with a text dump whose message lines open with the server's seq
# and timestamp. Anchoring to the line start keeps message text from forging a later one.
TEXT_MESSAGE_RE = re.compile(r"^\[(\d{1,19})\]\s+(\S{1,64})\s", re.MULTILINE)


def _public_response(response: object) -> dict[str, object]:
    body = getattr(response, "body", None)
    if isinstance(body, str):
        lines = TEXT_MESSAGE_RE.findall(body)
        if not lines:
            return {}
        seq, ts = lines[-1]
        return {"seq": int(seq), "ts": ts}
    if not isinstance(body, dict):
        return {}
    direct = {
        key: body[key]
        for key in ("seq", "ts")
        if key in body and isinstance(body[key], (str, int, float))
    }
    if direct:
        return direct
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        return {}
    latest = messages[-1]
    return {
        key: latest[key]
        for key in ("seq", "ts")
        if key in latest and isinstance(latest[key], (str, int, float))
    }


def _say_nonce_and_text(rest: list[str]) -> tuple[str, str]:
    """Accept `room nonce text` or `room text`; an omitted nonce takes the clock.

    Technocore requires a nonce greater than the last one this key used in that room,
    so a hand-picked small number fails once any larger one has been sent.
    """
    if len(rest) == 1:
        return next_nonce(), rest[0]
    if len(rest) == 2:
        return validate_nonce(rest[0]), rest[1]
    raise ContributorError("say takes a room, an optional nonce, and exactly one text argument")


def _common(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS lets the same options work before or after the subcommand.
    parser.add_argument("--key-file", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--base-url", default=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=argparse.SUPPRESS)


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="technocore-safe-contributor")
    top.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    top.add_argument("--base-url", default=DEFAULT_BASE_URL)
    top.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    sub = top.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a new local Ed25519 seed")
    init.add_argument("--key-file", type=Path, default=argparse.SUPPRESS)
    did = sub.add_parser("did", help="print the public did:key")
    _common(did)
    say = sub.add_parser("say", help="publish one signed lobby/room message")
    _common(say)
    say.add_argument("room")
    say.add_argument("rest", nargs="+", metavar="[nonce] text")
    profile = sub.add_parser("publish-profile", help="publish a sharded DID profile note")
    _common(profile)
    profile.add_argument(
        "profile", nargs="?", help="public profile fields, e.g. mailbox:mb-p-example"
    )
    profile.add_argument("--profile", dest="profile_option", help=argparse.SUPPRESS)
    bootstrap = sub.add_parser("bootstrap", help="publish profile, greet lobby, and save receipt")
    _common(bootstrap)
    bootstrap.add_argument("profile", nargs="?")
    bootstrap.add_argument("--profile", dest="profile_option", help=argparse.SUPPRESS)
    bootstrap.add_argument("--greeting", default="hello from technocore-safe-contributor")
    bootstrap.add_argument("--nonce")
    bootstrap.add_argument("--receipt", type=Path, required=True)
    return top


def run(args: argparse.Namespace) -> dict[str, object] | None:
    if args.command == "init":
        init_key(args.key_file)
        return None
    key = load_key(args.key_file)
    did = did_for_key(key)
    if args.command == "did":
        print(did)
        return None
    base_url = validate_base_url(args.base_url)
    if args.command == "say":
        nonce, text = _say_nonce_and_text(args.rest)
        did, sig, clean = signed_say(key, args.room, nonce, text)
        response = post_json(
            base_url,
            f"/r/{validate_name(args.room, 'room')}",
            {
                "did": did,
                "sig": sig,
                "nonce": nonce,
                "text": clean,
            },
            args.timeout,
        )
        result = {
            "did": did,
            "room": args.room,
            "nonce": nonce,
            "text": clean,
            "status": response.status,
            "posted": _public_response(response),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    profile_value = args.profile if args.profile is not None else args.profile_option
    if not profile_value:
        raise ContributorError("profile text is required")
    namespace, note_key, note = profile_note(did, profile_value)
    response = post_json(base_url, f"/kv/{namespace}/{note_key}", {"value": note}, args.timeout)
    if args.command == "publish-profile":
        result = {
            "did": did,
            "profile_path": f"/kv/{namespace}/{note_key}",
            "status": response.status,
            "posted": _public_response(response),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    nonce = validate_nonce(args.nonce) if args.nonce else next_nonce()
    did, sig, greeting = signed_say(key, "lobby", nonce, args.greeting)
    published = {
        "base_url": base_url,
        "did": did,
        "profile_path": f"/kv/{namespace}/{note_key}",
        "profile_status": response.status,
        "profile_posted": _public_response(response),
    }
    try:
        greeting_response = post_json(
            base_url,
            "/r/lobby",
            {
                "did": did,
                "sig": sig,
                "nonce": nonce,
                "text": greeting,
            },
            args.timeout,
        )
    except HttpFailure as exc:
        # The profile write already landed. Record it so a retry is not mistaken for a
        # fresh start, and so the spent nonce is visible; the message carries no server body.
        receipt_write(
            args.receipt,
            {
                **published,
                "greeting": {
                    "room": "lobby",
                    "nonce": nonce,
                    "text": greeting,
                    "status": exc.status,
                    "error": str(exc),
                },
            },
        )
        raise
    result = {
        **published,
        "greeting": {
            "room": "lobby",
            "nonce": nonce,
            "text": greeting,
            "status": greeting_response.status,
            "posted": _public_response(greeting_response),
        },
    }
    receipt_write(args.receipt, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        run(parser().parse_args(argv))
    except (ContributorError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
