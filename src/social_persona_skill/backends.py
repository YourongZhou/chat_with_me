from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess, run
from typing import Protocol
import json
import os
import re
import shutil
import sys
from urllib.parse import urlsplit, urlunsplit

from .models import AccountInput, AccountRecord, CollectedAccount, CorpusRecord, Platform
from .runtime import RuntimeError, RuntimeLayout


SCWEET_COMMIT = "5cd15c630c67356122642edf3a4c76d2d4950e08"
MEDIACRAWLER_COMMIT = "21b3f90c7d7797ad2d222e7d8f5e6537a8a5f9b0"
DEFAULT_X_USERNAME_CHECK = "karpathy"
DEFAULT_X_LIMIT = 100
DEFAULT_XIAOHONGSHU_LOGIN_URL = (
    "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"
)


class BackendError(RuntimeError):
    pass


class Backend(Protocol):
    platform: Platform
    name: str

    def bootstrap(self) -> str:
        ...

    def login(self) -> str:
        ...

    def collect(self, account: AccountInput) -> CollectedAccount:
        ...


@dataclass(slots=True)
class BaseBackend:
    layout: RuntimeLayout
    timeout_seconds: float = 120.0

    @property
    def platform(self) -> Platform:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    def bootstrap(self) -> str:
        raise NotImplementedError

    def login(self) -> str:
        raise NotImplementedError

    def collect(self, account: AccountInput) -> CollectedAccount:
        raise NotImplementedError

    def _run(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str | None] | None = None,
        timeout: float | None = None,
    ) -> CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            for key, value in env.items():
                if value is None:
                    merged_env.pop(key, None)
                    continue
                merged_env[key] = value
        completed = run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout or self.timeout_seconds,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            details = stderr or stdout or f"command failed with exit code {completed.returncode}"
            raise BackendError(details)
        return completed

    def _ensure_venv(self, venv_dir: Path) -> Path:
        python = venv_dir / "bin" / "python"
        if not python.exists():
            self._run([sys.executable, "-m", "venv", str(venv_dir)])
        return python

    def _utc_now(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()

    def _pip_env(self) -> dict[str, str | None]:
        return {
            "ALL_PROXY": None,
            "all_proxy": None,
            "HTTP_PROXY": None,
            "http_proxy": None,
            "HTTPS_PROXY": None,
            "https_proxy": None,
            "NO_PROXY": None,
            "no_proxy": None,
        }


class ScweetBackend(BaseBackend):
    platform = Platform.X
    name = "scweet"

    def bootstrap(self) -> str:
        self.layout.ensure_base_dirs()
        venv_dir = self.layout.backend_venv(self.platform)
        python = self._ensure_venv(venv_dir)
        self._run(
            [str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            env=self._pip_env(),
        )
        self._run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                f"git+https://github.com/Altimis/Scweet.git@{SCWEET_COMMIT}",
            ],
            env=self._pip_env(),
            timeout=600,
        )
        return f"Bootstrapped Scweet into {venv_dir}"

    def login(self) -> str:
        token = self.layout.read_x_auth_token()
        python = self.layout.backend_python(self.platform)
        if not python.exists():
            raise BackendError("X backend is not bootstrapped. Run 'backend bootstrap x' first.")

        helper = self._scweet_helper()
        completed = self._run(
            [
                str(python),
                str(helper),
                "--target",
                f"https://x.com/{DEFAULT_X_USERNAME_CHECK}",
                "--limit",
                "1",
                "--mode",
                "check",
            ],
            env={
                "SOCIAL_PERSONA_X_AUTH_TOKEN": token,
                "SOCIAL_PERSONA_X_DB_PATH": str(self.layout.x_state_db()),
            },
            timeout=180,
        )
        payload = json.loads(completed.stdout)
        return f"Validated X token for @{payload.get('username')} with {payload.get('tweet_count', 0)} tweet(s)."

    def collect(self, account: AccountInput) -> CollectedAccount:
        token = self.layout.read_x_auth_token()
        python = self.layout.backend_python(self.platform)
        if not python.exists():
            raise BackendError("X backend is not bootstrapped. Run 'backend bootstrap x' first.")

        helper = self._scweet_helper()
        completed = self._run(
            [
                str(python),
                str(helper),
                "--target",
                account.url,
                "--limit",
                str(DEFAULT_X_LIMIT),
                "--mode",
                "collect",
            ],
            env={
                "SOCIAL_PERSONA_X_AUTH_TOKEN": token,
                "SOCIAL_PERSONA_X_DB_PATH": str(self.layout.x_state_db()),
            },
            timeout=300,
        )
        payload = json.loads(completed.stdout)
        profile = payload.get("profile") or {}
        tweets = payload.get("tweets") or []
        if not tweets:
            raise BackendError(f"Scweet returned no timeline text for {account.url}.")

        username = str(payload.get("username") or self._profile_id(account.url))
        bio = str(profile.get("description") or "").strip()
        display_name = str(profile.get("name") or username).strip()
        corpus: list[CorpusRecord] = []
        collected_at = self._utc_now()
        if bio:
            corpus.append(
                CorpusRecord(
                    platform=self.platform,
                    account_url=account.url,
                    account_id=username,
                    item_id=f"{username}:bio",
                    item_type="bio",
                    text=bio,
                    created_at=str(profile.get("created_at") or ""),
                    source_url=account.url,
                    collector=self.name,
                    collected_at=collected_at,
                )
            )
        for tweet in tweets:
            text = str(tweet.get("text") or "").strip()
            if not text:
                continue
            corpus.append(
                CorpusRecord(
                    platform=self.platform,
                    account_url=account.url,
                    account_id=username,
                    item_id=str(tweet.get("tweet_id") or ""),
                    item_type="post",
                    text=text,
                    created_at=str(tweet.get("timestamp") or ""),
                    source_url=str(tweet.get("tweet_url") or account.url),
                    collector=self.name,
                    collected_at=collected_at,
                )
            )
        if not [row for row in corpus if row.item_type == "post"]:
            raise BackendError(f"Scweet returned no usable post corpus for {account.url}.")

        account_record = AccountRecord(
            platform=self.platform,
            url=account.url,
            profile_id=username,
            accessible=True,
            fetch_status="ok",
            display_name=display_name,
            profile_summary=bio,
            text_samples=[row.text for row in corpus[:5]],
            collector=self.name,
            backend=self.name,
            auth_mode="auth_token",
        )
        return CollectedAccount(account=account_record, corpus=corpus)

    def _scweet_helper(self) -> Path:
        return (
            Path(__file__).resolve().parent
            / "backend_helpers"
            / "scweet_collect.py"
        )

    def _profile_id(self, url: str) -> str:
        return url.rstrip("/").split("/")[-1]


class MediaCrawlerBackend(BaseBackend):
    platform = Platform.XIAOHONGSHU
    name = "mediacrawler"

    def bootstrap(self) -> str:
        self.layout.ensure_base_dirs()
        repo_dir = self.layout.xiaohongshu_repo()
        venv_dir = self.layout.backend_venv(self.platform)

        if not repo_dir.exists():
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            self._run(
                [
                    "git",
                    "clone",
                    "https://github.com/NanmiCoder/MediaCrawler",
                    str(repo_dir),
                ],
                timeout=600,
            )
            self._run(["git", "-C", str(repo_dir), "checkout", MEDIACRAWLER_COMMIT])

        python = self._ensure_venv(venv_dir)
        self._run(
            [str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            env=self._pip_env(),
        )
        self._run(
            [str(python), "-m", "pip", "install", "-r", str(repo_dir / "requirements.txt")],
            cwd=repo_dir,
            env=self._pip_env(),
            timeout=900,
        )
        self._run(
            [str(python), "-m", "playwright", "install", "chromium"],
            env=self._pip_env(),
            timeout=900,
        )
        return f"Bootstrapped MediaCrawler into {repo_dir}"

    def login(self) -> str:
        python = self.layout.backend_python(self.platform)
        repo_dir = self.layout.xiaohongshu_repo()
        if not python.exists() or not repo_dir.exists():
            raise BackendError("Xiaohongshu backend is not bootstrapped. Run 'backend bootstrap xiaohongshu' first.")

        self._prepare_browser_state_link(repo_dir)
        if self.layout.has_xiaohongshu_login_state():
            return f"Login state is already available under {self.layout.xiaohongshu_state_root()}"

        run_dir = self.layout.xiaohongshu_run_root() / "login"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        creator_url = os.getenv("SOCIAL_PERSONA_XHS_LOGIN_URL", DEFAULT_XIAOHONGSHU_LOGIN_URL)
        self._run(
            [
                str(python),
                str(repo_dir / "main.py"),
                "--platform",
                "xhs",
                "--lt",
                "qrcode",
                "--type",
                "creator",
                "--creator_id",
                creator_url,
                "--get_comment",
                "false",
                "--get_sub_comment",
                "false",
                "--headless",
                "false",
                "--save_data_option",
                "jsonl",
                "--save_data_path",
                str(run_dir),
                "--max_concurrency_num",
                "1",
            ],
            cwd=repo_dir,
            env=self._pip_env(),
            timeout=1800,
        )
        if not self.layout.has_xiaohongshu_login_state():
            raise BackendError("Xiaohongshu login did not produce reusable browser state.")
        return f"Login state is available under {self.layout.xiaohongshu_state_root()}"

    def collect(self, account: AccountInput) -> CollectedAccount:
        python = self.layout.backend_python(self.platform)
        repo_dir = self.layout.xiaohongshu_repo()
        if not python.exists() or not repo_dir.exists():
            raise BackendError("Xiaohongshu backend is not bootstrapped. Run 'backend bootstrap xiaohongshu' first.")
        if not self.layout.has_xiaohongshu_login_state():
            raise BackendError("Xiaohongshu login state is missing. Run 'backend login xiaohongshu' first.")

        self._prepare_browser_state_link(repo_dir)
        completed = self._run(
            [
                str(python),
                str(self._xiaohongshu_helper()),
                "--repo-dir",
                str(repo_dir),
                "--profile-url",
                account.url,
            ],
            cwd=repo_dir,
            env=self._pip_env(),
            timeout=1800,
        )
        payload = json.loads(completed.stdout)
        corpus = self._normalize_xiaohongshu_payload(payload, account)
        if not corpus:
            raise BackendError(f"MediaCrawler returned no usable Xiaohongshu corpus for {account.url}.")

        profile_id = self._profile_id(account.url)
        basic_info = ((payload.get("profile") or {}).get("basicInfo") or {})
        display_name = str(basic_info.get("nickname") or profile_id).strip() or profile_id
        profile_summary = self._creator_bio_text(payload)
        account_record = AccountRecord(
            platform=self.platform,
            url=account.url,
            profile_id=profile_id,
            accessible=True,
            fetch_status="ok",
            display_name=display_name,
            profile_summary=profile_summary,
            text_samples=[row.text for row in corpus[:5]],
            collector=self.name,
            backend=self.name,
            auth_mode="browser_state",
        )
        return CollectedAccount(account=account_record, corpus=corpus)

    def _xiaohongshu_helper(self) -> Path:
        return (
            Path(__file__).resolve().parent
            / "backend_helpers"
            / "xiaohongshu_collect.py"
        )

    def _normalize_xiaohongshu_payload(
        self,
        payload: dict[str, object],
        account: AccountInput,
    ) -> list[CorpusRecord]:
        profile_id = self._profile_id(account.url)
        collected_at = self._utc_now()
        corpus: list[CorpusRecord] = []

        bio_text = self._creator_bio_text(payload)
        if bio_text:
            corpus.append(
                CorpusRecord(
                    platform=self.platform,
                    account_url=account.url,
                    account_id=profile_id,
                    item_id=f"{profile_id}:bio",
                    item_type="bio",
                    text=bio_text,
                    created_at="",
                    source_url=account.url,
                    collector=self.name,
                    collected_at=collected_at,
                )
            )

        for item in payload.get("notes") or []:
            if not isinstance(item, dict):
                continue
            text = self._note_text(item)
            if not text:
                continue
            corpus.append(
                CorpusRecord(
                    platform=self.platform,
                    account_url=account.url,
                    account_id=profile_id,
                    item_id=str(item.get("note_id") or ""),
                    item_type="post",
                    text=text,
                    created_at=str(item.get("time") or ""),
                    source_url=self._sanitize_source_url(str(item.get("note_url") or account.url)),
                    collector=self.name,
                    collected_at=collected_at,
                )
            )
        return corpus

    def _creator_bio_text(self, payload: dict[str, object]) -> str:
        profile = payload.get("profile") or {}
        if not isinstance(profile, dict):
            return ""
        basic_info = profile.get("basicInfo") or {}
        if not isinstance(basic_info, dict):
            basic_info = {}
        tags = profile.get("tags") or []
        tag_names = []
        if isinstance(tags, list):
            for item in tags:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    if name:
                        tag_names.append(name)

        parts = [
            str(basic_info.get("nickname") or "").strip(),
            str(basic_info.get("desc") or "").strip(),
        ]
        if tag_names:
            parts.append("Tags: " + ", ".join(tag_names))
        return "\n\n".join(part for part in parts if part)

    def _normalize_xiaohongshu_run(
        self,
        run_dir: Path,
        account: AccountInput,
    ) -> list[CorpusRecord]:
        jsonl_dir = run_dir / "xhs" / "jsonl"
        if not jsonl_dir.exists():
            return []
        content_files = sorted(jsonl_dir.glob("creator_contents_*.jsonl"))
        if not content_files:
            return []

        profile_id = self._profile_id(account.url)
        collected_at = self._utc_now()
        corpus: list[CorpusRecord] = []
        for file_path in content_files:
            for line in file_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                text = self._note_text(item)
                if not text:
                    continue
                corpus.append(
                    CorpusRecord(
                        platform=self.platform,
                        account_url=account.url,
                        account_id=profile_id,
                        item_id=str(item.get("note_id") or ""),
                        item_type="post",
                        text=text,
                        created_at=str(item.get("time") or ""),
                        source_url=self._sanitize_source_url(str(item.get("note_url") or account.url)),
                        collector=self.name,
                        collected_at=collected_at,
                    )
                )
        return corpus

    def _note_text(self, item: dict[str, object]) -> str:
        title = str(item.get("title") or "").strip()
        desc = str(item.get("desc") or "").strip()
        if title and desc and title != desc:
            return f"{title}\n\n{desc}"
        return title or desc

    def _run_id(self, url: str) -> str:
        profile_id = self._profile_id(url)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{profile_id}-{timestamp}"

    def _prepare_browser_state_link(self, repo_dir: Path) -> None:
        state_root = self.layout.xiaohongshu_state_root()
        state_root.mkdir(parents=True, exist_ok=True)
        target = state_root / "browser_data"
        target.mkdir(parents=True, exist_ok=True)

        link_path = repo_dir / "browser_data"
        if link_path.is_symlink():
            if link_path.resolve() == target.resolve():
                return
            link_path.unlink()
        elif link_path.exists():
            raise BackendError(
                f"Unexpected path at {link_path}; expected a symlink to {target}. "
                "Move it away and retry xiaohongshu login."
            )
        link_path.symlink_to(target, target_is_directory=True)

    def _profile_id(self, url: str) -> str:
        match = re.search(r"/profile/([^/?#]+)", url)
        if match:
            return match.group(1)
        return url.rstrip("/").split("/")[-1]

    def _sanitize_source_url(self, url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class PlaceholderBackend(BaseBackend):
    def __init__(self, layout: RuntimeLayout, platform: Platform) -> None:
        super().__init__(layout=layout)
        self._platform = platform

    @property
    def platform(self) -> Platform:
        return self._platform

    @property
    def name(self) -> str:
        return f"{self._platform.value}-placeholder"

    def bootstrap(self) -> str:
        raise BackendError(f"{self._platform.value} backend is reserved but not implemented in this sprint.")

    def login(self) -> str:
        raise BackendError(f"{self._platform.value} backend is reserved but not implemented in this sprint.")

    def collect(self, account: AccountInput) -> CollectedAccount:
        raise BackendError(f"{self._platform.value} backend is reserved but not implemented in this sprint.")


def build_backend_registry(layout: RuntimeLayout) -> dict[Platform, Backend]:
    return {
        Platform.X: ScweetBackend(layout=layout),
        Platform.XIAOHONGSHU: MediaCrawlerBackend(layout=layout),
        Platform.INSTAGRAM: PlaceholderBackend(layout=layout, platform=Platform.INSTAGRAM),
        Platform.ZHIHU: PlaceholderBackend(layout=layout, platform=Platform.ZHIHU),
    }
