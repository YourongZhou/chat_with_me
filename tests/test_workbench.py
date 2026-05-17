from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from social_persona_skill.jobs import JobManager
from social_persona_skill.models import AccountInput, AccountRecord, CollectedAccount, CorpusRecord, Platform
from social_persona_skill.web import create_app
from social_persona_skill.workbench import PersonaWorkbench
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
    corpus: list[CorpusRecord] = []
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


def _wait_for_job(client: TestClient, job_id: str, *, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in time")


def test_storage_migration_upgrades_legacy_persona(tmp_path: Path) -> None:
    storage_dir = tmp_path / "personas"
    person_dir = storage_dir / "abc123"
    person_dir.mkdir(parents=True)
    (person_dir / "profile.md").write_text("# Legacy\n", encoding="utf-8")
    (person_dir / "person.json").write_text(
        json.dumps(
            {
                "person_id": "abc123",
                "canonical_name": "Legacy Name",
                "accounts": [
                    {
                        "platform": "github",
                        "url": "https://github.com/legacy",
                        "profile_id": "legacy",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (person_dir / "sources.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "platform": "github",
                        "url": "https://github.com/legacy",
                        "profile_id": "legacy",
                        "backend": "github",
                        "collector": "github",
                        "corpus_path": "corpora/github/legacy.jsonl",
                        "item_count": 0,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    workflow = PersonaWorkflow(storage_dir=storage_dir, runtime_root=tmp_path / ".runtime", registry={})
    results = workflow.migrate_personas()

    assert results == [{"person_id": "abc123", "changed": True, "schema_version": 2}]
    assert (person_dir / "person.json.bak").exists()
    assert (person_dir / "sources.json.bak").exists()

    payload = json.loads((person_dir / "person.json").read_text(encoding="utf-8"))
    assert payload["persona_name"] == "Legacy Name"
    assert payload["canonical_name"] == "Legacy Name"
    assert payload["primary_account_url"] == "https://github.com/legacy"
    assert payload["schema_version"] == 2


def test_persona_name_falls_back_to_profile_id_when_display_name_is_missing(tmp_path: Path) -> None:
    github_url = "https://github.com/nameless"
    dataset = {
        github_url: _collection(
            platform=Platform.GITHUB,
            url=github_url,
            profile_id="nameless",
            display_name="",
            profile_summary="No display name available.",
            posts=["first post"],
        )
    }
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry={Platform.GITHUB: FakeBackend(Platform.GITHUB, dataset)},
    )

    result, _ = workflow.create_persona([github_url])

    assert result.person.persona_name == "nameless"
    assert result.person.canonical_name == "nameless"


def test_updating_persona_name_preserves_person_id_and_corpus_paths(tmp_path: Path) -> None:
    github_url = "https://github.com/karpathy"
    dataset = {
        github_url: _collection(
            platform=Platform.GITHUB,
            url=github_url,
            profile_id="karpathy",
            display_name="Andrej Karpathy",
            profile_summary="Public profile",
            posts=["ship fast"],
        )
    }
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry={Platform.GITHUB: FakeBackend(Platform.GITHUB, dataset)},
    )

    created, saved_dir = workflow.create_persona([github_url])
    updated = workflow.update_persona_metadata(created.person.person_id, persona_name="AK", primary_account_url=github_url)

    assert updated.person.person_id == created.person.person_id
    assert updated.person.persona_name == "AK"
    assert (saved_dir / "corpora" / "github" / "karpathy.jsonl").exists()


def test_job_manager_tracks_status_and_logs(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "jobs")
    job = manager.submit(
        "persona.create",
        lambda log: (log("hello from job"), {"ok": True})[1],
    )

    deadline = time.time() + 5
    while time.time() < deadline:
        state = manager.get(job.job_id)
        if state.status == "succeeded":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("job did not finish")

    assert manager.get(job.job_id).result == {"ok": True}
    assert "hello from job" in manager.read_log(job.job_id)


def test_web_api_lists_updates_and_runs_jobs(tmp_path: Path, monkeypatch) -> None:
    github_url = "https://github.com/karpathy"
    zhihu_url = "https://www.zhihu.com/people/tester"
    dataset = {
        github_url: _collection(
            platform=Platform.GITHUB,
            url=github_url,
            profile_id="karpathy",
            display_name="Andrej Karpathy",
            profile_summary="Builds neural nets in public.",
            posts=["ship code"],
        ),
        zhihu_url: _collection(
            platform=Platform.ZHIHU,
            url=zhihu_url,
            profile_id="tester",
            display_name="测试作者",
            profile_summary="写作和问答。",
            posts=["第一篇回答"],
        ),
    }
    workflow = PersonaWorkflow(
        storage_dir=tmp_path / "personas",
        runtime_root=tmp_path / ".runtime",
        registry={
            Platform.GITHUB: FakeBackend(Platform.GITHUB, dataset),
            Platform.ZHIHU: FakeBackend(Platform.ZHIHU, dataset),
        },
    )
    real_build_skill = workflow.build_skill
    monkeypatch.setattr(
        workflow,
        "build_skill",
        lambda person_id, slug=None: real_build_skill(person_id, slug=slug, target_root=tmp_path / ".claude"),
    )
    workbench = PersonaWorkbench(
        workflow=workflow,
        jobs=JobManager(tmp_path / ".runtime" / "jobs"),
    )
    client = TestClient(create_app(workbench=workbench))

    create_response = client.post("/api/jobs/personas/create", json={"urls": [github_url]})
    assert create_response.status_code == 200
    create_job = _wait_for_job(client, create_response.json()["job_id"])
    assert create_job["status"] == "succeeded"
    person_id = create_job["result"]["person_id"]

    list_payload = client.get("/api/personas").json()
    assert list_payload[0]["persona_name"] == "Andrej Karpathy"

    detail_payload = client.get(f"/api/personas/{person_id}").json()
    assert detail_payload["person"]["persona_name"] == "Andrej Karpathy"
    assert detail_payload["person"]["primary_account_url"] == github_url

    patch_response = client.patch(
        f"/api/personas/{person_id}",
        json={"persona_name": "AK", "primary_account_url": github_url},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["person"]["persona_name"] == "AK"

    attach_response = client.post(
        f"/api/jobs/personas/{person_id}/attach",
        json={"urls": [zhihu_url]},
    )
    attach_job = _wait_for_job(client, attach_response.json()["job_id"])
    assert attach_job["status"] == "succeeded"

    build_response = client.post("/api/jobs/skills/build", json={"person_id": person_id})
    build_job = _wait_for_job(client, build_response.json()["job_id"])
    assert build_job["status"] == "succeeded"
    assert Path(build_job["result"]["installed_skill_dir"]).exists()


def test_web_blocks_actions_until_migration_and_exposes_failed_login_logs(tmp_path: Path, monkeypatch) -> None:
    storage_dir = tmp_path / "personas"
    person_dir = storage_dir / "legacy"
    person_dir.mkdir(parents=True)
    (person_dir / "profile.md").write_text("# Legacy\n", encoding="utf-8")
    (person_dir / "person.json").write_text(
        json.dumps({"person_id": "legacy", "canonical_name": "Legacy", "accounts": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (person_dir / "sources.json").write_text(json.dumps({"accounts": []}, ensure_ascii=False), encoding="utf-8")

    workflow = PersonaWorkflow(storage_dir=storage_dir, runtime_root=tmp_path / ".runtime", registry={})
    workbench = PersonaWorkbench(workflow=workflow, jobs=JobManager(tmp_path / ".runtime" / "jobs"))
    client = TestClient(create_app(workbench=workbench))

    blocked = client.post("/api/jobs/personas/create", json={"urls": ["https://github.com/test"]})
    assert blocked.status_code == 409

    migrate_response = client.post("/api/jobs/personas/migrate")
    migrate_job = _wait_for_job(client, migrate_response.json()["job_id"])
    assert migrate_job["status"] == "succeeded"

    monkeypatch.setattr(workflow, "login_backend", lambda platform: (_ for _ in ()).throw(RuntimeError("login failed")))
    failed_job_response = client.post("/api/jobs/backend/login", json={"platform": "zhihu"})
    failed_job = _wait_for_job(client, failed_job_response.json()["job_id"])
    assert failed_job["status"] == "failed"
    log_text = client.get(f"/api/jobs/{failed_job['job_id']}/log").text
    assert "已启动本机浏览器，请在桌面完成登录。" in log_text
    assert "login failed" in log_text
