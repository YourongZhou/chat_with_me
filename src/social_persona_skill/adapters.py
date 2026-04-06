from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from pathlib import Path
from shutil import which
from subprocess import run
from typing import Protocol
from urllib.parse import urlparse
import json
import os
import re

from bs4 import BeautifulSoup
import requests

from .models import AccountInput, AccountRecord, Platform


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 social-persona-skill/0.1"
)


@dataclass(slots=True)
class FetchResult:
    accessible: bool
    fetch_status: str
    display_name: str = ""
    profile_summary: str = ""
    text_samples: list[str] | None = None
    collector: str = "unknown"
    raw_html: str = ""


class Adapter(Protocol):
    platform: Platform

    def collect(self, account: AccountInput, timeout_seconds: float) -> AccountRecord:
        ...


class GitHubAdapter:
    platform = Platform.GITHUB

    def collect(self, account: AccountInput, timeout_seconds: float) -> AccountRecord:
        profile_id = _profile_id(account)
        result = _fetch_html(account.url, timeout_seconds)
        if result.accessible:
            parsed = _parse_github_profile(result)
        else:
            parsed = FetchResult(
                accessible=False,
                fetch_status=result.fetch_status,
                collector=result.collector,
            )

        return AccountRecord(
            platform=account.platform,
            url=account.url,
            profile_id=profile_id,
            accessible=parsed.accessible,
            fetch_status=parsed.fetch_status,
            display_name=parsed.display_name or profile_id,
            profile_summary=parsed.profile_summary,
            text_samples=parsed.text_samples or [],
            collector=parsed.collector,
        )


class GenericTextAdapter:
    def __init__(self, platform: Platform, env_command_var: str) -> None:
        self.platform = platform
        self.env_command_var = env_command_var

    def collect(self, account: AccountInput, timeout_seconds: float) -> AccountRecord:
        profile_id = _profile_id(account)
        external = _collect_from_command(account, self.env_command_var, timeout_seconds)
        if external is not None:
            return AccountRecord(
                platform=account.platform,
                url=account.url,
                profile_id=profile_id,
                accessible=external.accessible,
                fetch_status=external.fetch_status,
                display_name=external.display_name or profile_id,
                profile_summary=external.profile_summary,
                text_samples=external.text_samples or [],
                collector=external.collector,
            )

        playwright = _collect_with_playwright(account.url, timeout_seconds)
        if playwright is not None:
            return AccountRecord(
                platform=account.platform,
                url=account.url,
                profile_id=profile_id,
                accessible=playwright.accessible,
                fetch_status=playwright.fetch_status,
                display_name=playwright.display_name or profile_id,
                profile_summary=playwright.profile_summary,
                text_samples=playwright.text_samples or [],
                collector=playwright.collector,
            )

        html = _fetch_html(account.url, timeout_seconds)
        embedded = _extract_embedded_state_samples(html.raw_html, account.platform)
        fallback_samples = embedded or _extract_text_samples_from_html(html.profile_summary)
        fallback_summary = html.profile_summary or (fallback_samples[0] if fallback_samples else "")
        return AccountRecord(
            platform=account.platform,
            url=account.url,
            profile_id=profile_id,
            accessible=html.accessible,
            fetch_status=html.fetch_status,
            display_name=html.display_name or profile_id,
            profile_summary=fallback_summary,
            text_samples=fallback_samples,
            collector=html.collector,
        )


def build_adapters() -> dict[Platform, Adapter]:
    return {
        Platform.GITHUB: GitHubAdapter(),
        Platform.X: GenericTextAdapter(Platform.X, "SOCIAL_PERSONA_X_CMD"),
        Platform.XIAOHONGSHU: GenericTextAdapter(
            Platform.XIAOHONGSHU,
            "SOCIAL_PERSONA_XIAOHONGSHU_CMD",
        ),
    }


def _fetch_html(url: str, timeout_seconds: float) -> FetchResult:
    try:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        return FetchResult(
            accessible=False,
            fetch_status=exc.__class__.__name__,
            collector="requests",
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    description_node = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta",
        attrs={"property": "og:description"},
    )
    description = _clean_text(description_node.get("content", "") if description_node else "")

    return FetchResult(
        accessible=response.ok,
        fetch_status=f"http_{response.status_code}",
        display_name=title,
        profile_summary=description,
        text_samples=_extract_text_samples_from_html(response.text),
        collector="requests",
        raw_html=response.text,
    )


def _parse_github_profile(result: FetchResult) -> FetchResult:
    soup = BeautifulSoup(result.raw_html, "html.parser")
    name_node = soup.select_one("span.p-name")
    login_node = soup.select_one("span.p-nickname")
    bio_node = soup.select_one("[itemprop='description']")
    readme_node = soup.select_one("#readme article")
    pinned_nodes = soup.select("li.pinned-item-list-item")

    display_name = _clean_text(
        " ".join(
            item
            for item in [
                name_node.get_text(" ", strip=True) if name_node else "",
                f"({login_node.get_text(' ', strip=True)})" if login_node else "",
            ]
            if item
        )
    ) or result.display_name.replace("· GitHub", "").strip()

    profile_bits = []
    if bio_node:
        profile_bits.append(_clean_text(bio_node.get_text(" ", strip=True)))
    if result.profile_summary and result.profile_summary not in profile_bits:
        profile_bits.append(result.profile_summary)

    text_samples = []
    if bio_node:
        text_samples.extend(_split_samples(bio_node.get_text(" ", strip=True), limit=2))
    if readme_node:
        text_samples.extend(_split_samples(readme_node.get_text("\n", strip=True), limit=4))
    for pinned in pinned_nodes[:4]:
        text_samples.extend(_split_samples(pinned.get_text("\n", strip=True), limit=2))
    if not text_samples:
        text_samples = _extract_text_samples_from_html(result.raw_html)

    return FetchResult(
        accessible=True,
        fetch_status=result.fetch_status,
        display_name=display_name,
        profile_summary=" ".join(_dedupe_strings(profile_bits)[:2]),
        text_samples=_dedupe_strings(text_samples)[:8],
        collector="requests",
        raw_html=result.raw_html,
    )


def _collect_from_command(
    account: AccountInput,
    env_command_var: str,
    timeout_seconds: float,
) -> FetchResult | None:
    command = os.getenv(env_command_var, "").strip()
    if not command:
        return None

    cmd = [part for part in command.split(" ") if part] + [account.url]
    completed = run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(int(timeout_seconds), 1) * 4,
    )
    if completed.returncode != 0:
        return FetchResult(
            accessible=False,
            fetch_status=f"collector_exit_{completed.returncode}",
            collector=Path(cmd[0]).name,
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return FetchResult(
            accessible=False,
            fetch_status="collector_invalid_json",
            collector=Path(cmd[0]).name,
        )

    return FetchResult(
        accessible=bool(payload.get("accessible", True)),
        fetch_status=str(payload.get("fetch_status", "ok")),
        display_name=str(payload.get("display_name", "")),
        profile_summary=str(payload.get("profile_summary", "")),
        text_samples=[str(item) for item in payload.get("text_samples", [])],
        collector=Path(cmd[0]).name,
    )


def _collect_with_playwright(url: str, timeout_seconds: float) -> FetchResult | None:
    if which("python") is None:
        return None

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return None

    storage_state = os.getenv("SOCIAL_PERSONA_PLAYWRIGHT_STORAGE_STATE", "").strip() or None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context_args = {"storage_state": storage_state} if storage_state else {}
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            title = _clean_text(page.title())
            text = _clean_text(page.locator("body").inner_text(timeout=3000))
            context.close()
            browser.close()
    except Exception as exc:
        return FetchResult(
            accessible=False,
            fetch_status=exc.__class__.__name__,
            collector="playwright",
        )

    return FetchResult(
        accessible=bool(text),
        fetch_status="dom_ok" if text else "dom_empty",
        display_name=title,
        profile_summary=text[:400],
        text_samples=_split_samples(text),
        collector="playwright",
    )


def _profile_id(account: AccountInput) -> str:
    parsed = urlparse(account.url)
    path = parsed.path.strip("/")
    if account.platform in {Platform.X, Platform.GITHUB} and path:
        return path.split("/")[-1].lower()
    if account.platform is Platform.XIAOHONGSHU:
        match = re.search(r"/profile/([^/]+)", parsed.path)
        if match:
            return match.group(1).lower()
    return path.lower() or parsed.netloc.lower()


def _extract_text_samples_from_html(text: str) -> list[str]:
    cleaned = _clean_text(text)
    return _split_samples(cleaned)


def _extract_embedded_state_samples(html: str, platform: Platform) -> list[str]:
    if "__INITIAL_STATE__" not in html:
        return []

    match = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;\s*</script>", html, re.S)
    if not match:
        idx = html.find("__INITIAL_STATE__")
        snippet = html[idx : idx + 250000] if idx != -1 else ""
    else:
        snippet = match.group(1)

    snippet = snippet.replace(":undefined", ":null")
    snippet = snippet.replace(":!0", ":true").replace(":!1", ":false")
    snippet = re.sub(r":\s*undefined\b", ": null", snippet)
    snippet = re.sub(r":\s*NaN\b", ": null", snippet)
    snippet = re.sub(r":\s*Infinity\b", ": null", snippet)

    try:
        payload = json.loads(snippet)
    except Exception:
        return []

    values = _collect_string_values(payload)
    cleaned = [_clean_text(item) for item in values]
    filtered = []
    for item in cleaned:
        lowered = item.lower()
        if len(item) < 12 and not _contains_cjk(item):
            continue
        if _looks_like_config_token(item):
            continue
        if platform is Platform.X and any(token in lowered for token in ["twitter", "x corp", "feature_switch", "responsive-web", "api.x.com", "commerceitems"]):
            continue
        if platform is Platform.XIAOHONGSHU and any(token in item for token in ["小红书_沪", "经营许可证", "互联网", "举报", "备案", "algorithm", "searchInputImageConfig"]):
            continue
        if re.fullmatch(r"[\w\-/:. ]+", item) and " " not in item and "/" in item:
            continue
        if not (_contains_cjk(item) or " " in item or re.search(r"[.!?;,，。！？；：]", item)):
            continue
        filtered.append(item)
    return _dedupe_strings(filtered)[:8]


def _split_samples(text: str, *, limit: int = 8) -> list[str]:
    if not text:
        return []
    lines = [_clean_text(item) for item in re.split(r"[\r\n]+", text)]
    lines = [item for item in lines if len(item) >= 24]
    return lines[:limit]


def _clean_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _dedupe_strings(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _collect_string_values(payload: object) -> list[str]:
    values: list[str] = []
    if isinstance(payload, str):
        values.append(payload)
        return values
    if isinstance(payload, list):
        for item in payload:
            values.extend(_collect_string_values(item))
        return values
    if isinstance(payload, dict):
        for item in payload.values():
            values.extend(_collect_string_values(item))
    return values


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def _looks_like_config_token(text: str) -> bool:
    return (
        bool(re.fullmatch(r"[A-Za-z0-9_.-]+", text))
        and " " not in text
        and not _contains_cjk(text)
    )
