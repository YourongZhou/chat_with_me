import json
from pathlib import Path

import pytest

from social_persona_skill.models import (
    AccountInput,
    AccountRecord,
    CollectedAccount,
    CorpusRecord,
    Platform,
)
from social_persona_skill.skills import SkillBuildError
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


def test_skill_build_creates_prompt_pack_and_claude_artifacts(tmp_path: Path) -> None:
    x_url = "https://x.com/karpathy"
    xhs_url = "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"
    dataset = {
        x_url: _collection(
            platform=Platform.X,
            url=x_url,
            profile_id="karpathy",
            display_name="Andrej Karpathy",
            profile_summary="Neural nets and LLMs.",
            posts=[
                "I like to build fast GPT systems.",
                "LLM training in simple, raw C/CUDA is fun.",
            ],
        ),
        xhs_url: _collection(
            platform=Platform.XIAOHONGSHU,
            url=xhs_url,
            profile_id="59b62f1550c4b47fbfa368d9",
            display_name="测试用户",
            profile_summary="关注 AI 和编程。",
            posts=["第一篇笔记正文\n\n#AI[话题]#", "第二篇笔记正文！！！"],
        ),
    }
    registry = {
        Platform.X: FakeBackend(Platform.X, dataset),
        Platform.XIAOHONGSHU: FakeBackend(Platform.XIAOHONGSHU, dataset),
    }
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry=registry,
    )
    created, saved_dir = workflow.create_persona([x_url, xhs_url])

    built = workflow.build_skill(created.person.person_id, target_root=tmp_path / ".claude")

    assert built.slug == "andrej-karpathy"
    assert (saved_dir / "skill" / "manifest.json").exists()
    assert (saved_dir / "skill" / "persona.md").exists()
    assert (saved_dir / "skill" / "style.md").exists()
    assert (saved_dir / "skill" / "examples.md").exists()
    assert (saved_dir / "skill" / "commands.json").exists()

    installed_skill_dir = Path(built.installed_skill_dir)
    assert (installed_skill_dir / "SKILL.md").exists()
    assert (installed_skill_dir / "references" / "persona.md").exists()
    assert (installed_skill_dir / "references" / "style.md").exists()
    assert (installed_skill_dir / "references" / "examples.md").exists()
    assert not (tmp_path / ".claude" / "commands").exists()
    assert [item.name for item in built.commands] == ["/persona-andrej-karpathy"] * 3
    assert [item.mode for item in built.commands] == ["roleplay", "ask", "rewrite"]
    assert [item.prompt_prefix for item in built.commands] == ["roleplay:", "ask:", "rewrite:"]

    persona_md = (saved_dir / "skill" / "persona.md").read_text(encoding="utf-8")
    skill_md = (installed_skill_dir / "SKILL.md").read_text(encoding="utf-8")
    examples_md = (saved_dir / "skill" / "examples.md").read_text(encoding="utf-8")
    commands_payload = json.loads((saved_dir / "skill" / "commands.json").read_text(encoding="utf-8"))
    assert "Attached Accounts Inventory" in persona_md
    assert "xiaohongshu" in persona_md
    assert "Mode selection" in skill_md
    assert "roleplay:" in skill_md
    assert "ask:" in skill_md
    assert "rewrite:" in skill_md
    assert "example-01" in examples_md
    assert "LLM training" in examples_md or "第一篇笔记正文" in examples_md
    assert commands_payload["skill"] == "/persona-andrej-karpathy"
    assert len(commands_payload["modes"]) == 3


def test_skill_build_degrades_when_only_bio_corpus(tmp_path: Path) -> None:
    xhs_url = "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"
    dataset = {
        xhs_url: _collection(
            platform=Platform.XIAOHONGSHU,
            url=xhs_url,
            profile_id="59b62f1550c4b47fbfa368d9",
            display_name="测试用户",
            profile_summary="只有简介，没有正文。",
            posts=[],
        ),
    }
    registry = {
        Platform.XIAOHONGSHU: FakeBackend(Platform.XIAOHONGSHU, dataset),
    }
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry=registry,
    )
    created, saved_dir = workflow.create_persona([xhs_url])

    built = workflow.build_skill(created.person.person_id, target_root=tmp_path / ".claude")

    assert built.limited_evidence is True
    style_md = (saved_dir / "skill" / "style.md").read_text(encoding="utf-8")
    skill_md = Path(built.installed_skill_dir, "SKILL.md").read_text(encoding="utf-8")
    examples_md = (saved_dir / "skill" / "examples.md").read_text(encoding="utf-8")
    assert "Post corpus available: no" in style_md
    assert "Public post corpus is limited" in skill_md
    assert "ask which mode they want" in skill_md
    assert "只有简介，没有正文。" in examples_md


def test_skill_build_redacts_query_tokens_and_uses_portable_manifest_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    xhs_url = "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"
    collected = _collection(
        platform=Platform.XIAOHONGSHU,
        url=xhs_url,
        profile_id="59b62f1550c4b47fbfa368d9",
        display_name="测试用户",
        profile_summary="关注 AI 和编程。",
        posts=["第一篇笔记正文"],
    )
    collected.corpus[-1].source_url = (
        "https://www.xiaohongshu.com/explore/note-1?xsec_token=secret&xsec_source=pc_user"
    )
    registry = {
        Platform.XIAOHONGSHU: FakeBackend(Platform.XIAOHONGSHU, {xhs_url: collected}),
    }
    workflow = PersonaWorkflow(
        storage_dir="personas",
        runtime_root=".runtime",
        registry=registry,
    )

    created, saved_dir = workflow.create_persona([xhs_url])
    built = workflow.build_skill(created.person.person_id, target_root=".claude")

    manifest = json.loads((saved_dir / "skill" / "manifest.json").read_text(encoding="utf-8"))
    examples_md = (saved_dir / "skill" / "examples.md").read_text(encoding="utf-8")

    assert manifest["target_root"] == ".claude"
    assert manifest["installed_skill_dir"] == f".claude/skills/persona-{built.slug}"
    assert "/home/" not in json.dumps(manifest, ensure_ascii=False)
    assert "xsec_token" not in examples_md
    assert "https://www.xiaohongshu.com/explore/note-1" in examples_md


def test_skill_build_handles_slug_collisions_and_reuses_existing_slug(tmp_path: Path) -> None:
    first_url = "https://x.com/user-one"
    second_url = "https://x.com/user-two"
    dataset = {
        first_url: _collection(
            platform=Platform.X,
            url=first_url,
            profile_id="user-one",
            display_name="Same Persona",
            profile_summary="First summary",
            posts=["First post"],
        ),
        second_url: _collection(
            platform=Platform.X,
            url=second_url,
            profile_id="user-two",
            display_name="Same Persona",
            profile_summary="Second summary",
            posts=["Second post"],
        ),
    }
    registry = {Platform.X: FakeBackend(Platform.X, dataset)}
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry=registry,
    )
    created_one, _ = workflow.create_persona([first_url])
    built_one = workflow.build_skill(created_one.person.person_id, target_root=tmp_path / ".claude")
    created_two, _ = workflow.create_persona([second_url])
    built_two = workflow.build_skill(created_two.person.person_id, target_root=tmp_path / ".claude")
    built_two_again = workflow.build_skill(created_two.person.person_id, target_root=tmp_path / ".claude")

    assert built_one.slug == "same-persona"
    assert built_two.slug == f"same-persona-{created_two.person.person_id[:6]}"
    assert built_two_again.slug == built_two.slug


def test_skill_build_explicit_slug_conflict_raises(tmp_path: Path) -> None:
    first_url = "https://x.com/user-one"
    second_url = "https://x.com/user-two"
    dataset = {
        first_url: _collection(
            platform=Platform.X,
            url=first_url,
            profile_id="user-one",
            display_name="Alpha",
            profile_summary="First summary",
            posts=["First post"],
        ),
        second_url: _collection(
            platform=Platform.X,
            url=second_url,
            profile_id="user-two",
            display_name="Beta",
            profile_summary="Second summary",
            posts=["Second post"],
        ),
    }
    registry = {Platform.X: FakeBackend(Platform.X, dataset)}
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry=registry,
    )
    created_one, _ = workflow.create_persona([first_url])
    workflow.build_skill(created_one.person.person_id, slug="shared-style", target_root=tmp_path / ".claude")
    created_two, _ = workflow.create_persona([second_url])

    with pytest.raises(SkillBuildError):
        workflow.build_skill(created_two.person.person_id, slug="shared-style", target_root=tmp_path / ".claude")


def test_skill_build_refreshes_after_attach(tmp_path: Path) -> None:
    x_url = "https://x.com/karpathy"
    xhs_url = "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"
    dataset = {
        x_url: _collection(
            platform=Platform.X,
            url=x_url,
            profile_id="karpathy",
            display_name="Andrej Karpathy",
            profile_summary="Neural nets and LLMs.",
            posts=["LLM training in raw C."],
        ),
        xhs_url: _collection(
            platform=Platform.XIAOHONGSHU,
            url=xhs_url,
            profile_id="59b62f1550c4b47fbfa368d9",
            display_name="测试用户",
            profile_summary="关注 AI 和编程。",
            posts=["第一篇笔记正文"],
        ),
    }
    registry = {
        Platform.X: FakeBackend(Platform.X, dataset),
        Platform.XIAOHONGSHU: FakeBackend(Platform.XIAOHONGSHU, dataset),
    }
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry=registry,
    )
    created, saved_dir = workflow.create_persona([x_url])
    workflow.build_skill(created.person.person_id, target_root=tmp_path / ".claude")
    workflow.attach_persona(created.person.person_id, [xhs_url])
    workflow.build_skill(created.person.person_id, target_root=tmp_path / ".claude")

    persona_md = (saved_dir / "skill" / "persona.md").read_text(encoding="utf-8")
    examples_md = (saved_dir / "skill" / "examples.md").read_text(encoding="utf-8")
    assert xhs_url in persona_md
    assert "第一篇笔记正文" in examples_md
