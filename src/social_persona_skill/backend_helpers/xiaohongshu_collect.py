from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)


def _parse_note_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] if path else ""


async def _collect_profile_notes(page: Page, profile_url: str) -> list[dict[str, object]]:
    responses: list[dict[str, object]] = []

    async def on_response(resp) -> None:
        url = resp.url
        if "edith.xiaohongshu.com/api/sns/web/v1/user_posted" not in url:
            return
        try:
            body = await resp.json()
        except Exception:
            return
        responses.append(body)

    page.on("response", on_response)
    await _open_profile_page(page, profile_url)

    notes: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    for body in responses:
        data = body.get("data", {}) if isinstance(body, dict) else {}
        items = data.get("notes", []) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            note_id = str(item.get("note_id") or "").strip()
            if not note_id or note_id in seen_ids:
                continue
            seen_ids.add(note_id)
            notes.append(
                {
                    "note_id": note_id,
                    "title": str(item.get("display_title") or "").strip(),
                    "xsec_token": str(item.get("xsec_token") or "").strip(),
                    "xsec_source": "pc_user",
                    "source_url": f"https://www.xiaohongshu.com/explore/{note_id}",
                    "liked_count": str((item.get("interact_info") or {}).get("liked_count") or "").strip(),
                }
            )

    if notes:
        return notes

    card_items = await page.evaluate(
        """
() => Array.from(document.querySelectorAll('section.note-item')).map((el) => {
  const cover = el.querySelector('a.cover[href*="xsec_token="]');
  const titleEl = el.querySelector('.title');
  return {
    href: cover ? cover.getAttribute('href') || '' : '',
    title: titleEl ? (titleEl.innerText || '').trim() : '',
  };
})
"""
    )

    for item in card_items:
        href = str(item.get("href") or "").strip()
        if not href:
            continue
        full_url = urljoin("https://www.xiaohongshu.com", href)
        parsed = urlparse(full_url)
        query = parse_qs(parsed.query)
        note_id = _parse_note_id(full_url)
        if not note_id or note_id in seen_ids:
            continue
        seen_ids.add(note_id)
        notes.append(
            {
                "note_id": note_id,
                "title": str(item.get("title") or "").strip(),
                "xsec_token": query.get("xsec_token", [""])[0],
                "xsec_source": query.get("xsec_source", ["pc_user"])[0] or "pc_user",
                "source_url": full_url,
                "liked_count": "",
            }
        )

    return notes


async def _open_profile_page(page: Page, profile_url: str) -> None:
    await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)

    for state in ("load", "networkidle"):
        try:
            await page.wait_for_load_state(state, timeout=5000)
        except PlaywrightTimeoutError:
            continue

    # Xiaohongshu frequently keeps background requests alive, so treat
    # a rendered app shell as good enough and let response listeners gather data.
    try:
        await page.wait_for_selector("#app", timeout=5000)
    except PlaywrightTimeoutError:
        pass

    await page.wait_for_timeout(1500)


async def _collect(repo_dir: Path, profile_url: str) -> dict[str, object]:
    sys.path.insert(0, str(repo_dir))

    from media_platform.xhs.core import XiaoHongShuCrawler
    from media_platform.xhs.help import parse_creator_info_from_url

    creator_info = parse_creator_info_from_url(profile_url)
    user_data_dir = repo_dir / "browser_data" / "xhs_user_data_dir"
    if not user_data_dir.exists():
        raise RuntimeError(f"Missing Xiaohongshu browser state at {user_data_dir}")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=True,
            viewport={"width": 1440, "height": 1600},
            user_agent=USER_AGENT,
        )
        try:
            page = await context.new_page()
            crawler = XiaoHongShuCrawler()
            crawler.browser_context = context
            crawler.context_page = page

            note_summaries = await _collect_profile_notes(page, profile_url)
            client = await crawler.create_xhs_client(None)
            profile = await client.get_creator_info(
                user_id=creator_info.user_id,
                xsec_token=creator_info.xsec_token,
                xsec_source=creator_info.xsec_source,
            )

            note_details: list[dict[str, object]] = []
            notes_error = ""
            for item in note_summaries:
                note_id = str(item.get("note_id") or "")
                xsec_token = str(item.get("xsec_token") or "")
                xsec_source = str(item.get("xsec_source") or "pc_user")
                title = str(item.get("title") or "").strip()
                source_url = str(item.get("source_url") or "")
                try:
                    detail = await client.get_note_by_id_from_html(
                        note_id,
                        xsec_source,
                        xsec_token,
                        enable_cookie=True,
                    )
                except Exception as exc:  # pragma: no cover - live-path fallback
                    notes_error = str(exc)
                    detail = None

                record = {
                    "note_id": note_id,
                    "title": title,
                    "desc": "",
                    "type": "",
                    "time": "",
                    "note_url": source_url or f"https://www.xiaohongshu.com/explore/{note_id}",
                }
                if isinstance(detail, dict):
                    record["title"] = str(detail.get("title") or title).strip()
                    record["desc"] = str(detail.get("desc") or "").strip()
                    record["type"] = str(detail.get("type") or "").strip()
                    record["time"] = str(detail.get("time") or "").strip()
                    record["note_url"] = str(detail.get("note_url") or record["note_url"]).strip()
                note_details.append(record)

            return {
                "profile_url": profile_url,
                "profile_id": creator_info.user_id,
                "profile": profile,
                "notes": note_details,
                "notes_error": notes_error,
            }
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--profile-url", required=True)
    args = parser.parse_args()

    payload = asyncio.run(_collect(Path(args.repo_dir).resolve(), args.profile_url))
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
