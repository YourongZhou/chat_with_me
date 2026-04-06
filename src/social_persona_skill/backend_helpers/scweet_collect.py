from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlparse

from Scweet import Scweet


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect profile info and tweets with Scweet.")
    parser.add_argument("--target", required=True, help="X profile URL or username")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of profile tweets to collect")
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

    username = _extract_username(args.target)
    client = Scweet(auth_token=auth_token, db_path=db_path)

    profile = {}
    tweets = []

    profiles = client.get_user_info([username])
    if profiles:
        profile = profiles[0]

    tweets = client.get_profile_tweets([username], limit=max(1, args.limit))
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


if __name__ == "__main__":
    main()
