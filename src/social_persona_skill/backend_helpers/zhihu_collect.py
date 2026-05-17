from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)
ZH_SIGNIN_URL = "https://www.zhihu.com/signin"
ZH_HOME_URL = "https://www.zhihu.com"
ZH_SEARCH_URL = (
    "https://www.zhihu.com/search?q=python&search_source=Suggestion&type=content"
)


def _profile_token(profile_url: str) -> str:
    parts = [segment for segment in urlparse(profile_url).path.split("/") if segment]
    if len(parts) >= 2 and parts[0] == "people":
        return parts[1]
    raise RuntimeError(f"Could not parse a Zhihu profile token from {profile_url}.")


def _user_data_dir(repo_dir: Path) -> Path:
    candidates = [
        repo_dir / "browser_data" / "zhihu_user_data_dir",
        repo_dir / "browser_data" / "cdp_zhihu_user_data_dir",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


async def _open_page(page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    for state in ("load", "networkidle"):
        try:
            await page.wait_for_load_state(state, timeout=5000)
        except PlaywrightTimeoutError:
            continue


async def _wait_for_login(crawler, timeout_seconds: float) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            client = await crawler.create_zhihu_client(None)
            if await client.pong():
                current_user = await client.get_current_user_info()
                return {
                    "ok": True,
                    "authenticated_as": str(current_user.get("name") or "").strip(),
                    "url_token": str(current_user.get("url_token") or "").strip(),
                }
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError("Timed out waiting for Zhihu login to complete.")


async def _prepare_client(repo_dir: Path, *, headless: bool):
    sys.path.insert(0, str(repo_dir))
    from media_platform.zhihu.core import ZhihuCrawler

    user_data_dir = _user_data_dir(repo_dir)
    user_data_dir.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            viewport={"width": 1440, "height": 1600},
            user_agent=USER_AGENT,
        )
        try:
            page = await context.new_page()
            crawler = ZhihuCrawler()
            crawler.browser_context = context
            crawler.context_page = page
            crawler.user_agent = USER_AGENT
            yield crawler, page, context
        finally:
            await context.close()


async def _login(repo_dir: Path) -> dict[str, object]:
    sys.path.insert(0, str(repo_dir))
    from media_platform.zhihu.core import ZhihuCrawler

    user_data_dir = _user_data_dir(repo_dir)
    user_data_dir.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            viewport={"width": 1440, "height": 1600},
            user_agent=USER_AGENT,
        )
        try:
            page = await context.new_page()
            await _open_page(page, ZH_SIGNIN_URL)
            crawler = ZhihuCrawler()
            crawler.browser_context = context
            crawler.context_page = page
            crawler.user_agent = USER_AGENT
            return await _wait_for_login(crawler, timeout_seconds=1200)
        finally:
            await context.close()


async def _check(repo_dir: Path) -> dict[str, object]:
    sys.path.insert(0, str(repo_dir))
    from media_platform.zhihu.core import ZhihuCrawler

    user_data_dir = _user_data_dir(repo_dir)
    if not user_data_dir.exists():
        return {"ok": False, "reason": f"missing browser state at {user_data_dir}"}

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=True,
            viewport={"width": 1440, "height": 1600},
            user_agent=USER_AGENT,
        )
        try:
            page = await context.new_page()
            await _open_page(page, ZH_HOME_URL)
            crawler = ZhihuCrawler()
            crawler.browser_context = context
            crawler.context_page = page
            crawler.user_agent = USER_AGENT
            client = await crawler.create_zhihu_client(None)
            if not await client.pong():
                return {"ok": False, "reason": "login state is invalid or expired"}
            current_user = await client.get_current_user_info()
            return {
                "ok": True,
                "authenticated_as": str(current_user.get("name") or "").strip(),
                "url_token": str(current_user.get("url_token") or "").strip(),
            }
        finally:
            await context.close()


def _reset_browser_state(repo_dir: Path) -> None:
    user_data_dir = _user_data_dir(repo_dir)
    if user_data_dir.exists():
        shutil.rmtree(user_data_dir)


async def _collect(repo_dir: Path, profile_url: str) -> dict[str, object]:
    sys.path.insert(0, str(repo_dir))
    profile_token = _profile_token(profile_url)
    from media_platform.zhihu.core import ZhihuCrawler

    user_data_dir = _user_data_dir(repo_dir)
    if not user_data_dir.exists():
        raise RuntimeError(f"Missing Zhihu browser state at {user_data_dir}")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=True,
            viewport={"width": 1440, "height": 1600},
            user_agent=USER_AGENT,
        )
        try:
            page = await context.new_page()
            await _open_page(page, ZH_HOME_URL)

            crawler = ZhihuCrawler()
            crawler.browser_context = context
            crawler.context_page = page
            crawler.user_agent = USER_AGENT
            client = await crawler.create_zhihu_client(None)
            if not await client.pong():
                raise RuntimeError("Zhihu login state is missing or expired.")

            await _open_page(page, ZH_SEARCH_URL)
            await asyncio.sleep(2)
            await client.update_cookies(browser_context=context)

            creator = await client.get_creator_info(profile_token)
            if creator is None:
                raise RuntimeError(f"Zhihu creator profile could not be loaded for {profile_url}.")

            answers = await client.get_all_anwser_by_creator(creator, crawl_interval=0.0)
            articles = await client.get_all_articles_by_creator(creator, crawl_interval=0.0)
            return {
                "ok": True,
                "profile_url": profile_url,
                "profile_id": profile_token,
                "creator": _model_dump(creator),
                "answers": [_model_dump(item) for item in answers],
                "articles": [_model_dump(item) for item in articles],
            }
        finally:
            await context.close()


def _model_dump(value: Any) -> dict[str, object]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    raise RuntimeError(f"Unsupported Zhihu model type: {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["login", "check", "reset", "collect"], required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--profile-url")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).resolve()
    if args.mode == "login":
        payload = asyncio.run(_login(repo_dir))
    elif args.mode == "check":
        payload = asyncio.run(_check(repo_dir))
    elif args.mode == "reset":
        _reset_browser_state(repo_dir)
        payload = {"ok": True}
    else:
        if not args.profile_url:
            raise SystemExit("Zhihu collection requires --profile-url.")
        payload = asyncio.run(_collect(repo_dir, args.profile_url))

    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
