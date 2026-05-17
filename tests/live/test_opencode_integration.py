from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from social_persona_skill.models import AccountInput, AccountRecord, CollectedAccount, CorpusRecord, Platform
from social_persona_skill.workflow import PersonaWorkflow


class FakeBackend:
    def __init__(self, platform: Platform, dataset: dict[str, CollectedAccount]) -> None:
        self.platform = platform
        self.name = f"{platform.value}-fake"
        self.dataset = dataset

    def bootstrap(self) -> str:
        return f"bootstrapped {self.platform.value}"

    def login(self) -> str:
        return f"logged in {self.platform.value}"

    def collect(self, account: AccountInput) -> CollectedAccount:
        return self.dataset[account.url]


def _collection(
    *,
    platform: Platform,
    url: str,
    profile_id: str,
    display_name: str,
    profile_summary: str,
    posts: list[str],
) -> CollectedAccount:
    corpus = []
    if profile_summary:
        corpus.append(
            CorpusRecord(
                platform=platform,
                account_url=url,
                account_id=profile_id,
                item_id=f"{profile_id}:bio",
                item_type="bio",
                text=profile_summary,
                source_url=url,
                collector=f"{platform.value}-fake",
                collected_at="2026-01-01T00:00:00+00:00",
            )
        )
    for index, text in enumerate(posts, start=1):
        corpus.append(
            CorpusRecord(
                platform=platform,
                account_url=url,
                account_id=profile_id,
                item_id=f"{profile_id}:{index}",
                item_type="post",
                text=text,
                created_at=f"2026-01-{index:02d}",
                source_url=f"{url}/posts/{index}",
                collector=f"{platform.value}-fake",
                collected_at="2026-01-01T00:00:00+00:00",
            )
        )
    account = AccountRecord(
        platform=platform,
        url=url,
        profile_id=profile_id,
        accessible=True,
        fetch_status="ok",
        display_name=display_name,
        profile_summary=profile_summary,
        text_samples=posts[:3],
        collector=f"{platform.value}-fake",
        backend=f"{platform.value}-fake",
        auth_mode="test",
    )
    return CollectedAccount(account=account, corpus=corpus)


@pytest.mark.live
def test_opencode_cli_and_skill_layout_smoke(tmp_path: Path) -> None:
    opencode = shutil.which("opencode")
    if not opencode:
        pytest.skip("install OpenCode before running this smoke test")

    x_url = "https://x.com/karpathy"
    dataset = {
        x_url: _collection(
            platform=Platform.X,
            url=x_url,
            profile_id="karpathy",
            display_name="Andrej Karpathy",
            profile_summary="Neural nets and LLMs.",
            posts=["I like to build fast GPT systems."],
        ),
    }
    registry = {Platform.X: FakeBackend(Platform.X, dataset)}
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry=registry,
    )
    created, _ = workflow.create_persona([x_url])
    built = workflow.build_skill(
        created.person.person_id,
        hosts=["opencode"],
        install_roots={"opencode": tmp_path / ".opencode"},
    )

    skill_dir = Path(built.installed_skill_dir)
    assert skill_dir.name == built.installs[0].entry_name
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "references" / "persona.md").exists()

    isolated_home = tmp_path / "home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(isolated_home)
    env["XDG_CONFIG_HOME"] = str(isolated_home / ".config")
    env["XDG_CACHE_HOME"] = str(isolated_home / ".cache")

    version = subprocess.run(
        [opencode, "--version"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert version.stdout.strip() or version.stderr.strip()

    agent_list = subprocess.run(
        [opencode, "agent", "list"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert agent_list.stdout.strip() or agent_list.stderr.strip()
