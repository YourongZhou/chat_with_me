from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

from .models import (
    AccountRecord,
    CorpusRecord,
    EvidenceRecord,
    HistoryRecord,
    OperationResult,
    PersonRecord,
    Platform,
    SourceRecord,
    StoredPersona,
)

CURRENT_PERSONA_SCHEMA_VERSION = 2


class PersonaStorage:
    def __init__(self, base_dir: str | Path = "personas") -> None:
        self.base_dir = Path(base_dir)

    def person_dir(self, person_id: str) -> Path:
        return self.base_dir / person_id

    def existing_person_dirs(self) -> list[Path]:
        if not self.base_dir.exists():
            return []
        return sorted(path for path in self.base_dir.iterdir() if path.is_dir())

    def needs_migration(self) -> bool:
        return any(self.person_needs_migration(path.name) for path in self.existing_person_dirs())

    def person_needs_migration(self, person_id: str) -> bool:
        person_path = self.person_dir(person_id) / "person.json"
        if not person_path.exists():
            return False
        payload = json.loads(person_path.read_text(encoding="utf-8"))
        return (
            int(payload.get("schema_version", 1)) < CURRENT_PERSONA_SCHEMA_VERSION
            or "persona_name" not in payload
            or "primary_account_url" not in payload
            or "aliases" not in payload
        )

    def save_result(self, result: OperationResult) -> Path:
        person_dir = self.person_dir(result.person.person_id)
        person_dir.mkdir(parents=True, exist_ok=True)
        self._normalize_person_record(result.person)

        (person_dir / "person.json").write_text(
            json.dumps(asdict(result.person), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (person_dir / "profile.md").write_text(result.markdown, encoding="utf-8")
        (person_dir / "sources.json").write_text(
            json.dumps(self._sources_payload(result.sources), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._write_corpora(person_dir, result.corpora, result.sources)
        return person_dir

    def load_persona(self, person_id: str) -> StoredPersona:
        person_dir = self.person_dir(person_id)
        person_payload = json.loads((person_dir / "person.json").read_text(encoding="utf-8"))
        sources_payload = json.loads((person_dir / "sources.json").read_text(encoding="utf-8"))
        markdown = (person_dir / "profile.md").read_text(encoding="utf-8")

        person = self._person_from_payload(person_payload)
        sources = self._sources_from_payload(sources_payload)
        corpora = self._load_corpora(person_dir, sources)
        return StoredPersona(
            person=person,
            markdown=markdown,
            sources=sources,
            corpora=corpora,
        )

    def migrate_all(self) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for person_dir in self.existing_person_dirs():
            results.append(self.migrate_persona(person_dir.name))
        return results

    def migrate_persona(self, person_id: str) -> dict[str, object]:
        person_dir = self.person_dir(person_id)
        person_path = person_dir / "person.json"
        sources_path = person_dir / "sources.json"
        if not person_path.exists():
            raise FileNotFoundError(f"Missing person.json for persona {person_id}.")
        if not sources_path.exists():
            raise FileNotFoundError(f"Missing sources.json for persona {person_id}.")

        person_payload = json.loads(person_path.read_text(encoding="utf-8"))
        sources_payload = json.loads(sources_path.read_text(encoding="utf-8"))
        changed = False

        if int(person_payload.get("schema_version", 1)) < CURRENT_PERSONA_SCHEMA_VERSION:
            changed = True
        if "persona_name" not in person_payload:
            changed = True
        if "primary_account_url" not in person_payload:
            changed = True
        if "aliases" not in person_payload:
            changed = True

        if not changed:
            return {
                "person_id": person_id,
                "changed": False,
                "schema_version": int(person_payload.get("schema_version", CURRENT_PERSONA_SCHEMA_VERSION)),
            }

        self._backup_file(person_path)
        self._backup_file(sources_path)

        migrated_person = self._migrate_person_payload(person_payload)
        migrated_sources = self._migrate_sources_payload(sources_payload)

        person_path.write_text(
            json.dumps(migrated_person, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        sources_path.write_text(
            json.dumps(migrated_sources, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return {
            "person_id": person_id,
            "changed": True,
            "schema_version": CURRENT_PERSONA_SCHEMA_VERSION,
        }

    def update_persona_metadata(
        self,
        person_id: str,
        *,
        persona_name: str | None = None,
        primary_account_url: str | None = None,
    ) -> StoredPersona:
        stored = self.load_persona(person_id)
        if persona_name is not None:
            stored.person.persona_name = persona_name.strip() or stored.person.persona_name
        if primary_account_url is not None:
            stored.person.primary_account_url = primary_account_url.strip()
        stored.person.canonical_name = stored.person.persona_name
        self._normalize_person_record(stored.person)
        result = OperationResult(
            person=stored.person,
            markdown=stored.markdown,
            created=False,
            sources=stored.sources,
            corpora=stored.corpora,
        )
        self.save_result(result)
        return self.load_persona(person_id)

    def _write_corpora(
        self,
        person_dir: Path,
        corpora: dict[str, list[CorpusRecord]],
        sources: list[SourceRecord],
    ) -> None:
        corpora_dir = person_dir / "corpora"
        corpora_dir.mkdir(parents=True, exist_ok=True)

        sources_by_url = {source.url: source for source in sources}
        for account_url, rows in corpora.items():
            source = sources_by_url.get(account_url)
            if source is None:
                continue
            corpus_path = person_dir / source.corpus_path
            corpus_path.parent.mkdir(parents=True, exist_ok=True)
            with corpus_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(self._corpus_payload(row), ensure_ascii=False) + "\n")

    def _load_corpora(
        self,
        person_dir: Path,
        sources: list[SourceRecord],
    ) -> dict[str, list[CorpusRecord]]:
        corpora: dict[str, list[CorpusRecord]] = {}
        for source in sources:
            rows: list[CorpusRecord] = []
            corpus_path = person_dir / source.corpus_path
            if corpus_path.exists():
                with corpus_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        rows.append(self._corpus_from_payload(json.loads(line)))
            corpora[source.url] = rows
        return corpora

    def _sources_payload(self, sources: list[SourceRecord]) -> dict[str, object]:
        return {
            "accounts": [
                {
                    "platform": source.platform.value,
                    "url": source.url,
                    "profile_id": source.profile_id,
                    "backend": source.backend,
                    "collector": source.collector,
                    "corpus_path": source.corpus_path,
                    "item_count": source.item_count,
                    "last_collected_at": source.last_collected_at,
                    "auth_mode": source.auth_mode,
                    "fetch_status": source.fetch_status,
                    "accessible": source.accessible,
                    "display_name": source.display_name,
                    "profile_summary": source.profile_summary,
                }
                for source in sources
            ]
        }

    def _sources_from_payload(self, payload: dict[str, object]) -> list[SourceRecord]:
        return [
            SourceRecord(
                platform=Platform(item["platform"]),
                url=item["url"],
                profile_id=item["profile_id"],
                backend=item["backend"],
                collector=item["collector"],
                corpus_path=item["corpus_path"],
                item_count=item["item_count"],
                last_collected_at=item["last_collected_at"],
                auth_mode=item["auth_mode"],
                fetch_status=item["fetch_status"],
                accessible=item["accessible"],
                display_name=item.get("display_name", ""),
                profile_summary=item.get("profile_summary", ""),
            )
            for item in payload.get("accounts", [])
        ]

    def _corpus_payload(self, row: CorpusRecord) -> dict[str, object]:
        return {
            "platform": row.platform.value,
            "account_url": row.account_url,
            "account_id": row.account_id,
            "item_id": row.item_id,
            "item_type": row.item_type,
            "text": row.text,
            "created_at": row.created_at,
            "source_url": row.source_url,
            "collector": row.collector,
            "collected_at": row.collected_at,
        }

    def _corpus_from_payload(self, payload: dict[str, object]) -> CorpusRecord:
        return CorpusRecord(
            platform=Platform(payload["platform"]),
            account_url=payload["account_url"],
            account_id=payload["account_id"],
            item_id=payload["item_id"],
            item_type=payload["item_type"],
            text=payload["text"],
            created_at=payload.get("created_at", ""),
            source_url=payload.get("source_url", ""),
            collector=payload.get("collector", "unknown"),
            collected_at=payload.get("collected_at", ""),
        )

    def _person_from_payload(self, payload: dict[str, object]) -> PersonRecord:
        accounts = [
            AccountRecord(
                platform=Platform(account["platform"]),
                url=account["url"],
                profile_id=account["profile_id"],
                attach_status=account.get("attach_status", "attached"),
                accessible=account.get("accessible", False),
                fetch_status=account.get("fetch_status", "unknown"),
                display_name=account.get("display_name", ""),
                profile_summary=account.get("profile_summary", ""),
                text_samples=list(account.get("text_samples", [])),
                collector=account.get("collector", "unknown"),
                backend=account.get("backend", "unknown"),
                auth_mode=account.get("auth_mode", "none"),
            )
            for account in payload.get("accounts", [])
        ]
        evidence = [
            EvidenceRecord(
                account_url=item["account_url"],
                platform=Platform(item["platform"]),
                summary=item["summary"],
                confidence=item["confidence"],
            )
            for item in payload.get("evidence", [])
        ]
        history = [
            HistoryRecord(
                action=item["action"],
                details=item["details"],
            )
            for item in payload.get("history", [])
        ]
        person = PersonRecord(
            person_id=payload["person_id"],
            persona_name=str(payload.get("persona_name") or payload.get("canonical_name") or payload["person_id"]),
            accounts=accounts,
            schema_version=int(payload.get("schema_version", 1)),
            canonical_name=payload.get("canonical_name", payload.get("persona_name", "")),
            primary_account_url=payload.get("primary_account_url", ""),
            aliases=list(payload.get("aliases", [])),
            identity_resolution=dict(payload.get("identity_resolution", {})),
            background_summary=payload.get("background_summary", ""),
            talking_style_summary=payload.get("talking_style_summary", ""),
            platform_observations=dict(payload.get("platform_observations", {})),
            evidence=evidence,
            history=history,
            uncertainties=list(payload.get("uncertainties", [])),
        )
        self._normalize_person_record(person)
        return person

    def _backup_file(self, path: Path) -> None:
        backup_path = path.with_name(f"{path.name}.bak")
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    def _migrate_person_payload(self, payload: dict[str, object]) -> dict[str, object]:
        accounts = payload.get("accounts", [])
        primary_account_url = ""
        if isinstance(accounts, list):
            for account in accounts:
                if isinstance(account, dict):
                    primary_account_url = str(account.get("url") or "").strip()
                    if primary_account_url:
                        break

        persona_name = str(payload.get("persona_name") or payload.get("canonical_name") or payload.get("person_id") or "unknown-person").strip()
        migrated = dict(payload)
        migrated["schema_version"] = CURRENT_PERSONA_SCHEMA_VERSION
        migrated["persona_name"] = persona_name
        migrated["canonical_name"] = str(payload.get("canonical_name") or persona_name)
        migrated["primary_account_url"] = str(payload.get("primary_account_url") or primary_account_url)
        aliases = payload.get("aliases", [])
        migrated["aliases"] = list(aliases) if isinstance(aliases, list) else []
        return migrated

    def _migrate_sources_payload(self, payload: dict[str, object]) -> dict[str, object]:
        accounts = payload.get("accounts", [])
        normalized_accounts: list[dict[str, object]] = []
        if isinstance(accounts, list):
            for item in accounts:
                if not isinstance(item, dict):
                    continue
                normalized_accounts.append(
                    {
                        "platform": item["platform"],
                        "url": item["url"],
                        "profile_id": item["profile_id"],
                        "backend": item["backend"],
                        "collector": item["collector"],
                        "corpus_path": item["corpus_path"],
                        "item_count": item["item_count"],
                        "last_collected_at": item.get("last_collected_at", ""),
                        "auth_mode": item.get("auth_mode", "none"),
                        "fetch_status": item.get("fetch_status", "unknown"),
                        "accessible": item.get("accessible", False),
                        "display_name": item.get("display_name", ""),
                        "profile_summary": item.get("profile_summary", ""),
                    }
                )
        return {"accounts": normalized_accounts}

    def _normalize_person_record(self, person: PersonRecord) -> None:
        person.schema_version = CURRENT_PERSONA_SCHEMA_VERSION
        person.persona_name = person.persona_name.strip() or person.canonical_name.strip() or person.person_id
        person.canonical_name = person.persona_name
        if not person.primary_account_url:
            person.primary_account_url = person.accounts[0].url if person.accounts else ""
