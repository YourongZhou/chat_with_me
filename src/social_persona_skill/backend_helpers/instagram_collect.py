from __future__ import annotations

import argparse
import json
from itertools import islice
from pathlib import Path
from urllib.parse import urlparse

import instaloader


def _profile_username(profile_url: str) -> str:
    parts = [segment for segment in urlparse(profile_url).path.split("/") if segment]
    if not parts:
        raise RuntimeError(f"Could not parse an Instagram profile username from {profile_url}.")
    return parts[0]


def _build_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )


def _login(username: str, password: str, session_file: Path) -> dict[str, object]:
    loader = _build_loader()
    loader.login(username, password)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    loader.save_session_to_file(str(session_file))
    return {
        "ok": True,
        "authenticated_as": loader.test_login() or username,
        "session_file": str(session_file),
    }


def _collect(profile_url: str, login_user: str, session_file: Path, limit: int) -> dict[str, object]:
    loader = _build_loader()
    loader.load_session_from_file(login_user, str(session_file))
    authenticated_as = loader.test_login()
    if not authenticated_as:
        raise RuntimeError("Instagram session is invalid or expired. Run login again.")

    profile = instaloader.Profile.from_username(loader.context, _profile_username(profile_url))
    posts: list[dict[str, object]] = []
    for post in islice(profile.get_posts(), limit):
        posts.append(
            {
                "shortcode": post.shortcode,
                "caption": post.caption or "",
                "created_at": post.date_utc.replace(microsecond=0).isoformat(),
                "post_url": f"https://www.instagram.com/p/{post.shortcode}/",
            }
        )

    return {
        "ok": True,
        "authenticated_as": authenticated_as,
        "profile_url": profile_url,
        "profile": {
            "username": profile.username,
            "full_name": profile.full_name,
            "biography": profile.biography,
            "external_url": profile.external_url,
            "is_private": profile.is_private,
        },
        "posts": posts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["login", "collect"], required=True)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--login-user")
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--profile-url")
    parser.add_argument("--limit", type=int, default=24)
    args = parser.parse_args()

    session_file = Path(args.session_file).resolve()
    if args.mode == "login":
        if not args.username or not args.password:
            raise SystemExit("Instagram login requires --username and --password.")
        payload = _login(args.username, args.password, session_file)
    else:
        if not args.login_user or not args.profile_url:
            raise SystemExit("Instagram collection requires --login-user and --profile-url.")
        payload = _collect(args.profile_url, args.login_user, session_file, args.limit)

    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
