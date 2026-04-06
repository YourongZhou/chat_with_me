from __future__ import annotations

from dataclasses import asdict
from hashlib import sha1
from typing import Iterable

from .models import (
    AccountRecord,
    CollectedAccount,
    CorpusRecord,
    EvidenceRecord,
    HistoryRecord,
    OperationResult,
    PersonRecord,
    StoredPersona,
)


class PersonaDistiller:
    def create_person(
        self,
        collections: Iterable[CollectedAccount],
        *,
        sources=None,
    ) -> OperationResult:
        normalized = list(collections)
        accounts = [item.account for item in normalized]
        corpora = {item.account.url: item.corpus for item in normalized}
        person = PersonRecord(
            person_id=self._make_person_id(accounts),
            canonical_name=self._canonical_name(accounts),
            accounts=accounts,
        )
        self._refresh_person(person, corpora)
        person.history.append(
            HistoryRecord(
                action="create",
                details=f"Created person from {len(accounts)} account(s).",
            )
        )
        return OperationResult(
            person=person,
            markdown=self.render_markdown(person, corpora),
            created=True,
            sources=list(sources or []),
            corpora=corpora,
        )

    def attach_accounts(
        self,
        stored: StoredPersona,
        collections: Iterable[CollectedAccount],
        *,
        sources=None,
    ) -> OperationResult:
        incoming = list(collections)
        existing_urls = {account.url for account in stored.person.accounts}
        corpora = {**stored.corpora}
        accounts = list(stored.person.accounts)
        history = list(stored.person.history)
        evidence = list(stored.person.evidence)
        added_count = 0

        for item in incoming:
            if item.account.url in existing_urls:
                history.append(
                    HistoryRecord(
                        action="update",
                        details=f"Skipped already attached account {item.account.url}.",
                    )
                )
                continue
            accounts.append(item.account)
            corpora[item.account.url] = item.corpus
            evidence.append(
                EvidenceRecord(
                    account_url=item.account.url,
                    platform=item.account.platform,
                    summary=f"Attached new {item.account.platform.value} account {item.account.profile_id}.",
                    confidence="medium",
                )
            )
            added_count += 1

        person = PersonRecord(
            person_id=stored.person.person_id,
            canonical_name=stored.person.canonical_name,
            accounts=accounts,
            identity_resolution=dict(stored.person.identity_resolution),
            evidence=evidence,
            history=history,
            uncertainties=list(stored.person.uncertainties),
        )
        self._refresh_person(person, corpora)
        person.history.append(
            HistoryRecord(
                action="update",
                details=f"Attached {added_count} new account(s).",
            )
        )
        return OperationResult(
            person=person,
            markdown=self.render_markdown(person, corpora),
            created=False,
            sources=list(sources or []),
            corpora=corpora,
        )

    def render_markdown(
        self,
        person: PersonRecord,
        corpora: dict[str, list[CorpusRecord]],
    ) -> str:
        lines = [
            f"# {person.canonical_name}",
            "",
            "## Attached Accounts",
        ]

        for account in person.accounts:
            lines.append(f"- {account.platform.value}: {account.url}")

        lines.extend(
            [
                "",
                "## Background Summary",
                person.background_summary or "No background summary available.",
                "",
                "## Talking Style Summary",
                person.talking_style_summary or "No talking-style summary available.",
                "",
                "## Platform Observations",
            ]
        )

        for platform, observation in sorted(person.platform_observations.items()):
            lines.append(f"- {platform}: {observation}")

        lines.extend(["", "## Source Text Samples"])
        for account in person.accounts:
            sample = self._sample_text(corpora.get(account.url, []))
            if sample:
                lines.append(f"- {account.platform.value}: {sample}")
            else:
                lines.append(f"- {account.platform.value}: no text samples collected")

        if person.uncertainties:
            lines.extend(["", "## Uncertainties"])
            for item in person.uncertainties:
                lines.append(f"- {item}")

        lines.extend(["", "## Evidence"])
        for evidence in person.evidence:
            lines.append(
                f"- {evidence.platform.value}: {evidence.summary} [{evidence.confidence}]"
            )

        return "\n".join(lines) + "\n"

    def serialize_person(self, person: PersonRecord) -> dict[str, object]:
        return asdict(person)

    def _refresh_person(
        self,
        person: PersonRecord,
        corpora: dict[str, list[CorpusRecord]],
    ) -> None:
        self._apply_corpus_to_accounts(person.accounts, corpora)
        person.canonical_name = self._canonical_name(person.accounts)
        person.identity_resolution = {
            "strategy": "explicit-persona-attach",
            "canonical_platform": None,
            "merged_account_count": len(person.accounts),
        }
        person.background_summary = self._build_background_summary(person.accounts, corpora)
        person.talking_style_summary = self._build_talking_style_summary(corpora)
        person.platform_observations = {
            account.platform.value: self._platform_observation(account, corpora.get(account.url, []))
            for account in person.accounts
        }
        person.evidence = self._dedupe_evidence(
            [
                item
                for item in [
                    *person.evidence,
                    *[
                        EvidenceRecord(
                            account_url=account.url,
                            platform=account.platform,
                            summary=self._evidence_summary(account, corpora.get(account.url, [])),
                            confidence="high" if corpora.get(account.url, []) else "low",
                        )
                        for account in person.accounts
                    ],
                ]
                if item.summary
            ]
        )
        person.uncertainties = [
            f"No usable text corpus collected for {account.platform.value} at {account.url}."
            for account in person.accounts
            if not corpora.get(account.url)
        ]

    def _make_person_id(self, accounts: list[AccountRecord]) -> str:
        merged = ",".join(sorted({account.profile_id for account in accounts}))
        return sha1(merged.encode("utf-8")).hexdigest()[:12]

    def _canonical_name(self, accounts: list[AccountRecord]) -> str:
        for account in accounts:
            if account.display_name.strip():
                return account.display_name.strip()
        ids = sorted({account.profile_id for account in accounts})
        return ids[0] if ids else "unknown-person"

    def _apply_corpus_to_accounts(
        self,
        accounts: list[AccountRecord],
        corpora: dict[str, list[CorpusRecord]],
    ) -> None:
        for account in accounts:
            rows = corpora.get(account.url, [])
            posts = [row.text for row in rows if row.item_type == "post" and row.text.strip()]
            bios = [row.text for row in rows if row.item_type == "bio" and row.text.strip()]
            if bios and not account.profile_summary:
                account.profile_summary = bios[0]
            samples = bios[:1] + posts[:4]
            if samples:
                account.text_samples = self._dedupe_text(samples)[:5]

    def _build_background_summary(
        self,
        accounts: list[AccountRecord],
        corpora: dict[str, list[CorpusRecord]],
    ) -> str:
        summaries = []
        for account in accounts:
            bios = [row.text for row in corpora.get(account.url, []) if row.item_type == "bio" and row.text.strip()]
            if bios:
                summaries.append(f"{account.platform.value}: {bios[0]}")
            elif account.profile_summary:
                summaries.append(f"{account.platform.value}: {account.profile_summary}")
            elif account.display_name:
                summaries.append(f"{account.platform.value}: {account.display_name}")
        if summaries:
            return " ".join(self._dedupe_text(summaries)[:3])
        platforms = ", ".join(sorted(account.platform.value for account in accounts))
        handles = ", ".join(sorted(account.profile_id for account in accounts))
        return f"Synthesized from {platforms} accounts: {handles}."

    def _build_talking_style_summary(
        self,
        corpora: dict[str, list[CorpusRecord]],
    ) -> str:
        corpus = " ".join(
            row.text
            for rows in corpora.values()
            for row in rows
            if row.item_type == "post" and row.text.strip()
        )
        if not corpus:
            return "Talking-style profile unavailable because no post corpus was collected."

        words = corpus.split()
        avg_len = sum(len(word) for word in words) / max(len(words), 1)
        sentences = [item.strip() for item in corpus.split(".") if item.strip()]
        avg_sentence_words = sum(len(item.split()) for item in sentences) / max(len(sentences), 1)
        lowered = corpus.lower()
        style_bits = []
        style_bits.append(
            "technical"
            if any(token in lowered for token in ["model", "neural", "train", "repo", "code", "llm", "agent"])
            else "general"
        )
        style_bits.append("compact" if avg_sentence_words < 12 else "expansive")
        style_bits.append("plainspoken" if avg_len < 6 else "dense")
        return f"Text appears {', '.join(style_bits)} based on collected post corpus."

    def _platform_observation(
        self,
        account: AccountRecord,
        corpus: list[CorpusRecord],
    ) -> str:
        posts = len([row for row in corpus if row.item_type == "post"])
        bios = len([row for row in corpus if row.item_type == "bio"])
        if account.accessible:
            return (
                f"Collected via {account.collector}/{account.backend} with status {account.fetch_status}; "
                f"{posts} post item(s), {bios} bio item(s)."
            )
        return (
            f"Collection degraded via {account.collector}/{account.backend} ({account.fetch_status}); "
            f"{posts} post item(s), {bios} bio item(s)."
        )

    def _evidence_summary(
        self,
        account: AccountRecord,
        corpus: list[CorpusRecord],
    ) -> str:
        for row in corpus:
            if row.text.strip():
                return row.text[:220]
        if account.profile_summary:
            return account.profile_summary[:220]
        return f"Observed account {account.profile_id} on {account.platform.value}."

    def _sample_text(self, corpus: list[CorpusRecord]) -> str:
        for row in corpus:
            if row.text.strip():
                return row.text[:220]
        return ""

    def _dedupe_evidence(self, evidence: list[EvidenceRecord]) -> list[EvidenceRecord]:
        deduped: list[EvidenceRecord] = []
        seen: set[str] = set()
        for item in evidence:
            key = "|".join(
                [
                    item.account_url,
                    item.platform.value,
                    item.summary,
                    item.confidence,
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _dedupe_text(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped
