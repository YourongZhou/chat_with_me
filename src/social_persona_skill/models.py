from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Platform(StrEnum):
    X = "x"
    GITHUB = "github"
    XIAOHONGSHU = "xiaohongshu"
    INSTAGRAM = "instagram"
    ZHIHU = "zhihu"


@dataclass(slots=True, frozen=True)
class AccountInput:
    platform: Platform
    url: str


@dataclass(slots=True)
class AccountRecord:
    platform: Platform
    url: str
    profile_id: str
    attach_status: str = "attached"
    accessible: bool = False
    fetch_status: str = "unknown"
    display_name: str = ""
    profile_summary: str = ""
    text_samples: list[str] = field(default_factory=list)
    collector: str = "unknown"
    backend: str = "unknown"
    auth_mode: str = "none"


@dataclass(slots=True)
class CorpusRecord:
    platform: Platform
    account_url: str
    account_id: str
    item_id: str
    item_type: str
    text: str
    created_at: str = ""
    source_url: str = ""
    collector: str = "unknown"
    collected_at: str = ""


@dataclass(slots=True)
class SourceRecord:
    platform: Platform
    url: str
    profile_id: str
    backend: str
    collector: str
    corpus_path: str
    item_count: int
    last_collected_at: str
    auth_mode: str
    fetch_status: str
    accessible: bool
    display_name: str = ""
    profile_summary: str = ""


@dataclass(slots=True)
class CollectedAccount:
    account: AccountRecord
    corpus: list[CorpusRecord] = field(default_factory=list)
    source: SourceRecord | None = None


@dataclass(slots=True)
class EvidenceRecord:
    account_url: str
    platform: Platform
    summary: str
    confidence: str


@dataclass(slots=True)
class HistoryRecord:
    action: str
    details: str


@dataclass(slots=True)
class PersonRecord:
    person_id: str
    canonical_name: str
    accounts: list[AccountRecord] = field(default_factory=list)
    identity_resolution: dict[str, object] = field(default_factory=dict)
    background_summary: str = ""
    talking_style_summary: str = ""
    platform_observations: dict[str, str] = field(default_factory=dict)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    history: list[HistoryRecord] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OperationResult:
    person: PersonRecord
    markdown: str
    created: bool
    sources: list[SourceRecord] = field(default_factory=list)
    corpora: dict[str, list[CorpusRecord]] = field(default_factory=dict)


@dataclass(slots=True)
class StoredPersona:
    person: PersonRecord
    markdown: str
    sources: list[SourceRecord] = field(default_factory=list)
    corpora: dict[str, list[CorpusRecord]] = field(default_factory=dict)


@dataclass(slots=True)
class SkillCommandRecord:
    name: str
    mode: str
    prompt_prefix: str
    usage: str


@dataclass(slots=True)
class SkillBuildResult:
    person_id: str
    slug: str
    skill_source_dir: str
    installed_skill_dir: str
    manifest_path: str
    target_root: str
    limited_evidence: bool = False
    commands: list[SkillCommandRecord] = field(default_factory=list)
