from __future__ import annotations

from pathlib import Path
import re

from .backends import Backend, BackendError, build_backend_registry
from .models import AccountInput, CollectedAccount, OperationResult, Platform, SkillBuildResult, SourceRecord
from .runtime import RuntimeLayout
from .service import PersonaDistiller
from .skills import ClaudeSkillBuilder
from .storage import PersonaStorage


class PersonaWorkflow:
    def __init__(
        self,
        *,
        storage_dir: str | Path = "personas",
        runtime_root: str | Path = ".runtime",
        registry: dict[Platform, Backend] | None = None,
    ) -> None:
        self.layout = RuntimeLayout(Path(runtime_root))
        self.storage = PersonaStorage(storage_dir)
        self.distiller = PersonaDistiller()
        self.skill_builder = ClaudeSkillBuilder(self.storage)
        self.registry = registry or build_backend_registry(self.layout)

    def bootstrap_backend(self, platform: Platform) -> str:
        backend = self._backend(platform)
        return backend.bootstrap()

    def login_backend(self, platform: Platform) -> str:
        backend = self._backend(platform)
        return backend.login()

    def create_persona(self, urls: list[str]) -> tuple[OperationResult, Path]:
        collections = [self._backend(self._platform_for_url(url)).collect(self._account(url)) for url in urls]
        sources = [self._source_for_collection(item) for item in collections]
        result = self.distiller.create_person(collections, sources=sources)
        saved_dir = self.storage.save_result(result)
        return result, saved_dir

    def attach_persona(self, person_id: str, urls: list[str]) -> tuple[OperationResult, Path]:
        stored = self.storage.load_persona(person_id)
        existing_urls = {account.url for account in stored.person.accounts}
        collections: list[CollectedAccount] = []
        for url in urls:
            if url in existing_urls:
                continue
            collections.append(self._backend(self._platform_for_url(url)).collect(self._account(url)))

        sources_by_url = {source.url: source for source in stored.sources}
        for item in collections:
            sources_by_url[item.account.url] = self._source_for_collection(item)

        result = self.distiller.attach_accounts(
            stored,
            collections,
            sources=list(sources_by_url.values()),
        )
        saved_dir = self.storage.save_result(result)
        return result, saved_dir

    def build_skill(
        self,
        person_id: str,
        *,
        slug: str | None = None,
        target_root: str | Path = ".claude",
    ) -> SkillBuildResult:
        return self.skill_builder.build(person_id=person_id, slug=slug, target_root=target_root)

    def _backend(self, platform: Platform) -> Backend:
        try:
            return self.registry[platform]
        except KeyError as exc:
            raise BackendError(f"No backend is registered for {platform.value}.") from exc

    def _account(self, url: str) -> AccountInput:
        return AccountInput(platform=self._platform_for_url(url), url=url)

    def _platform_for_url(self, url: str) -> Platform:
        lowered = url.lower()
        if "xiaohongshu.com" in lowered:
            return Platform.XIAOHONGSHU
        if "x.com" in lowered or "twitter.com" in lowered:
            return Platform.X
        if "instagram.com" in lowered:
            return Platform.INSTAGRAM
        if "zhihu.com" in lowered:
            return Platform.ZHIHU
        raise BackendError(f"Unsupported URL: {url}")

    def _source_for_collection(self, item: CollectedAccount) -> SourceRecord:
        slug = self._slug(item.account.profile_id)
        corpus_path = f"corpora/{item.account.platform.value}/{slug}.jsonl"
        collected_at = ""
        if item.corpus:
            collected_at = max((row.collected_at for row in item.corpus if row.collected_at), default="")
        return SourceRecord(
            platform=item.account.platform,
            url=item.account.url,
            profile_id=item.account.profile_id,
            backend=item.account.backend,
            collector=item.account.collector,
            corpus_path=corpus_path,
            item_count=len(item.corpus),
            last_collected_at=collected_at,
            auth_mode=item.account.auth_mode,
            fetch_status=item.account.fetch_status,
            accessible=item.account.accessible,
            display_name=item.account.display_name,
            profile_summary=item.account.profile_summary,
        )

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
        slug = slug.strip("-")
        return slug or "account"
