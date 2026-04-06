import asyncio
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from social_persona_skill.backends import MediaCrawlerBackend, ScweetBackend
from social_persona_skill.models import AccountInput, Platform
from social_persona_skill.runtime import RuntimeLayout


def _touch_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)


def test_read_x_auth_token(tmp_path: Path) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.root.mkdir(parents=True, exist_ok=True)
    runtime.auth_tokens_file.write_text(
        "# X (twitter):\nsecret_token_value # token\n",
        encoding="utf-8",
    )

    assert runtime.read_x_auth_token() == "secret_token_value"


def test_scweet_backend_normalizes_profile_and_posts(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    runtime.auth_tokens_file.write_text("# X (twitter):\nsecret_token_value # token\n", encoding="utf-8")
    _touch_executable(runtime.backend_python(Platform.X))

    backend = ScweetBackend(layout=runtime)
    fixture = {
        "ok": True,
        "username": "karpathy",
        "profile": {
            "name": "Andrej Karpathy",
            "description": "I like to train Deep Neural Nets on large datasets.",
            "created_at": "2025-01-01",
        },
        "tweets": [
            {
                "tweet_id": "1",
                "timestamp": "2025-01-02",
                "text": "LLM training in simple, raw C/CUDA",
                "tweet_url": "https://x.com/karpathy/status/1",
            },
            {
                "tweet_id": "2",
                "timestamp": "2025-01-03",
                "text": "The simplest, fastest repository for training GPTs.",
                "tweet_url": "https://x.com/karpathy/status/2",
            },
        ],
    }

    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 0, stdout=json.dumps(fixture), stderr="")

    monkeypatch.setattr(backend, "_run", fake_run)

    collected = backend.collect(AccountInput(platform=Platform.X, url="https://x.com/karpathy"))

    assert collected.account.backend == "scweet"
    assert collected.account.auth_mode == "auth_token"
    assert collected.account.profile_id == "karpathy"
    assert collected.account.display_name == "Andrej Karpathy"
    assert len(collected.corpus) == 3
    assert collected.corpus[0].item_type == "bio"
    assert collected.corpus[1].item_type == "post"
    assert "LLM training" in collected.corpus[1].text


def test_mediacrawler_backend_parses_helper_outputs(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    _touch_executable(runtime.backend_python(Platform.XIAOHONGSHU))
    (runtime.xiaohongshu_repo() / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (runtime.xiaohongshu_repo() / "main.py").write_text("", encoding="utf-8")
    browser_data = runtime.xiaohongshu_state_root() / "browser_data" / "cdp_xhs_user_data_dir"
    browser_data.mkdir(parents=True, exist_ok=True)

    backend = MediaCrawlerBackend(layout=runtime)

    def fake_run(cmd, **kwargs):
        payload = {
            "profile_url": "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9",
            "profile_id": "59b62f1550c4b47fbfa368d9",
            "profile": {
                "basicInfo": {
                    "nickname": "测试用户",
                    "desc": "这是主页简介",
                },
                "tags": [
                    {"name": "程序员"},
                    {"name": "南京"},
                ],
            },
            "notes": [
                {
                    "note_id": "note-1",
                    "title": "第一篇笔记",
                    "desc": "这是正文内容",
                    "time": "2026-01-01",
                    "note_url": "https://www.xiaohongshu.com/explore/note-1?xsec_token=secret&xsec_source=pc_user",
                }
            ],
        }
        return CompletedProcess(cmd, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")

    monkeypatch.setattr(backend, "_run", fake_run)

    collected = backend.collect(
        AccountInput(
            platform=Platform.XIAOHONGSHU,
            url="https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9",
        )
    )

    assert collected.account.backend == "mediacrawler"
    assert collected.account.auth_mode == "browser_state"
    assert collected.account.profile_id == "59b62f1550c4b47fbfa368d9"
    assert collected.account.display_name == "测试用户"
    assert "这是主页简介" in collected.account.profile_summary
    assert len(collected.corpus) == 2
    assert collected.corpus[0].item_type == "bio"
    assert collected.corpus[1].item_type == "post"
    assert "第一篇笔记" in collected.corpus[1].text
    assert collected.corpus[1].source_url == "https://www.xiaohongshu.com/explore/note-1"


def test_xiaohongshu_open_profile_page_tolerates_networkidle_timeout(monkeypatch) -> None:
    pytest.importorskip("playwright.async_api")
    from social_persona_skill.backend_helpers import xiaohongshu_collect as helper

    class FakeTimeoutError(Exception):
        pass

    class FakePage:
        def __init__(self) -> None:
            self.goto_calls: list[tuple[str, str, int]] = []
            self.load_states: list[str] = []
            self.selectors: list[str] = []
            self.wait_timeouts: list[int] = []

        async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
            self.goto_calls.append((url, wait_until, timeout))

        async def wait_for_load_state(self, state: str, *, timeout: int) -> None:
            self.load_states.append(state)
            if state == "networkidle":
                raise FakeTimeoutError("background requests never stopped")

        async def wait_for_selector(self, selector: str, *, timeout: int) -> None:
            self.selectors.append(selector)

        async def wait_for_timeout(self, timeout_ms: int) -> None:
            self.wait_timeouts.append(timeout_ms)

    monkeypatch.setattr(helper, "PlaywrightTimeoutError", FakeTimeoutError)
    page = FakePage()

    asyncio.run(helper._open_profile_page(page, "https://www.xiaohongshu.com/user/profile/test"))

    assert page.goto_calls == [
        ("https://www.xiaohongshu.com/user/profile/test", "domcontentloaded", 60000)
    ]
    assert page.load_states == ["load", "networkidle"]
    assert page.selectors == ["#app"]
    assert page.wait_timeouts == [1500]
