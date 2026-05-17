from __future__ import annotations

import argparse
import json
import os
import socket
from urllib.parse import urlparse

from Scweet import Scweet
from Scweet.config import ScweetConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect profile info and tweets with Scweet.")
    parser.add_argument("--target", required=True, help="X profile URL or username")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of profile tweets to collect; 0 means no helper-side cap",
    )
    parser.add_argument(
        "--mode",
        choices=("collect", "check"),
        default="collect",
        help="check validates the token with a minimal fetch; collect returns normalized payload",
    )
    args = parser.parse_args()

    auth_token = os.environ.get("SOCIAL_PERSONA_X_AUTH_TOKEN", "").strip()
    db_path = os.environ.get("SOCIAL_PERSONA_X_DB_PATH", "scweet_state.db")
    if not auth_token:
        raise SystemExit("SOCIAL_PERSONA_X_AUTH_TOKEN is required")

    _assert_x_network_available()

    username = _extract_username(args.target)
    config = ScweetConfig(
        daily_requests_limit=_read_positive_int_env("SOCIAL_PERSONA_X_DAILY_REQUESTS_LIMIT", 200),
        daily_tweets_limit=_read_positive_int_env("SOCIAL_PERSONA_X_DAILY_TWEETS_LIMIT", 4000),
        max_empty_pages=_read_positive_int_env("SOCIAL_PERSONA_X_MAX_EMPTY_PAGES", 3),
    )
    client = Scweet(auth_token=auth_token, db_path=db_path, config=config)

    profile = {}
    tweets = []

    profiles = client.get_user_info([username])
    if profiles:
        profile = profiles[0]

    limit = args.limit if args.limit > 0 else None
    tweets = client.get_profile_tweets([username], limit=limit, max_empty_pages=config.max_empty_pages)
    if args.mode == "check":
        if not tweets and not profile:
            raise SystemExit("token validation failed: no profile or tweets returned")
        print(
            json.dumps(
                {
                    "ok": True,
                    "username": username,
                    "tweet_count": len(tweets),
                    "profile": profile,
                },
                ensure_ascii=False,
            )
        )
        return

    payload = {
        "ok": True,
        "username": username,
        "profile": profile,
        "tweets": tweets,
    }
    print(json.dumps(payload, ensure_ascii=False))


def _extract_username(target: str) -> str:
    value = target.strip()
    if value.startswith("@"):
        return value[1:]
    if "://" not in value:
        return value.strip("/")
    parsed = urlparse(value)
    path = parsed.path.strip("/")
    if not path:
        raise ValueError(f"Unable to parse username from {target}")
    return path.split("/")[0]


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _assert_x_network_available(host: str = "x.com", port: int = 443, timeout_seconds: float = 5.0) -> None:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return
    except OSError as exc:
        raise SystemExit(
            f"network access to {host}:{port} is unavailable: {exc}. "
            "Run this collector in an environment with outbound network access."
        ) from exc


if __name__ == "__main__":
    main()
