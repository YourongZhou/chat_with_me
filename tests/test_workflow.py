import json
from pathlib import Path

from social_persona_skill.models import (
    AccountInput,
    AccountRecord,
    CollectedAccount,
    CorpusRecord,
    Platform,
)
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


def test_create_persona_persists_person_sources_and_corpora(tmp_path: Path) -> None:
    x_url = "https://x.com/karpathy"
    xhs_url = "https://www.xiaohongshu.com/user/profile/59b62f1550c4b47fbfa368d9"
    dataset = {
        x_url: _collection(
            platform=Platform.X,
            url=x_url,
            profile_id="karpathy",
            display_name="Andrej Karpathy",
            profile_summary="Neural nets and LLMs.",
            posts=["LLM training in raw C.", "GPT repos and experiments."],
        ),
        xhs_url: _collection(
            platform=Platform.XIAOHONGSHU,
            url=xhs_url,
            profile_id="59b62f1550c4b47fbfa368d9",
            display_name="测试用户",
            profile_summary="关注 AI 和编程。",
            posts=["第一篇笔记正文", "第二篇笔记正文"],
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
    result, saved_dir = workflow.create_persona([x_url, xhs_url])

    assert result.person.person_id
    assert (saved_dir / "person.json").exists()
    assert (saved_dir / "profile.md").exists()
    assert (saved_dir / "sources.json").exists()
    assert (saved_dir / "corpora" / "x" / "karpathy.jsonl").exists()
    assert (saved_dir / "corpora" / "xiaohongshu" / "59b62f1550c4b47fbfa368d9.jsonl").exists()

    sources = json.loads((saved_dir / "sources.json").read_text(encoding="utf-8"))
    assert len(sources["accounts"]) == 2
    assert all("corpus_path" in item for item in sources["accounts"])


def test_attach_persona_adds_new_platform_without_losing_existing_corpus(tmp_path: Path) -> None:
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
    created, _ = workflow.create_persona([x_url])
    attached, saved_dir = workflow.attach_persona(created.person.person_id, [xhs_url])

    assert len(attached.person.accounts) == 2
    assert (saved_dir / "corpora" / "x" / "karpathy.jsonl").exists()
    assert (saved_dir / "corpora" / "xiaohongshu" / "59b62f1550c4b47fbfa368d9.jsonl").exists()

    loaded_person = json.loads((saved_dir / "person.json").read_text(encoding="utf-8"))
    assert len(loaded_person["accounts"]) == 2
    assert any(item["url"] == x_url for item in loaded_person["accounts"])
    assert any(item["url"] == xhs_url for item in loaded_person["accounts"])
