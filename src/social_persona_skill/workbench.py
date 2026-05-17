from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .jobs import JobManager, JobRecord
from .models import Platform, StoredPersona
from .workflow import PersonaWorkflow


class PersonaWorkbench:
    def __init__(
        self,
        *,
        storage_dir: str | Path = "personas",
        runtime_root: str | Path = ".runtime",
        workflow: PersonaWorkflow | None = None,
        jobs: JobManager | None = None,
    ) -> None:
        self.workflow = workflow or PersonaWorkflow(storage_dir=storage_dir, runtime_root=runtime_root)
        self.jobs = jobs or JobManager(self.workflow.layout.root / "jobs")

    def needs_migration(self) -> bool:
        return self.workflow.storage.needs_migration()

    def list_personas(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for person_dir in self.workflow.storage.existing_person_dirs():
            stored = self.workflow.storage.load_persona(person_dir.name)
            records.append(self._persona_summary(stored, person_dir))
        return sorted(records, key=lambda item: (item["updated_at"], item["person_id"]), reverse=True)

    def get_persona(self, person_id: str) -> StoredPersona:
        return self.workflow.storage.load_persona(person_id)

    def update_persona(self, person_id: str, *, persona_name: str | None, primary_account_url: str | None) -> StoredPersona:
        return self.workflow.update_persona_metadata(
            person_id,
            persona_name=persona_name,
            primary_account_url=primary_account_url,
        )

    def submit_backend_bootstrap(self, platform: Platform) -> JobRecord:
        return self.jobs.submit(
            "backend.bootstrap",
            lambda log: self._run_backend_bootstrap(platform, log),
        )

    def submit_backend_login(self, platform: Platform) -> JobRecord:
        return self.jobs.submit(
            "backend.login",
            lambda log: self._run_backend_login(platform, log),
        )

    def submit_persona_create(self, urls: list[str]) -> JobRecord:
        return self.jobs.submit(
            "persona.create",
            lambda log: self._run_persona_create(urls, log),
        )

    def submit_persona_attach(self, person_id: str, urls: list[str]) -> JobRecord:
        return self.jobs.submit(
            "persona.attach",
            lambda log: self._run_persona_attach(person_id, urls, log),
        )

    def submit_persona_migrate(self) -> JobRecord:
        return self.jobs.submit(
            "persona.migrate",
            lambda log: self._run_persona_migrate(log),
        )

    def submit_skill_build(self, person_id: str, *, slug: str | None = None) -> JobRecord:
        return self.jobs.submit(
            "skill.build",
            lambda log: self._run_skill_build(person_id, slug, log),
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return asdict(self.jobs.get(job_id))

    def get_job_log(self, job_id: str) -> str:
        return self.jobs.read_log(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.jobs.list_jobs()]

    def _run_backend_bootstrap(self, platform: Platform, log) -> dict[str, Any]:
        log(f"Bootstrapping {platform.value} backend...")
        message = self.workflow.bootstrap_backend(platform)
        log(message)
        return {"platform": platform.value, "message": message}

    def _run_backend_login(self, platform: Platform, log) -> dict[str, Any]:
        if platform in {Platform.ZHIHU, Platform.XIAOHONGSHU}:
            log("已启动本机浏览器，请在桌面完成登录。")
        log(f"Logging into {platform.value} backend...")
        message = self.workflow.login_backend(platform)
        log(message)
        return {"platform": platform.value, "message": message}

    def _run_persona_create(self, urls: list[str], log) -> dict[str, Any]:
        log(f"Creating persona from {len(urls)} URL(s)...")
        result, saved_dir = self.workflow.create_persona(urls)
        log(f"Saved persona to {saved_dir}")
        return {
            "person_id": result.person.person_id,
            "persona_name": result.person.persona_name,
            "saved_dir": str(saved_dir),
        }

    def _run_persona_attach(self, person_id: str, urls: list[str], log) -> dict[str, Any]:
        log(f"Attaching {len(urls)} URL(s) to persona {person_id}...")
        result, saved_dir = self.workflow.attach_persona(person_id, urls)
        log(f"Saved persona to {saved_dir}")
        return {
            "person_id": result.person.person_id,
            "persona_name": result.person.persona_name,
            "saved_dir": str(saved_dir),
        }

    def _run_persona_migrate(self, log) -> dict[str, Any]:
        log("Migrating stored personas...")
        results = self.workflow.migrate_personas()
        changed = sum(1 for item in results if item.get("changed"))
        log(f"Migrated {changed} persona(s); inspected {len(results)} total.")
        return {
            "changed": changed,
            "total": len(results),
            "personas": results,
        }

    def _run_skill_build(self, person_id: str, slug: str | None, log) -> dict[str, Any]:
        log(f"Building skill for persona {person_id}...")
        result = self.workflow.build_skill(person_id, slug=slug)
        log(f"Built skill into {result.skill_source_dir}")
        return asdict(result)

    def _persona_summary(self, stored: StoredPersona, person_dir: Path) -> dict[str, Any]:
        item_count = sum(len(rows) for rows in stored.corpora.values())
        platforms = [account.platform.value for account in stored.person.accounts]
        updated_at = person_dir.joinpath("person.json").stat().st_mtime
        return {
            "person_id": stored.person.person_id,
            "persona_name": stored.person.persona_name,
            "canonical_name": stored.person.canonical_name,
            "primary_account_url": stored.person.primary_account_url,
            "platforms": platforms,
            "account_count": len(stored.person.accounts),
            "item_count": item_count,
            "updated_at": updated_at,
            "needs_migration": self.workflow.storage.person_needs_migration(stored.person.person_id),
        }
