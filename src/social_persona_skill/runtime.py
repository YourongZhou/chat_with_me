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

    def xiaohongshu_state_root(self) -> Path:
        return self.root / "state" / Platform.XIAOHONGSHU.value / "browser_state"

    def xiaohongshu_run_root(self) -> Path:
        return self.root / "state" / Platform.XIAOHONGSHU.value / "runs"

    def x_state_db(self) -> Path:
        return self.backend_root(Platform.X) / "scweet_state.db"

    def ensure_base_dirs(self) -> None:
        self.backend_root(Platform.X).mkdir(parents=True, exist_ok=True)
        self.backend_root(Platform.XIAOHONGSHU).mkdir(parents=True, exist_ok=True)
        self.xiaohongshu_state_root().mkdir(parents=True, exist_ok=True)
        self.xiaohongshu_run_root().mkdir(parents=True, exist_ok=True)

    def read_x_auth_token(self) -> str:
        path = self.auth_tokens_file
        if not path.exists():
            raise RuntimeError(
                f"X auth token file not found at {path}. Expected a section like '# X (twitter):'."
            )

        content = path.read_text(encoding="utf-8")
        section_pattern = re.compile(
            r"(?im)^\s*#\s*X\s*\(twitter\)\s*:\s*$"
        )
        match = section_pattern.search(content)
        if match is None:
            raise RuntimeError(
                f"X auth token section '# X (twitter):' was not found in {path}."
            )

        tail = content[match.end() :].splitlines()
        for line in tail:
            raw = line.strip()
            if not raw:
                continue
            if raw.startswith("#"):
                break
            token = raw.split("#", 1)[0].strip()
            if token:
                return token

        raise RuntimeError(
            f"No X auth token value was found below '# X (twitter):' in {path}."
        )

    def has_xiaohongshu_login_state(self) -> bool:
        browser_data = self.xiaohongshu_state_root() / "browser_data"
        if not browser_data.exists():
            return False

        user_data_dirs = [
            browser_data / "xhs_user_data_dir",
            browser_data / "cdp_xhs_user_data_dir",
        ]
        for user_data_dir in user_data_dirs:
            cookies_db = user_data_dir / "Default" / "Cookies"
            local_state = user_data_dir / "Local State"
            if cookies_db.exists() or local_state.exists():
                return True
        return any(browser_data.iterdir())
