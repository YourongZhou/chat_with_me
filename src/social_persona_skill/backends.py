from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired, run
from typing import Protocol
import json
import os
import re
import shutil
import sys
from urllib.parse import urlsplit, urlunsplit

import requests

from .models import AccountInput, AccountRecord, CollectedAccount, CorpusRecord, Platform
from .runtime import RuntimeError, RuntimeLayout


SCWEET_COMMIT = "5cd15c630c67356122642edf3a4c76d2d4950e08"
MEDIACRAWLER_COMMIT = "21b3f90c7d7797ad2d222e7d8f5e6537a8a5f9b0"
DEFAULT_X_USERNAME_CHECK = "karpathy"
DEFAULT_X_LIMIT = 0
DEFAULT_X_DAILY_REQUESTS_LIMIT = 200
DEFAULT_X_DAILY_TWEETS_LIMIT = 4000
DEFAULT_X_MAX_EMPTY_PAGES = 3
DEFAULT_GITHUB_REPO_LIMIT = 6
DEFAULT_GITHUB_EVENT_LIMIT = 20
DEFAULT_GITHUB_README_CHARS = 4000
DEFAULT_INSTAGRAM_LIMIT = 24
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
        try:
            completed = run(
                cmd,
                cwd=str(cwd) if cwd else None,
                env=merged_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=timeout or self.timeout_seconds,
            )
        except TimeoutExpired as exc:
            effective_timeout = timeout or self.timeout_seconds
            command = " ".join(cmd)
            raise BackendError(
                f"command timed out after {effective_timeout:.0f}s: {command}"
            ) from exc
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
            env=self._scweet_env(token),
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
            env=self._scweet_env(token),
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

    def _scweet_env(self, token: str) -> dict[str, str]:
        return {
            "SOCIAL_PERSONA_X_AUTH_TOKEN": token,
            "SOCIAL_PERSONA_X_DB_PATH": str(self.layout.x_state_db()),
            "SOCIAL_PERSONA_X_DAILY_REQUESTS_LIMIT": str(DEFAULT_X_DAILY_REQUESTS_LIMIT),
            "SOCIAL_PERSONA_X_DAILY_TWEETS_LIMIT": str(DEFAULT_X_DAILY_TWEETS_LIMIT),
            "SOCIAL_PERSONA_X_MAX_EMPTY_PAGES": str(DEFAULT_X_MAX_EMPTY_PAGES),
        }

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


class GitHubBackend(BaseBackend):
    platform = Platform.GITHUB
    name = "github"
    api_base_url = "https://api.github.com"

    def bootstrap(self) -> str:
        self.layout.ensure_base_dirs()
        return "GitHub backend uses the public GitHub API and requires no local bootstrap."

    def login(self) -> str:
        token = self._github_token(required=False)
        if not token:
            return (
                "GitHub backend can run in public API mode without login. "
                "Add a '# GitHub:' token to .runtime/auth_tokens for higher rate limits."
            )

        payload = self._request_json("/user", token=token)
        if not isinstance(payload, dict):
            raise BackendError("GitHub API returned an unexpected /user payload.")
        login = str(payload.get("login") or "").strip()
        if not login:
            raise BackendError("GitHub token validation succeeded but no login was returned.")
        return f"Validated GitHub token for @{login}."

    def collect(self, account: AccountInput) -> CollectedAccount:
        username = self._profile_id(account.url)
        token = self._github_token(required=False)
        user = self._request_json(f"/users/{username}", token=token)
        if not isinstance(user, dict):
            raise BackendError(f"GitHub API returned an unexpected profile payload for {account.url}.")

        repos_payload = self._request_json(
            f"/users/{username}/repos",
            params={"sort": "updated", "per_page": str(DEFAULT_GITHUB_REPO_LIMIT), "type": "owner"},
            token=token,
        )
        repos = repos_payload if isinstance(repos_payload, list) else []

        events_payload = self._request_json(
            f"/users/{username}/events/public",
            params={"per_page": str(DEFAULT_GITHUB_EVENT_LIMIT)},
            token=token,
        )
        events = events_payload if isinstance(events_payload, list) else []

        corpus = self._normalize_github_payload(user, repos, events, account, token=token)
        if not [row for row in corpus if row.item_type == "post"]:
            raise BackendError(f"GitHub returned no usable public text corpus for {account.url}.")

        profile_id = str(user.get("login") or username).strip() or username
        display_name = str(user.get("name") or profile_id).strip() or profile_id
        profile_summary = self._github_bio_text(user)
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
            auth_mode="api_token" if token else "public_api",
        )
        return CollectedAccount(account=account_record, corpus=corpus)

    def _normalize_github_payload(
        self,
        user: dict[str, object],
        repos: list[object],
        events: list[object],
        account: AccountInput,
        *,
        token: str | None,
    ) -> list[CorpusRecord]:
        profile_id = str(user.get("login") or self._profile_id(account.url)).strip() or self._profile_id(account.url)
        collected_at = self._utc_now()
        corpus: list[CorpusRecord] = []
        seen_ids: set[str] = set()

        bio_text = self._github_bio_text(user)
        if bio_text:
            item_id = f"{profile_id}:bio"
            seen_ids.add(item_id)
            corpus.append(
                CorpusRecord(
                    platform=self.platform,
                    account_url=account.url,
                    account_id=profile_id,
                    item_id=item_id,
                    item_type="bio",
                    text=bio_text,
                    created_at=str(user.get("updated_at") or ""),
                    source_url=account.url,
                    collector=self.name,
                    collected_at=collected_at,
                )
            )

        for event in events:
            if not isinstance(event, dict):
                continue
            for row in self._event_rows(event, account, profile_id, collected_at):
                if row.item_id in seen_ids or not row.text.strip():
                    continue
                seen_ids.add(row.item_id)
                corpus.append(row)

        for repo in repos:
            if not isinstance(repo, dict) or bool(repo.get("fork")):
                continue
            row = self._repo_row(repo, account, profile_id, collected_at, token=token)
            if row is None or row.item_id in seen_ids or not row.text.strip():
                continue
            seen_ids.add(row.item_id)
            corpus.append(row)

        return corpus

    def _event_rows(
        self,
        event: dict[str, object],
        account: AccountInput,
        profile_id: str,
        collected_at: str,
    ) -> list[CorpusRecord]:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            return []
        event_type = str(event.get("type") or "").strip()
        repo = event.get("repo") or {}
        if not isinstance(repo, dict):
            repo = {}
        repo_name = str(repo.get("name") or "").strip()
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        created_at = str(event.get("created_at") or "")

        if event_type == "PushEvent":
            commits = payload.get("commits") or []
            messages: list[str] = []
            head_sha = ""
            if isinstance(commits, list):
                for item in commits[:4]:
                    if not isinstance(item, dict):
                        continue
                    message = str(item.get("message") or "").strip()
                    if message and message not in messages:
                        messages.append(message)
                    if not head_sha:
                        head_sha = str(item.get("sha") or "").strip()
            text = "\n\n".join(messages).strip()
            if not text:
                return []
            source_url = account.url
            if repo_name and head_sha:
                source_url = f"https://github.com/{repo_name}/commit/{head_sha}"
            return [
                CorpusRecord(
                    platform=self.platform,
                    account_url=account.url,
                    account_id=profile_id,
                    item_id=f"event:{event_id}",
                    item_type="post",
                    text=text,
                    created_at=created_at,
                    source_url=source_url,
                    collector=self.name,
                    collected_at=collected_at,
                )
            ]

        if event_type == "PullRequestEvent":
            pull_request = payload.get("pull_request") or {}
            if isinstance(pull_request, dict):
                text = self._title_body_text(
                    str(pull_request.get("title") or ""),
                    str(pull_request.get("body") or ""),
                )
                if text:
                    return [
                        CorpusRecord(
                            platform=self.platform,
                            account_url=account.url,
                            account_id=profile_id,
                            item_id=f"event:{event_id}",
                            item_type="post",
                            text=text,
                            created_at=created_at,
                            source_url=str(pull_request.get("html_url") or account.url),
                            collector=self.name,
                            collected_at=collected_at,
                        )
                    ]

        if event_type in {"IssuesEvent", "IssueCommentEvent", "PullRequestReviewCommentEvent", "CommitCommentEvent"}:
            for key in ("comment", "issue", "pull_request"):
                item = payload.get(key) or {}
                if not isinstance(item, dict):
                    continue
                body = str(item.get("body") or "").strip()
                title = str(item.get("title") or "").strip()
                text = self._title_body_text(title, body) if title else body
                if text:
                    return [
                        CorpusRecord(
                            platform=self.platform,
                            account_url=account.url,
                            account_id=profile_id,
                            item_id=f"event:{event_id}",
                            item_type="post",
                            text=text,
                            created_at=created_at,
                            source_url=str(item.get("html_url") or account.url),
                            collector=self.name,
                            collected_at=collected_at,
                        )
                    ]

        if event_type == "ReleaseEvent":
            release = payload.get("release") or {}
            if isinstance(release, dict):
                text = self._title_body_text(
                    str(release.get("name") or release.get("tag_name") or ""),
                    str(release.get("body") or ""),
                )
                if text:
                    return [
                        CorpusRecord(
                            platform=self.platform,
                            account_url=account.url,
                            account_id=profile_id,
                            item_id=f"event:{event_id}",
                            item_type="post",
                            text=text,
                            created_at=created_at,
                            source_url=str(release.get("html_url") or account.url),
                            collector=self.name,
                            collected_at=collected_at,
                        )
                    ]

        return []

    def _repo_row(
        self,
        repo: dict[str, object],
        account: AccountInput,
        profile_id: str,
        collected_at: str,
        *,
        token: str | None,
    ) -> CorpusRecord | None:
        repo_name = str(repo.get("name") or "").strip()
        full_name = str(repo.get("full_name") or repo_name).strip()
        if not repo_name or not full_name:
            return None

        parts: list[str] = []
        description = str(repo.get("description") or "").strip()
        if description:
            parts.append(description)

        topics = repo.get("topics") or []
        if isinstance(topics, list):
            topic_names = [str(item).strip() for item in topics if str(item).strip()]
            if topic_names:
                parts.append("Topics: " + ", ".join(topic_names))

        readme_text = self._readme_text(full_name, token=token)
        if readme_text:
            parts.append(readme_text)

        text = "\n\n".join(part for part in parts if part).strip()
        if not text:
            return None

        return CorpusRecord(
            platform=self.platform,
            account_url=account.url,
            account_id=profile_id,
            item_id=f"repo:{full_name}",
            item_type="post",
            text=text,
            created_at=str(repo.get("pushed_at") or repo.get("updated_at") or ""),
            source_url=str(repo.get("html_url") or account.url),
            collector=self.name,
            collected_at=collected_at,
        )

    def _github_bio_text(self, user: dict[str, object]) -> str:
        parts = [
            str(user.get("name") or "").strip(),
            str(user.get("bio") or "").strip(),
        ]
        company = str(user.get("company") or "").strip()
        if company:
            parts.append(f"Company: {company}")
        location = str(user.get("location") or "").strip()
        if location:
            parts.append(f"Location: {location}")
        blog = str(user.get("blog") or "").strip()
        if blog:
            parts.append(f"Link: {blog}")

        stats: list[str] = []
        for label, key in (
            ("Followers", "followers"),
            ("Following", "following"),
            ("Public repos", "public_repos"),
            ("Public gists", "public_gists"),
        ):
            value = user.get(key)
            if value not in (None, "", 0):
                stats.append(f"{label}: {value}")
        if stats:
            parts.append(", ".join(stats))
        return "\n\n".join(part for part in parts if part)

    def _title_body_text(self, title: str, body: str) -> str:
        cleaned_title = title.strip()
        cleaned_body = body.strip()
        if cleaned_title and cleaned_body and cleaned_title != cleaned_body:
            return f"{cleaned_title}\n\n{cleaned_body}"
        return cleaned_title or cleaned_body

    def _readme_text(self, full_name: str, *, token: str | None) -> str:
        text = self._request_text(
            f"/repos/{full_name}/readme",
            token=token,
            accept="application/vnd.github.raw+json",
            allow_not_found=True,
        )
        return self._truncate_text(text, DEFAULT_GITHUB_README_CHARS)

    def _request_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        token: str | None = None,
    ) -> object:
        response = requests.get(
            f"{self.api_base_url}{path}",
            headers=self._github_headers(token=token),
            params=params,
            timeout=self.timeout_seconds,
        )
        self._raise_for_github_response(response, path)
        try:
            return response.json()
        except ValueError as exc:
            raise BackendError(f"GitHub API returned invalid JSON for {path}.") from exc

    def _request_text(
        self,
        path: str,
        *,
        token: str | None,
        accept: str,
        allow_not_found: bool = False,
    ) -> str:
        response = requests.get(
            f"{self.api_base_url}{path}",
            headers=self._github_headers(token=token, accept=accept),
            timeout=self.timeout_seconds,
        )
        if allow_not_found and response.status_code == 404:
            return ""
        self._raise_for_github_response(response, path)
        return response.text.strip()

    def _raise_for_github_response(self, response: requests.Response, path: str) -> None:
        if response.ok:
            return
        if response.status_code == 404:
            raise BackendError(f"GitHub resource was not found for API path {path}.")

        message = ""
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            message = str(payload.get("message") or "").strip()

        if response.status_code in {403, 429} and response.headers.get("X-RateLimit-Remaining") == "0":
            reset = response.headers.get("X-RateLimit-Reset", "").strip()
            details = "GitHub API rate limit exceeded"
            if reset:
                details += f"; reset at unix time {reset}"
            if message:
                details += f". {message}"
            raise BackendError(details + ".")

        details = message or response.text.strip() or f"HTTP {response.status_code}"
        raise BackendError(f"GitHub API request failed for {path}: {details}")

    def _github_headers(self, *, token: str | None, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "social-persona-skill/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _github_token(self, *, required: bool) -> str | None:
        env_token = os.getenv("SOCIAL_PERSONA_GITHUB_TOKEN", "").strip()
        if env_token:
            return env_token
        return self.layout.read_github_token(required=required)

    def _profile_id(self, url: str) -> str:
        match = re.search(r"github\.com/([^/?#]+)", url, re.I)
        if match:
            return match.group(1)
        return url.rstrip("/").split("/")[-1]

    def _truncate_text(self, text: str, limit: int) -> str:
        cleaned = text.strip()
        if len(cleaned) <= limit:
            return cleaned
        truncated = cleaned[:limit].rsplit("\n", 1)[0].strip()
        return truncated or cleaned[:limit].strip()


class InstaloaderBackend(BaseBackend):
    platform = Platform.INSTAGRAM
    name = "instaloader"

    def bootstrap(self) -> str:
        self.layout.ensure_base_dirs()
        venv_dir = self.layout.backend_venv(self.platform)
        python = self._ensure_venv(venv_dir)
        self._run(
            [str(python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
            env=self._pip_env(),
        )
        self._run(
            [str(python), "-m", "pip", "install", "instaloader"],
            env=self._pip_env(),
            timeout=600,
        )
        return f"Bootstrapped Instaloader into {venv_dir}"

    def login(self) -> str:
        username, password = self.layout.read_instagram_credentials()
        python = self.layout.backend_python(self.platform)
        if not python.exists():
            raise BackendError("Instagram backend is not bootstrapped. Run 'backend bootstrap instagram' first.")

        session_file = self.layout.instagram_session_file(username)
        helper = self._instagram_helper()
        completed = self._run(
            [
                str(python),
                str(helper),
                "--mode",
                "login",
                "--username",
                username,
                "--password",
                password,
                "--session-file",
                str(session_file),
            ],
            env=self._pip_env(),
            timeout=300,
        )
        payload = json.loads(completed.stdout)
        authenticated_as = str(payload.get("authenticated_as") or username).strip() or username
        canonical_session_file = self.layout.instagram_session_file(authenticated_as)
        if canonical_session_file != session_file and session_file.exists():
            canonical_session_file.parent.mkdir(parents=True, exist_ok=True)
            if canonical_session_file.exists():
                canonical_session_file.unlink()
            session_file.rename(canonical_session_file)
            session_file = canonical_session_file
        self.layout.write_instagram_active_username(authenticated_as)
        return f"Instagram session is available under {session_file}"

    def collect(self, account: AccountInput) -> CollectedAccount:
        python = self.layout.backend_python(self.platform)
        if not python.exists():
            raise BackendError("Instagram backend is not bootstrapped. Run 'backend bootstrap instagram' first.")

        login_user, session_file = self.layout.read_instagram_session_file()
        completed = self._run(
            [
                str(python),
                str(self._instagram_helper()),
                "--mode",
                "collect",
                "--login-user",
                login_user,
                "--session-file",
                str(session_file),
                "--profile-url",
                account.url,
                "--limit",
                str(DEFAULT_INSTAGRAM_LIMIT),
            ],
            env=self._pip_env(),
            timeout=600,
        )
        payload = json.loads(completed.stdout)
        corpus = self._normalize_instagram_payload(payload, account)
        if not [row for row in corpus if row.item_type == "post"]:
            raise BackendError(f"Instaloader returned no usable Instagram corpus for {account.url}.")

        profile = payload.get("profile") or {}
        if not isinstance(profile, dict):
            profile = {}
        profile_id = str(profile.get("username") or self._profile_id(account.url)).strip() or self._profile_id(account.url)
        display_name = str(profile.get("full_name") or profile_id).strip() or profile_id
        profile_summary = self._instagram_bio_text(profile)
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
            auth_mode="session_file",
        )
        return CollectedAccount(account=account_record, corpus=corpus)

    def _instagram_helper(self) -> Path:
        return Path(__file__).resolve().parent / "backend_helpers" / "instagram_collect.py"

    def _normalize_instagram_payload(
        self,
        payload: dict[str, object],
        account: AccountInput,
    ) -> list[CorpusRecord]:
        profile = payload.get("profile") or {}
        if not isinstance(profile, dict):
            profile = {}
        profile_id = str(profile.get("username") or self._profile_id(account.url)).strip() or self._profile_id(account.url)
        collected_at = self._utc_now()
        corpus: list[CorpusRecord] = []

        bio_text = self._instagram_bio_text(profile)
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

        for item in payload.get("posts") or []:
            if not isinstance(item, dict):
                continue
            caption = str(item.get("caption") or "").strip()
            if not caption:
                continue
            shortcode = str(item.get("shortcode") or "").strip()
            source_url = str(item.get("post_url") or "").strip()
            if not source_url and shortcode:
                source_url = f"https://www.instagram.com/p/{shortcode}/"
            corpus.append(
                CorpusRecord(
                    platform=self.platform,
                    account_url=account.url,
                    account_id=profile_id,
                    item_id=shortcode,
                    item_type="post",
                    text=caption,
                    created_at=str(item.get("created_at") or ""),
                    source_url=source_url or account.url,
                    collector=self.name,
                    collected_at=collected_at,
                )
            )
        return corpus

    def _instagram_bio_text(self, profile: dict[str, object]) -> str:
        parts = [
            str(profile.get("full_name") or "").strip(),
            str(profile.get("biography") or "").strip(),
        ]
        external_url = str(profile.get("external_url") or "").strip()
        if external_url:
            parts.append(f"Link: {external_url}")
        return "\n\n".join(part for part in parts if part)

    def _profile_id(self, url: str) -> str:
        match = re.search(r"instagram\.com/([^/?#]+)/?", url)
        if match:
            return match.group(1)
        return url.rstrip("/").split("/")[-1]


class ZhihuMediaCrawlerBackend(BaseBackend):
    platform = Platform.ZHIHU
    name = "mediacrawler"

    def bootstrap(self) -> str:
        self.layout.ensure_base_dirs()
        if shutil.which("node") is None:
            raise BackendError(
                "Zhihu backend requires Node.js on PATH because MediaCrawler uses ExecJS for request signing."
            )

        repo_dir = self.layout.zhihu_repo()
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
        repo_dir = self.layout.zhihu_repo()
        if not python.exists() or not repo_dir.exists():
            raise BackendError("Zhihu backend is not bootstrapped. Run 'backend bootstrap zhihu' first.")

        self._prepare_browser_state_link(repo_dir)
        if self.layout.has_zhihu_login_state():
            existing = self._run(
                [
                    str(python),
                    str(self._zhihu_helper()),
                    "--mode",
                    "check",
                    "--repo-dir",
                    str(repo_dir),
                ],
                cwd=repo_dir,
                env=self._pip_env(),
                timeout=300,
            )
            existing_payload = json.loads(existing.stdout)
            if existing_payload.get("ok"):
                return f"Login state is already available under {self.layout.zhihu_state_root()}"
            self._run(
                [
                    str(python),
                    str(self._zhihu_helper()),
                    "--mode",
                    "reset",
                    "--repo-dir",
                    str(repo_dir),
                ],
                cwd=repo_dir,
                env=self._pip_env(),
                timeout=120,
            )

        completed = self._run(
            [
                str(python),
                str(self._zhihu_helper()),
                "--mode",
                "login",
                "--repo-dir",
                str(repo_dir),
            ],
            cwd=repo_dir,
            env=self._pip_env(),
            timeout=1800,
        )
        payload = json.loads(completed.stdout)
        if not payload.get("ok") or not self.layout.has_zhihu_login_state():
            raise BackendError("Zhihu login did not produce reusable browser state.")

        validated = self._run(
            [
                str(python),
                str(self._zhihu_helper()),
                "--mode",
                "check",
                "--repo-dir",
                str(repo_dir),
            ],
            cwd=repo_dir,
            env=self._pip_env(),
            timeout=300,
        )
        validated_payload = json.loads(validated.stdout)
        if not validated_payload.get("ok"):
            raise BackendError(
                "Zhihu login completed, but the saved browser state is still invalid. "
                "Retry login and make sure the browser reaches a fully logged-in homepage."
            )
        return f"Login state is available under {self.layout.zhihu_state_root()}"

    def collect(self, account: AccountInput) -> CollectedAccount:
        python = self.layout.backend_python(self.platform)
        repo_dir = self.layout.zhihu_repo()
        if not python.exists() or not repo_dir.exists():
            raise BackendError("Zhihu backend is not bootstrapped. Run 'backend bootstrap zhihu' first.")
        if not self.layout.has_zhihu_login_state():
            raise BackendError("Zhihu login state is missing. Run 'backend login zhihu' first.")

        self._prepare_browser_state_link(repo_dir)
        completed = self._run(
            [
                str(python),
                str(self._zhihu_helper()),
                "--mode",
                "collect",
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
        corpus = self._normalize_zhihu_payload(payload, account)
        if not [row for row in corpus if row.item_type == "post"]:
            raise BackendError(f"MediaCrawler returned no usable Zhihu corpus for {account.url}.")

        creator = payload.get("creator") or {}
        if not isinstance(creator, dict):
            creator = {}
        profile_id = str(payload.get("profile_id") or self._profile_id(account.url)).strip() or self._profile_id(account.url)
        display_name = str(creator.get("user_nickname") or profile_id).strip() or profile_id
        profile_summary = self._creator_bio_text(creator)
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

    def _zhihu_helper(self) -> Path:
        return Path(__file__).resolve().parent / "backend_helpers" / "zhihu_collect.py"

    def _normalize_zhihu_payload(
        self,
        payload: dict[str, object],
        account: AccountInput,
    ) -> list[CorpusRecord]:
        profile_id = str(payload.get("profile_id") or self._profile_id(account.url)).strip() or self._profile_id(account.url)
        creator = payload.get("creator") or {}
        if not isinstance(creator, dict):
            creator = {}
        collected_at = self._utc_now()
        corpus: list[CorpusRecord] = []

        bio_text = self._creator_bio_text(creator)
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

        for bucket in ("answers", "articles"):
            for item in payload.get(bucket) or []:
                if not isinstance(item, dict):
                    continue
                text = self._content_text(item)
                if not text:
                    continue
                content_type = str(item.get("content_type") or bucket[:-1]).strip() or bucket[:-1]
                source_url = self._sanitize_source_url(str(item.get("content_url") or account.url))
                corpus.append(
                    CorpusRecord(
                        platform=self.platform,
                        account_url=account.url,
                        account_id=profile_id,
                        item_id=f"{content_type}:{item.get('content_id') or ''}",
                        item_type="post",
                        text=text,
                        created_at=str(item.get("created_time") or ""),
                        source_url=source_url,
                        collector=self.name,
                        collected_at=collected_at,
                    )
                )
        return corpus

    def _creator_bio_text(self, creator: dict[str, object]) -> str:
        parts = [str(creator.get("user_nickname") or "").strip()]
        gender = str(creator.get("gender") or "").strip()
        if gender and gender.lower() != "unknown":
            parts.append(f"Gender: {gender}")
        ip_location = str(creator.get("ip_location") or "").strip()
        if ip_location:
            parts.append(f"IP location: {ip_location}")

        stats: list[str] = []
        for label, key in (
            ("Followers", "fans"),
            ("Following", "follows"),
            ("Answers", "anwser_count"),
            ("Articles", "article_count"),
            ("Videos", "video_count"),
            ("Upvotes", "get_voteup_count"),
        ):
            value = creator.get(key)
            if value not in (None, "", 0):
                stats.append(f"{label}: {value}")
        if stats:
            parts.append(", ".join(stats))
        return "\n\n".join(part for part in parts if part)

    def _content_text(self, item: dict[str, object]) -> str:
        title = str(item.get("title") or "").strip()
        body = str(item.get("content_text") or "").strip()
        desc = str(item.get("desc") or "").strip()
        detail = body or desc
        if title and detail and title != detail:
            return f"{title}\n\n{detail}"
        return title or detail

    def _prepare_browser_state_link(self, repo_dir: Path) -> None:
        state_root = self.layout.zhihu_state_root()
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
                "Move it away and retry zhihu login."
            )
        link_path.symlink_to(target, target_is_directory=True)

    def _profile_id(self, url: str) -> str:
        match = re.search(r"/people/([^/?#]+)", url)
        if match:
            return match.group(1)
        return url.rstrip("/").split("/")[-1]

    def _sanitize_source_url(self, url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def build_backend_registry(layout: RuntimeLayout) -> dict[Platform, Backend]:
    return {
        Platform.X: ScweetBackend(layout=layout),
        Platform.GITHUB: GitHubBackend(layout=layout),
        Platform.XIAOHONGSHU: MediaCrawlerBackend(layout=layout),
        Platform.INSTAGRAM: InstaloaderBackend(layout=layout),
        Platform.ZHIHU: ZhihuMediaCrawlerBackend(layout=layout),
    }
