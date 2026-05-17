import asyncio
import importlib
import json
import socket
import sys
import types
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from social_persona_skill.backends import (
    BackendError,
    DEFAULT_X_DAILY_REQUESTS_LIMIT,
    DEFAULT_X_DAILY_TWEETS_LIMIT,
    DEFAULT_X_LIMIT,
    DEFAULT_X_MAX_EMPTY_PAGES,
    GitHubBackend,
    InstaloaderBackend,
    MediaCrawlerBackend,
    ScweetBackend,
    ZhihuMediaCrawlerBackend,
)
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


def test_read_github_token(tmp_path: Path) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.root.mkdir(parents=True, exist_ok=True)
    runtime.auth_tokens_file.write_text(
        "# GitHub:\nghp_secret_token_value\n",
        encoding="utf-8",
    )

    assert runtime.read_github_token() == "ghp_secret_token_value"
    assert runtime.read_github_token(required=False) == "ghp_secret_token_value"


def test_read_instagram_credentials(tmp_path: Path) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.root.mkdir(parents=True, exist_ok=True)
    runtime.auth_tokens_file.write_text(
        "# Instagram:\nusername=my_login\npassword=super_secret\n",
        encoding="utf-8",
    )

    assert runtime.read_instagram_credentials() == ("my_login", "super_secret")


def test_runtime_zhihu_login_state_requires_cookie_artifacts(tmp_path: Path) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    user_data_dir = runtime.zhihu_state_root() / "browser_data" / "zhihu_user_data_dir"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    (user_data_dir / "Local State").write_text("{}", encoding="utf-8")

    assert runtime.has_zhihu_login_state() is False

    cookies_db = user_data_dir / "Default" / "Cookies"
    cookies_db.parent.mkdir(parents=True, exist_ok=True)
    cookies_db.write_text("", encoding="utf-8")

    assert runtime.has_zhihu_login_state() is True


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
    observed: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        observed["cmd"] = cmd
        observed["env"] = kwargs.get("env")
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
    assert observed["cmd"] == [
        str(runtime.backend_python(Platform.X)),
        str(backend._scweet_helper()),
        "--target",
        "https://x.com/karpathy",
        "--limit",
        str(DEFAULT_X_LIMIT),
        "--mode",
        "collect",
    ]
    assert observed["env"] == {
        "SOCIAL_PERSONA_X_AUTH_TOKEN": "secret_token_value",
        "SOCIAL_PERSONA_X_DB_PATH": str(runtime.x_state_db()),
        "SOCIAL_PERSONA_X_DAILY_REQUESTS_LIMIT": str(DEFAULT_X_DAILY_REQUESTS_LIMIT),
        "SOCIAL_PERSONA_X_DAILY_TWEETS_LIMIT": str(DEFAULT_X_DAILY_TWEETS_LIMIT),
        "SOCIAL_PERSONA_X_MAX_EMPTY_PAGES": str(DEFAULT_X_MAX_EMPTY_PAGES),
    }


def test_backend_run_reports_timeout(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    backend = ScweetBackend(layout=runtime)

    def fake_run(*args, **kwargs):
        raise TimeoutExpired(cmd=["sleep", "1"], timeout=3)

    monkeypatch.setattr("social_persona_skill.backends.run", fake_run)

    with pytest.raises(BackendError, match="command timed out after 3s: sleep 1"):
        backend._run(["sleep", "1"], timeout=3)


def test_backend_run_uses_lossy_utf8_decode_for_subprocess_output(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    backend = ScweetBackend(layout=runtime)
    observed: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        observed.update(kwargs)
        return CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("social_persona_skill.backends.run", fake_run)

    backend._run(["echo", "ok"])

    assert observed["text"] is True
    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"


def test_scweet_helper_fails_fast_without_network(monkeypatch) -> None:
    fake_scweet_module = types.ModuleType("Scweet")
    fake_scweet_module.Scweet = object
    fake_scweet_config_module = types.ModuleType("Scweet.config")

    class FakeScweetConfig:
        def __init__(self, **kwargs):
            self.max_empty_pages = kwargs.get("max_empty_pages", 3)

    fake_scweet_config_module.ScweetConfig = FakeScweetConfig
    monkeypatch.setitem(sys.modules, "Scweet", fake_scweet_module)
    monkeypatch.setitem(sys.modules, "Scweet.config", fake_scweet_config_module)
    scweet_helper = importlib.import_module("social_persona_skill.backend_helpers.scweet_collect")

    def fake_create_connection(*args, **kwargs):
        raise OSError("network is unreachable")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    with pytest.raises(SystemExit, match="network access to x.com:443 is unavailable"):
        scweet_helper._assert_x_network_available()


def test_github_backend_normalizes_profile_repos_and_events(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    backend = GitHubBackend(layout=runtime)

    def fake_request_json(path, *, params=None, token=None):
        if path == "/users/karpathy":
            return {
                "login": "karpathy",
                "name": "Andrej Karpathy",
                "bio": "I like to train Deep Neural Nets on large datasets.",
                "company": "OpenAI",
                "location": "San Francisco",
                "blog": "https://karpathy.ai",
                "followers": 999,
                "following": 12,
                "public_repos": 42,
                "updated_at": "2026-01-01T00:00:00Z",
            }
        if path == "/users/karpathy/repos":
            return [
                {
                    "name": "nanoGPT",
                    "full_name": "karpathy/nanoGPT",
                    "html_url": "https://github.com/karpathy/nanoGPT",
                    "description": "The simplest, fastest repository for training GPTs.",
                    "topics": ["llm", "pytorch"],
                    "fork": False,
                    "pushed_at": "2026-01-03T00:00:00Z",
                },
                {
                    "name": "forked",
                    "full_name": "karpathy/forked",
                    "html_url": "https://github.com/karpathy/forked",
                    "description": "should be skipped",
                    "topics": [],
                    "fork": True,
                    "pushed_at": "2026-01-04T00:00:00Z",
                },
            ]
        if path == "/users/karpathy/events/public":
            return [
                {
                    "id": "evt-1",
                    "type": "PushEvent",
                    "repo": {"name": "karpathy/llm.c"},
                    "created_at": "2026-01-04T00:00:00Z",
                    "payload": {
                        "commits": [
                            {"sha": "abc123", "message": "refactor dataloader for packed sequences"},
                            {"sha": "def456", "message": "improve eval logging"},
                        ]
                    },
                },
                {
                    "id": "evt-2",
                    "type": "PullRequestEvent",
                    "created_at": "2026-01-05T00:00:00Z",
                    "payload": {
                        "pull_request": {
                            "title": "add fp8 training experiment",
                            "body": "initial pass for hopper kernels",
                            "html_url": "https://github.com/karpathy/llm.c/pull/10",
                        }
                    },
                },
            ]
        raise AssertionError(path)

    def fake_request_text(path, *, token=None, accept=None, allow_not_found=False):
        assert path == "/repos/karpathy/nanoGPT/readme"
        return "# nanoGPT\n\nTrain GPTs in simple, raw code."

    monkeypatch.setattr(backend, "_request_json", fake_request_json)
    monkeypatch.setattr(backend, "_request_text", fake_request_text)

    collected = backend.collect(AccountInput(platform=Platform.GITHUB, url="https://github.com/karpathy"))

    assert collected.account.backend == "github"
    assert collected.account.auth_mode == "public_api"
    assert collected.account.profile_id == "karpathy"
    assert collected.account.display_name == "Andrej Karpathy"
    assert "OpenAI" in collected.account.profile_summary
    assert len(collected.corpus) == 4
    assert collected.corpus[0].item_type == "bio"
    assert collected.corpus[1].source_url == "https://github.com/karpathy/llm.c/commit/abc123"
    assert "refactor dataloader" in collected.corpus[1].text
    assert collected.corpus[2].source_url == "https://github.com/karpathy/llm.c/pull/10"
    assert "fp8 training experiment" in collected.corpus[2].text
    assert collected.corpus[3].source_url == "https://github.com/karpathy/nanoGPT"
    assert "Train GPTs in simple, raw code." in collected.corpus[3].text


def test_instaloader_backend_normalizes_profile_and_posts(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    _touch_executable(runtime.backend_python(Platform.INSTAGRAM))
    runtime.write_instagram_active_username("collector_user")
    runtime.instagram_session_file("collector_user").write_text("session", encoding="utf-8")

    backend = InstaloaderBackend(layout=runtime)

    def fake_run(cmd, **kwargs):
        payload = {
            "ok": True,
            "authenticated_as": "collector_user",
            "profile_url": "https://www.instagram.com/tester/",
            "profile": {
                "username": "tester",
                "full_name": "Test Person",
                "biography": "Writes captions worth collecting.",
                "external_url": "https://example.com",
                "is_private": False,
            },
            "posts": [
                {
                    "shortcode": "abc123",
                    "caption": "First caption",
                    "created_at": "2026-01-02T03:04:05+00:00",
                    "post_url": "https://www.instagram.com/p/abc123/",
                },
                {
                    "shortcode": "def456",
                    "caption": "Second caption",
                    "created_at": "2026-01-03T03:04:05+00:00",
                    "post_url": "https://www.instagram.com/p/def456/",
                },
            ],
        }
        return CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(backend, "_run", fake_run)

    collected = backend.collect(
        AccountInput(platform=Platform.INSTAGRAM, url="https://www.instagram.com/tester/")
    )

    assert collected.account.backend == "instaloader"
    assert collected.account.auth_mode == "session_file"
    assert collected.account.profile_id == "tester"
    assert collected.account.display_name == "Test Person"
    assert "Writes captions" in collected.account.profile_summary
    assert len(collected.corpus) == 3
    assert collected.corpus[0].item_type == "bio"
    assert collected.corpus[1].item_type == "post"
    assert collected.corpus[1].source_url == "https://www.instagram.com/p/abc123/"


def test_mediacrawler_backend_parses_helper_outputs(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    _touch_executable(runtime.backend_python(Platform.XIAOHONGSHU))
    (runtime.xiaohongshu_repo() / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (runtime.xiaohongshu_repo() / "main.py").write_text("", encoding="utf-8")
    browser_data = runtime.xiaohongshu_state_root() / "browser_data" / "cdp_xhs_user_data_dir"
    cookies_db = browser_data / "Default" / "Cookies"
    cookies_db.parent.mkdir(parents=True, exist_ok=True)
    cookies_db.write_text("", encoding="utf-8")

    backend = MediaCrawlerBackend(layout=runtime)

    def fake_run(cmd, **kwargs):
        payload = {
            "profile_url": "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9",
            "profile_id": "59b62f1550c4b47fbfa368d9",
            "profile": {
                "basicInfo": {
                    "nickname": "test user",
                    "desc": "home page bio",
                },
                "tags": [
                    {"name": "engineer"},
                    {"name": "nanjing"},
                ],
            },
            "notes": [
                {
                    "note_id": "note-1",
                    "title": "first post",
                    "desc": "body text",
                    "time": "2026-01-01",
                    "note_url": "https://www.xiaohongshu.com/explore/note-1?xsec_token=secret&xsec_source=pc_user",
                }
            ],
        }
        return CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

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
    assert collected.account.display_name == "test user"
    assert "home page bio" in collected.account.profile_summary
    assert len(collected.corpus) == 2
    assert collected.corpus[0].item_type == "bio"
    assert collected.corpus[1].item_type == "post"
    assert "first post" in collected.corpus[1].text
    assert collected.corpus[1].source_url == "https://www.xiaohongshu.com/explore/note-1"


def test_zhihu_backend_parses_helper_outputs(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    _touch_executable(runtime.backend_python(Platform.ZHIHU))
    runtime.zhihu_repo().mkdir(parents=True, exist_ok=True)
    user_data_dir = runtime.zhihu_state_root() / "browser_data" / "zhihu_user_data_dir"
    cookies_db = user_data_dir / "Default" / "Cookies"
    cookies_db.parent.mkdir(parents=True, exist_ok=True)
    cookies_db.write_text("", encoding="utf-8")

    backend = ZhihuMediaCrawlerBackend(layout=runtime)

    def fake_run(cmd, **kwargs):
        payload = {
            "ok": True,
            "profile_url": "https://www.zhihu.com/people/tester",
            "profile_id": "tester",
            "creator": {
                "user_nickname": "Test Author",
                "gender": "Male",
                "ip_location": "Jiangsu",
                "fans": 120,
                "follows": 45,
                "anwser_count": 7,
                "article_count": 2,
                "video_count": 0,
                "get_voteup_count": 999,
            },
            "answers": [
                {
                    "content_id": "answer-1",
                    "content_type": "answer",
                    "title": "How to collect text?",
                    "content_text": "Use the public API carefully.",
                    "created_time": 1710000000,
                    "content_url": "https://www.zhihu.com/question/1/answer/2?utm_psn=123",
                }
            ],
            "articles": [
                {
                    "content_id": "article-2",
                    "content_type": "article",
                    "title": "Long-form writing",
                    "content_text": "Articles should also be part of the persona corpus.",
                    "created_time": 1710000100,
                    "content_url": "https://zhuanlan.zhihu.com/p/42?utm_source=test",
                }
            ],
        }
        return CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(backend, "_run", fake_run)

    collected = backend.collect(
        AccountInput(platform=Platform.ZHIHU, url="https://www.zhihu.com/people/tester")
    )

    assert collected.account.backend == "mediacrawler"
    assert collected.account.auth_mode == "browser_state"
    assert collected.account.profile_id == "tester"
    assert collected.account.display_name == "Test Author"
    assert "Followers: 120" in collected.account.profile_summary
    assert len(collected.corpus) == 3
    assert collected.corpus[0].item_type == "bio"
    assert collected.corpus[1].item_type == "post"
    assert collected.corpus[1].source_url == "https://www.zhihu.com/question/1/answer/2"
    assert collected.corpus[2].source_url == "https://zhuanlan.zhihu.com/p/42"


def test_zhihu_login_resets_invalid_saved_state(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimeLayout(tmp_path / ".runtime")
    runtime.ensure_base_dirs()
    _touch_executable(runtime.backend_python(Platform.ZHIHU))
    runtime.zhihu_repo().mkdir(parents=True, exist_ok=True)

    user_data_dir = runtime.zhihu_state_root() / "browser_data" / "zhihu_user_data_dir"
    cookies_db = user_data_dir / "Default" / "Cookies"
    cookies_db.parent.mkdir(parents=True, exist_ok=True)
    cookies_db.write_text("", encoding="utf-8")

    backend = ZhihuMediaCrawlerBackend(layout=runtime)
    observed_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        observed_cmds.append(cmd)
        mode = cmd[cmd.index("--mode") + 1]
        if mode == "check":
            if len([item for item in observed_cmds if "--mode" in item and item[item.index("--mode") + 1] == "check"]) == 1:
                return CompletedProcess(cmd, 0, stdout=json.dumps({"ok": False}), stderr="")
            return CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True}), stderr="")
        if mode == "reset":
            return CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True}), stderr="")
        if mode == "login":
            return CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True}), stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(backend, "_run", fake_run)

    message = backend.login()

    assert "Login state is available under" in message
    modes = [cmd[cmd.index("--mode") + 1] for cmd in observed_cmds]
    assert modes == ["check", "reset", "login", "check"]


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
