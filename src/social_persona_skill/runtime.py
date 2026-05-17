from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .models import Platform


class RuntimeError(Exception):
    pass


@dataclass(slots=True)
class RuntimeLayout:
    root: Path = Path(".runtime")

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    @property
    def auth_tokens_file(self) -> Path:
        return self.root / "auth_tokens"

    def backend_root(self, platform: Platform) -> Path:
        return self.root / "backends" / platform.value

    def backend_venv(self, platform: Platform) -> Path:
        return self.backend_root(platform) / "venv"

    def backend_python(self, platform: Platform) -> Path:
        return self.backend_venv(platform) / "bin" / "python"

    def xiaohongshu_repo(self) -> Path:
        return self.backend_root(Platform.XIAOHONGSHU) / "repo"

    def zhihu_repo(self) -> Path:
        return self.backend_root(Platform.ZHIHU) / "repo"

    def xiaohongshu_state_root(self) -> Path:
        return self.root / "state" / Platform.XIAOHONGSHU.value / "browser_state"

    def zhihu_state_root(self) -> Path:
        return self.root / "state" / Platform.ZHIHU.value / "browser_state"

    def instagram_state_root(self) -> Path:
        return self.root / "state" / Platform.INSTAGRAM.value

    def instagram_session_dir(self) -> Path:
        return self.instagram_state_root() / "session"

    def instagram_active_user_file(self) -> Path:
        return self.instagram_state_root() / "active_username"

    def xiaohongshu_run_root(self) -> Path:
        return self.root / "state" / Platform.XIAOHONGSHU.value / "runs"

    def zhihu_run_root(self) -> Path:
        return self.root / "state" / Platform.ZHIHU.value / "runs"

    def x_state_db(self) -> Path:
        return self.backend_root(Platform.X) / "scweet_state.db"

    def instagram_session_file(self, username: str) -> Path:
        return self.instagram_session_dir() / f"{username}.session"

    def ensure_base_dirs(self) -> None:
        self.backend_root(Platform.X).mkdir(parents=True, exist_ok=True)
        self.backend_root(Platform.GITHUB).mkdir(parents=True, exist_ok=True)
        self.backend_root(Platform.XIAOHONGSHU).mkdir(parents=True, exist_ok=True)
        self.backend_root(Platform.INSTAGRAM).mkdir(parents=True, exist_ok=True)
        self.backend_root(Platform.ZHIHU).mkdir(parents=True, exist_ok=True)
        self.xiaohongshu_state_root().mkdir(parents=True, exist_ok=True)
        self.xiaohongshu_run_root().mkdir(parents=True, exist_ok=True)
        self.zhihu_state_root().mkdir(parents=True, exist_ok=True)
        self.zhihu_run_root().mkdir(parents=True, exist_ok=True)
        self.instagram_session_dir().mkdir(parents=True, exist_ok=True)

    def read_x_auth_token(self) -> str:
        return self._read_auth_section_values("X (twitter)")[0]

    def read_github_token(self, *, required: bool = True) -> str | None:
        try:
            return self._read_auth_section_values("GitHub")[0]
        except RuntimeError:
            if required:
                raise
            return None

    def read_instagram_credentials(self) -> tuple[str, str]:
        values = self._read_auth_section_values("Instagram")
        mapping: dict[str, str] = {}
        positional: list[str] = []
        for item in values:
            if "=" in item:
                key, value = item.split("=", 1)
                mapping[key.strip().lower()] = value.strip()
            else:
                positional.append(item)

        username = mapping.get("username") or (positional[0] if positional else "")
        password = mapping.get("password") or (positional[1] if len(positional) > 1 else "")
        if not username or not password:
            raise RuntimeError(
                "Instagram credentials are incomplete. Expected a section like "
                "'# Instagram:' with 'username=...' and 'password=...'."
            )
        return username, password

    def write_instagram_active_username(self, username: str) -> None:
        self.instagram_active_user_file().parent.mkdir(parents=True, exist_ok=True)
        self.instagram_active_user_file().write_text(username.strip(), encoding="utf-8")

    def read_instagram_active_username(self) -> str:
        path = self.instagram_active_user_file()
        if not path.exists():
            raise RuntimeError(
                "Instagram session metadata is missing. Run 'backend login instagram' first."
            )
        username = path.read_text(encoding="utf-8").strip()
        if not username:
            raise RuntimeError(
                "Instagram session metadata is empty. Run 'backend login instagram' again."
            )
        return username

    def has_instagram_session(self) -> bool:
        path = self.instagram_active_user_file()
        if not path.exists():
            return False
        username = path.read_text(encoding="utf-8").strip()
        if not username:
            return False
        return self.instagram_session_file(username).exists()

    def read_instagram_session_file(self) -> tuple[str, Path]:
        username = self.read_instagram_active_username()
        session_file = self.instagram_session_file(username)
        if not session_file.exists():
            raise RuntimeError(
                f"Instagram session file not found at {session_file}. Run 'backend login instagram' first."
            )
        return username, session_file

    def _read_auth_section_values(self, section_name: str) -> list[str]:
        path = self.auth_tokens_file
        if not path.exists():
            raise RuntimeError(
                f"Auth token file not found at {path}. Expected a section like '# {section_name}:'."
            )

        content = path.read_text(encoding="utf-8")
        section_pattern = re.compile(rf"(?im)^\s*#\s*{re.escape(section_name)}\s*:\s*$")
        match = section_pattern.search(content)
        if match is None:
            raise RuntimeError(
                f"Auth token section '# {section_name}:' was not found in {path}."
            )

        tail = content[match.end() :].splitlines()
        values: list[str] = []
        for line in tail:
            raw = line.strip()
            if not raw:
                continue
            if raw.startswith("#"):
                break
            value = raw.split("#", 1)[0].strip()
            if value:
                values.append(value)

        if not values:
            raise RuntimeError(
                f"No auth values were found below '# {section_name}:' in {path}."
            )
        return values

    def has_xiaohongshu_login_state(self) -> bool:
        return self._has_browser_login_state(self.xiaohongshu_state_root(), Platform.XIAOHONGSHU)

    def has_zhihu_login_state(self) -> bool:
        return self._has_browser_login_state(self.zhihu_state_root(), Platform.ZHIHU)

    def _has_browser_login_state(self, state_root: Path, platform: Platform) -> bool:
        browser_data = state_root / "browser_data"
        if not browser_data.exists():
            return False

        user_data_dir_names = [
            f"{platform.value}_user_data_dir",
            f"cdp_{platform.value}_user_data_dir",
        ]
        if platform is Platform.XIAOHONGSHU:
            user_data_dir_names.append("cdp_xhs_user_data_dir")

        user_data_dirs = [browser_data / name for name in user_data_dir_names]
        for user_data_dir in user_data_dirs:
            cookies_db = user_data_dir / "Default" / "Cookies"
            local_state = user_data_dir / "Local State"
            network_cookies_db = user_data_dir / "Default" / "Network" / "Cookies"
            session_storage = user_data_dir / "Default" / "Session Storage"
            if cookies_db.exists() or network_cookies_db.exists() or (
                local_state.exists() and session_storage.exists()
            ):
                return True
        return False
