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


class PersonaStorage:
    def __init__(self, base_dir: str | Path = "personas") -> None:
        self.base_dir = Path(base_dir)

    def person_dir(self, person_id: str) -> Path:
        return self.base_dir / person_id

    def existing_person_dirs(self) -> list[Path]:
        if not self.base_dir.exists():
            return []
        return sorted(path for path in self.base_dir.iterdir() if path.is_dir())

    def save_result(self, result: OperationResult) -> Path:
        person_dir = self.person_dir(result.person.person_id)
        person_dir.mkdir(parents=True, exist_ok=True)

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
        return PersonRecord(
            person_id=payload["person_id"],
            canonical_name=payload["canonical_name"],
            accounts=accounts,
            identity_resolution=dict(payload.get("identity_resolution", {})),
            background_summary=payload.get("background_summary", ""),
            talking_style_summary=payload.get("talking_style_summary", ""),
            platform_observations=dict(payload.get("platform_observations", {})),
            evidence=evidence,
            history=history,
            uncertainties=list(payload.get("uncertainties", [])),
        )
