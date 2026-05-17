from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
import json
import re
import shutil
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .models import CorpusRecord, SkillBuildResult, SkillCommandRecord, SkillInstallRecord, StoredPersona
from .storage import PersonaStorage


class SkillBuildError(Exception):
    pass


_EN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "i",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "we",
    "with",
    "you",
    "your",
}
_ZH_STOPWORDS = {
    "一个",
    "一些",
    "不是",
    "什么",
    "今天",
    "但是",
    "你们",
    "我们",
    "可以",
    "因为",
    "所以",
    "如果",
    "就是",
    "然后",
    "还是",
    "这个",
    "那个",
    "真的",
    "感觉",
    "一下",
    "已经",
    "没有",
    "自己",
}
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]"
)
_HASHTAG_RE = re.compile(r"#([^#\n]{1,40})#?")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{2,}")
_CJK_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,8}")
_HOST_CLAUDE = "claude"
_HOST_CODEX = "codex"
_HOST_OPENCODE = "opencode"
_SUPPORTED_SKILL_HOSTS = (_HOST_CLAUDE, _HOST_CODEX, _HOST_OPENCODE)
_DEFAULT_TARGET_ROOTS = {
    _HOST_CLAUDE: ".claude",
    _HOST_CODEX: ".agents",
    _HOST_OPENCODE: ".opencode",
}
_OPENCODE_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PersonaSkillBuilder:
    def __init__(self, storage: PersonaStorage) -> None:
        self.storage = storage

    def build(
        self,
        *,
        person_id: str,
        slug: str | None = None,
        target_root: str | Path | None = None,
        hosts: list[str] | None = None,
        install_roots: dict[str, str | Path] | None = None,
    ) -> SkillBuildResult:
        stored = self.storage.load_persona(person_id)
        person_dir = self.storage.person_dir(person_id)
        source_dir = person_dir / "skill"
        manifest_path = source_dir / "manifest.json"
        existing_manifest = self._load_manifest(manifest_path)
        resolved_hosts = self._resolve_hosts(hosts)
        resolved_slug = self._resolve_slug(
            stored=stored,
            person_id=person_id,
            requested_slug=slug,
            existing_manifest=existing_manifest,
        )
        resolved_agent_slug = self._resolve_agent_slug(
            stored=stored,
            person_id=person_id,
            requested_slug=slug,
            resolved_slug=resolved_slug,
            existing_manifest=existing_manifest,
        )
        target_roots = self._resolve_target_roots(
            hosts=resolved_hosts,
            target_root=target_root,
            install_roots=install_roots,
        )
        install_specs = self._build_install_specs(
            hosts=resolved_hosts,
            target_roots=target_roots,
            slug=resolved_slug,
            agent_slug=resolved_agent_slug,
        )
        compiled = self._compile(stored, resolved_slug, resolved_agent_slug, install_specs)
        self._cleanup_previous_install(existing_manifest, compiled)
        self._write_source_pack(source_dir, compiled)
        installs = self._install_artifacts(compiled, install_specs)
        primary_install = installs[0]
        primary_modes = self._mode_specs_for_host(primary_install["host"], primary_install["entry_name"])

        return SkillBuildResult(
            person_id=person_id,
            slug=resolved_slug,
            skill_source_dir=str(source_dir),
            installed_skill_dir=str(primary_install["installed_skill_dir"]),
            manifest_path=str(manifest_path),
            target_root=str(primary_install["target_root"]),
            primary_host=str(primary_install["host"]),
            limited_evidence=compiled["limited_evidence"],
            commands=[
                SkillCommandRecord(
                    name=item["name"],
                    mode=item["mode"],
                    prompt_prefix=item["prompt_prefix"],
                    usage=item["usage"],
                )
                for item in primary_modes
            ],
            installs=[
                SkillInstallRecord(
                    host=str(item["host"]),
                    target_root=str(item["target_root"]),
                    installed_skill_dir=str(item["installed_skill_dir"]),
                    entry_name=str(item["entry_name"]),
                )
                for item in installs
            ],
        )

    def _compile(
        self,
        stored: StoredPersona,
        slug: str,
        agent_slug: str,
        install_specs: list[dict[str, object]],
    ) -> dict[str, object]:
        rows = [
            row
            for corpus in stored.corpora.values()
            for row in corpus
            if row.text.strip()
        ]
        post_rows = [row for row in rows if row.item_type == "post"]
        limited_evidence = not post_rows
        examples = self._select_examples(rows)
        style_profile = self._build_style_profile(stored, rows)
        topic_clusters = self._topic_clusters(rows)
        source_hash = self._source_hash(stored)
        mode_metadata = self._mode_metadata()
        built_at = self._utc_now()

        persona_md = self._render_persona_md(stored, topic_clusters)
        style_md = self._render_style_md(style_profile, limited_evidence)
        examples_md = self._render_examples_md(examples)
        skill_md_by_host = {
            _HOST_CLAUDE: self._render_claude_skill_md(stored, slug, limited_evidence),
            _HOST_CODEX: self._render_agent_skill_md(stored, agent_slug, limited_evidence),
            _HOST_OPENCODE: self._render_agent_skill_md(stored, agent_slug, limited_evidence),
        }
        commands_payload = {
            "claude_skill": f"/persona-{slug}",
            "agent_skill": f"persona-{agent_slug}",
            "modes": mode_metadata,
        }
        primary_install = install_specs[0]
        manifest = {
            "person_id": stored.person.person_id,
            "slug": slug,
            "agent_slug": agent_slug,
            "primary_host": primary_install["host"],
            "target_host": self._target_host_label(str(primary_install["host"])),
            "target_root": self._portable_path(primary_install["target_root"]),
            "built_at": built_at,
            "source_hash": source_hash,
            "limited_evidence": limited_evidence,
            "installed_skill_dir": self._portable_path(primary_install["installed_skill_dir"]),
            "installs": [
                {
                    "host": item["host"],
                    "target_root": self._portable_path(item["target_root"]),
                    "installed_skill_dir": self._portable_path(item["installed_skill_dir"]),
                    "skill_name": item["skill_name"],
                    "entry_name": item["entry_name"],
                }
                for item in install_specs
            ],
            "modes": mode_metadata,
        }
        return {
            "slug": slug,
            "agent_slug": agent_slug,
            "manifest": manifest,
            "persona_md": persona_md,
            "style_md": style_md,
            "examples_md": examples_md,
            "skill_md_by_host": skill_md_by_host,
            "commands_json": json.dumps(commands_payload, ensure_ascii=False, indent=2) + "\n",
            "limited_evidence": limited_evidence,
        }

    def _resolve_slug(
        self,
        *,
        stored: StoredPersona,
        person_id: str,
        requested_slug: str | None,
        existing_manifest: dict[str, object] | None,
    ) -> str:
        if requested_slug:
            candidate = self._slugify(requested_slug)
            if not candidate:
                raise SkillBuildError("The requested slug could not be normalized into a valid skill slug.")
            owner = self._slug_owner(candidate)
            if owner and owner != person_id:
                raise SkillBuildError(f"Slug '{candidate}' is already used by persona {owner}.")
            return candidate

        if existing_manifest and existing_manifest.get("slug"):
            existing_slug = str(existing_manifest["slug"])
            owner = self._slug_owner(existing_slug)
            if owner in {None, person_id}:
                return existing_slug

        base_slug = self._slugify(stored.person.persona_name) or self._slugify(person_id) or f"persona-{person_id}"
        owner = self._slug_owner(base_slug)
        if owner in {None, person_id}:
            return base_slug

        fallback = f"{base_slug}-{person_id[:6]}"
        owner = self._slug_owner(fallback)
        if owner in {None, person_id}:
            return fallback

        return f"{base_slug}-{person_id}"

    def _slug_owner(self, slug: str) -> str | None:
        for person_dir in self.storage.existing_person_dirs():
            manifest_path = person_dir / "skill" / "manifest.json"
            manifest = self._load_manifest(manifest_path)
            if not manifest:
                continue
            if manifest.get("slug") == slug:
                return str(manifest.get("person_id") or person_dir.name)
        return None

    def _resolve_agent_slug(
        self,
        *,
        stored: StoredPersona,
        person_id: str,
        requested_slug: str | None,
        resolved_slug: str,
        existing_manifest: dict[str, object] | None,
    ) -> str:
        if existing_manifest and existing_manifest.get("agent_slug"):
            existing_agent_slug = self._slugify_agent(str(existing_manifest["agent_slug"]))
            owner = self._agent_slug_owner(existing_agent_slug)
            if existing_agent_slug and owner in {None, person_id}:
                return existing_agent_slug

        base_input = requested_slug or stored.person.persona_name or resolved_slug or person_id
        base_slug = self._slugify_agent(base_input)
        if not base_slug:
            base_slug = f"person-{person_id[:12]}"

        owner = self._agent_slug_owner(base_slug)
        if owner in {None, person_id}:
            return base_slug

        fallback = f"{base_slug}-{person_id[:6]}"
        owner = self._agent_slug_owner(fallback)
        if owner in {None, person_id}:
            return fallback

        return f"{base_slug}-{person_id}"

    def _agent_slug_owner(self, slug: str) -> str | None:
        for person_dir in self.storage.existing_person_dirs():
            manifest_path = person_dir / "skill" / "manifest.json"
            manifest = self._load_manifest(manifest_path)
            if not manifest:
                continue
            if manifest.get("agent_slug") == slug:
                return str(manifest.get("person_id") or person_dir.name)
        return None

    def _resolve_hosts(self, hosts: list[str] | None) -> list[str]:
        if not hosts:
            return [_HOST_CLAUDE]

        resolved: list[str] = []
        for item in hosts:
            if item not in _SUPPORTED_SKILL_HOSTS:
                raise SkillBuildError(f"Unsupported skill host '{item}'.")
            if item not in resolved:
                resolved.append(item)
        return resolved

    def _resolve_target_roots(
        self,
        *,
        hosts: list[str],
        target_root: str | Path | None,
        install_roots: dict[str, str | Path] | None,
    ) -> dict[str, Path]:
        resolved = {host: Path(_DEFAULT_TARGET_ROOTS[host]).resolve() for host in hosts}

        if target_root is not None:
            target_root_path = Path(target_root).resolve()
            if len(hosts) == 1:
                resolved[hosts[0]] = target_root_path
            elif _HOST_CLAUDE in hosts:
                resolved[_HOST_CLAUDE] = target_root_path
            else:
                raise SkillBuildError(
                    "target_root can only be used with a single host or when 'claude' is among the selected hosts."
                )

        if install_roots:
            for host, root in install_roots.items():
                if host not in hosts:
                    continue
                resolved[host] = Path(root).resolve()

        return resolved

    def _build_install_specs(
        self,
        *,
        hosts: list[str],
        target_roots: dict[str, Path],
        slug: str,
        agent_slug: str,
    ) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        for host in hosts:
            skill_slug = slug if host == _HOST_CLAUDE else agent_slug
            skill_name = f"persona-{skill_slug}"
            if host == _HOST_OPENCODE and not _OPENCODE_SKILL_NAME_RE.fullmatch(skill_name):
                raise SkillBuildError(
                    "OpenCode skill names must be lowercase ASCII kebab-case. "
                    "Use an ASCII `--slug` if you want a custom OpenCode skill name."
                )
            entry_name = f"/{skill_name}" if host == _HOST_CLAUDE else skill_name
            target_root = target_roots[host]
            specs.append(
                {
                    "host": host,
                    "target_root": target_root,
                    "skill_name": skill_name,
                    "entry_name": entry_name,
                    "installed_skill_dir": target_root / "skills" / skill_name,
                }
            )
        return specs

    def _load_manifest(self, manifest_path: Path) -> dict[str, object] | None:
        if not manifest_path.exists():
            return None
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _write_source_pack(self, source_dir: Path, compiled: dict[str, object]) -> None:
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "manifest.json").write_text(
            json.dumps(compiled["manifest"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (source_dir / "persona.md").write_text(str(compiled["persona_md"]), encoding="utf-8")
        (source_dir / "style.md").write_text(str(compiled["style_md"]), encoding="utf-8")
        (source_dir / "examples.md").write_text(str(compiled["examples_md"]), encoding="utf-8")
        (source_dir / "commands.json").write_text(str(compiled["commands_json"]), encoding="utf-8")

    def _install_artifacts(
        self,
        compiled: dict[str, object],
        install_specs: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        installed: list[dict[str, object]] = []
        for spec in install_specs:
            skill_dir = spec["installed_skill_dir"]
            references_dir = skill_dir / "references"
            references_dir.mkdir(parents=True, exist_ok=True)

            host = str(spec["host"])
            (skill_dir / "SKILL.md").write_text(str(compiled["skill_md_by_host"][host]), encoding="utf-8")
            (skill_dir / "manifest.json").write_text(
                json.dumps(self._installed_manifest(compiled["manifest"], spec), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (references_dir / "persona.md").write_text(str(compiled["persona_md"]), encoding="utf-8")
            (references_dir / "style.md").write_text(str(compiled["style_md"]), encoding="utf-8")
            (references_dir / "examples.md").write_text(str(compiled["examples_md"]), encoding="utf-8")
            installed.append(spec)

        return installed

    def _cleanup_previous_install(
        self,
        existing_manifest: dict[str, object] | None,
        compiled: dict[str, object],
    ) -> None:
        if not existing_manifest:
            return

        previous_installs = self._previous_install_map(existing_manifest)
        next_installs = {
            str(item["host"]): str(item["installed_skill_dir"])
            for item in compiled["manifest"].get("installs", [])
            if isinstance(item, dict)
        }
        for host, previous_skill_dir in previous_installs.items():
            next_skill_dir = next_installs.get(host)
            if next_skill_dir and previous_skill_dir != next_skill_dir:
                skill_dir = self._materialize_manifest_path(previous_skill_dir)
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)

        for item in existing_manifest.get("commands", []):
            if not isinstance(item, dict):
                continue
            previous_command_path = item.get("path")
            if not previous_command_path:
                continue
            command_path = Path(str(previous_command_path))
            if command_path.suffix == ".md" and command_path.exists():
                command_path.unlink()

    def _previous_install_map(self, manifest: dict[str, object]) -> dict[str, str]:
        installs: dict[str, str] = {}
        for item in manifest.get("installs", []):
            if not isinstance(item, dict):
                continue
            host = item.get("host")
            installed_skill_dir = item.get("installed_skill_dir")
            if host and installed_skill_dir:
                installs[str(host)] = str(installed_skill_dir)

        if installs:
            return installs

        installed_skill_dir = manifest.get("installed_skill_dir")
        if installed_skill_dir:
            installs[self._normalize_manifest_host(manifest.get("target_host"))] = str(installed_skill_dir)
        return installs

    def _normalize_manifest_host(self, value: object) -> str:
        if value == "claude-code":
            return _HOST_CLAUDE
        return str(value or _HOST_CLAUDE)

    def _target_host_label(self, host: str) -> str:
        if host == _HOST_CLAUDE:
            return "claude-code"
        return host

    def _installed_manifest(self, manifest: dict[str, object], spec: dict[str, object]) -> dict[str, object]:
        installed = dict(manifest)
        installed["primary_host"] = spec["host"]
        installed["target_host"] = self._target_host_label(str(spec["host"]))
        installed["target_root"] = self._portable_path(spec["target_root"])
        installed["installed_skill_dir"] = self._portable_path(spec["installed_skill_dir"])
        installed["entry_name"] = spec["entry_name"]
        return installed

    def _render_persona_md(
        self,
        stored: StoredPersona,
        topic_clusters: list[str],
    ) -> str:
        lines = [
            f"# Persona Pack: {stored.person.canonical_name}",
            "",
            "## Canonical Identity",
            f"- Canonical name: {stored.person.canonical_name}",
            f"- Person ID: {stored.person.person_id}",
            "",
            "## Attached Accounts Inventory",
        ]
        for account in stored.person.accounts:
            lines.append(f"- {account.platform.value}: {account.url} ({account.profile_id})")
        lines.extend(
            [
                "",
                "## Background Summary",
                stored.person.background_summary or "No background summary available.",
                "",
                "## Topic Clusters",
            ]
        )
        if topic_clusters:
            lines.extend(f"- {item}" for item in topic_clusters)
        else:
            lines.append("- No stable topic clusters inferred from the available corpus.")
        lines.extend(["", "## Platform-Specific Notes"])
        if stored.person.platform_observations:
            for platform, observation in sorted(stored.person.platform_observations.items()):
                lines.append(f"- {platform}: {observation}")
        else:
            lines.append("- No platform-specific notes available.")
        lines.extend(["", "## Uncertainty Markers"])
        if stored.person.uncertainties:
            lines.extend(f"- {item}" for item in stored.person.uncertainties)
        else:
            lines.append("- No explicit uncertainty markers were recorded during persona distillation.")
        return "\n".join(lines) + "\n"

    def _render_style_md(
        self,
        style_profile: dict[str, object],
        limited_evidence: bool,
    ) -> str:
        openings = style_profile["openings"] or ["No stable opening pattern extracted."]
        transitions = style_profile["transitions"] or ["No repeated transition phrases extracted."]
        emphases = style_profile["emphases"] or ["No repeated emphasis patterns extracted."]
        lines = [
            "# Style Guide",
            "",
            "## Evidence Quality",
            "- Primary evidence: public text corpus only",
            f"- Post corpus available: {'yes' if not limited_evidence else 'no'}",
            "- Constraint: mimic public wording patterns only; do not invent private facts or hidden relationships",
            "",
            "## Dominant Language And Code-Switching",
            f"- Dominant language: {style_profile['language']}",
            f"- Code-switch behavior: {style_profile['code_switch']}",
            "",
            "## Register And Delivery",
            f"- Formality: {style_profile['formality']}",
            f"- Warmth: {style_profile['warmth']}",
            f"- Intensity: {style_profile['intensity']}",
            "",
            "## Sentence And Paragraph Habits",
            f"- Sentence length: {style_profile['sentence_length']}",
            f"- Paragraph habits: {style_profile['paragraph_habits']}",
            "",
            "## Emoji, Punctuation, And Hashtags",
            f"- Emoji habit: {style_profile['emoji_habit']}",
            f"- Punctuation habit: {style_profile['punctuation_habit']}",
            f"- Hashtag habit: {style_profile['hashtag_habit']}",
            "",
            "## Preferred Openings",
        ]
        lines.extend(f"- {item}" for item in openings)
        lines.extend(["", "## Preferred Transitions"])
        lines.extend(f"- {item}" for item in transitions)
        lines.extend(["", "## Preferred Emphases"])
        lines.extend(f"- {item}" for item in emphases)
        lines.extend(
            [
                "",
                "## Rewrite Defaults",
                f"- Default output language: keep the user's input language; if unspecified, prefer {style_profile['language']}",
                "- Preserve user facts and intent unless the user explicitly asks for creative adaptation",
                "- Transfer rhythm, tone, paragraph shape, and emphasis habits before vocabulary imitation",
                "",
                "## Roleplay Red Lines",
                "- Stay in first person, but do not fabricate private events, current location, hidden preferences, or offline relationships",
                "- If the user asks for a non-public fact, answer with in-character uncertainty instead of certainty",
                "- If evidence is thin, keep the style transfer lighter and avoid overcommitting to niche habits",
            ]
        )
        return "\n".join(lines) + "\n"

    def _render_examples_md(self, examples: list[dict[str, str]]) -> str:
        lines = [
            "# Style Examples",
            "",
            "Use these examples as short evidence snippets for rhythm, vocabulary, formatting, and topic framing.",
            "",
        ]
        if not examples:
            lines.extend(
                [
                    "## No Examples",
                    "",
                    "No usable public text examples were available in the compiled corpus.",
                    "",
                ]
            )
            return "\n".join(lines)

        for item in examples:
            lines.extend(
                [
                    f"## {item['example_id']}",
                    f"- platform: {item['platform']}",
                    f"- item_type: {item['item_type']}",
                    f"- source_url: {item['source_url']}",
                    "- excerpt:",
                    self._blockquote(item["excerpt"]),
                    "",
                ]
            )
        return "\n".join(lines)

    def _render_claude_skill_md(
        self,
        stored: StoredPersona,
        slug: str,
        limited_evidence: bool,
    ) -> str:
        evidence_line = (
            "Public post corpus is limited; keep roleplay and rewrite outputs modest and avoid overfitting tiny samples."
            if limited_evidence
            else "Public post corpus is available; prioritize rhythm, vocabulary, and formatting patterns visible in the examples."
        )
        return (
            "---\n"
            f"name: persona-{slug}\n"
            f"description: Generated persona skill for {stored.person.canonical_name}. Use when asked to roleplay as this person, analyze their public persona, or rewrite text in their public style.\n"
            "---\n\n"
            f"# Persona Skill: {stored.person.canonical_name}\n\n"
            "Read the reference files before producing roleplay, analysis, or rewrite outputs:\n"
            "- `references/persona.md`\n"
            "- `references/style.md`\n"
            "- `references/examples.md`\n\n"
            "Mode selection:\n"
            "- `roleplay:` reply in first person and stay in character.\n"
            "- `ask:` answer in third person and analyze the persona from public evidence.\n"
            "- `rewrite:` preserve the user's facts and rewrite only the expression style.\n"
            "- If the user does not provide one of these prefixes, ask which mode they want instead of guessing.\n\n"
            "Behavior contract:\n"
            "- Ground all outputs in public text evidence only.\n"
            "- Never present private facts or certainty about hidden beliefs.\n"
            "- Prefer short supporting excerpts from the examples when explaining traits.\n"
            f"- {evidence_line}\n\n"
            "Examples:\n"
            f"- `/persona-{slug}` then `roleplay: ...`\n"
            f"- `/persona-{slug}` then `ask: What traits stand out?`\n"
            f"- `/persona-{slug}` then `rewrite: <text>`\n"
        )

    def _render_agent_skill_md(
        self,
        stored: StoredPersona,
        agent_slug: str,
        limited_evidence: bool,
    ) -> str:
        skill_name = f"persona-{agent_slug}"
        evidence_line = (
            "Public post corpus is limited; keep roleplay and rewrite outputs modest and avoid overfitting tiny samples."
            if limited_evidence
            else "Public post corpus is available; prioritize rhythm, vocabulary, and formatting patterns visible in the examples."
        )
        return (
            "---\n"
            f"name: {skill_name}\n"
            f"description: Generated persona skill for {stored.person.canonical_name}. Use when asked to roleplay as this person, analyze their public persona, or rewrite text in their public style.\n"
            "---\n\n"
            f"# Persona Skill: {stored.person.canonical_name}\n\n"
            "Read the reference files before producing roleplay, analysis, or rewrite outputs:\n"
            "- `references/persona.md`\n"
            "- `references/style.md`\n"
            "- `references/examples.md`\n\n"
            "Mode selection:\n"
            "- `roleplay:` reply in first person and stay in character.\n"
            "- `ask:` answer in third person and analyze the persona from public evidence.\n"
            "- `rewrite:` preserve the user's facts and rewrite only the expression style.\n"
            "- If the user does not provide one of these prefixes, ask which mode they want instead of guessing.\n\n"
            "Behavior contract:\n"
            "- Ground all outputs in public text evidence only.\n"
            "- Never present private facts or certainty about hidden beliefs.\n"
            "- Prefer short supporting excerpts from the examples when explaining traits.\n"
            f"- {evidence_line}\n\n"
            "Examples:\n"
            f"- Activate `{skill_name}` then send `roleplay: ...`\n"
            f"- Activate `{skill_name}` then send `ask: What traits stand out?`\n"
            f"- Activate `{skill_name}` then send `rewrite: <text>`\n"
        )

    def _mode_metadata(self) -> list[dict[str, str]]:
        return [
            {
                "mode": "roleplay",
                "prompt_prefix": "roleplay:",
                "description": "Reply in first person and stay within public-text evidence.",
            },
            {
                "mode": "ask",
                "prompt_prefix": "ask:",
                "description": "Analyze persona traits in third person with evidence snippets.",
            },
            {
                "mode": "rewrite",
                "prompt_prefix": "rewrite:",
                "description": "Preserve facts and rewrite expression style only.",
            },
        ]

    def _mode_specs_for_host(self, host: str, entry_name: str) -> list[dict[str, str]]:
        prefix = "then start the prompt with" if host == _HOST_CLAUDE else "then send"
        return [
            {
                "name": entry_name,
                "mode": item["mode"],
                "prompt_prefix": item["prompt_prefix"],
                "usage": f"{entry_name} {prefix} `{item['prompt_prefix']}`",
                "description": item["description"],
            }
            for item in self._mode_metadata()
        ]

    def _select_examples(self, rows: list[CorpusRecord]) -> list[dict[str, str]]:
        posts = [row for row in rows if row.item_type == "post"]
        bios = [row for row in rows if row.item_type == "bio"]
        candidates = posts or bios
        if posts and bios:
            candidates = posts + bios[:2]

        ranked = sorted(
            candidates,
            key=lambda row: (
                -self._example_score(row.text),
                row.platform.value,
                row.source_url,
                row.item_id,
            ),
        )
        selected: list[CorpusRecord] = []
        seen_text: set[str] = set()
        for row in ranked:
            normalized = row.text.strip()
            if not normalized or normalized in seen_text:
                continue
            seen_text.add(normalized)
            selected.append(row)
            if len(selected) >= 12:
                break

        selected.sort(key=lambda row: (0 if row.item_type == "post" else 1, row.platform.value, row.item_id))
        return [
            {
                "example_id": f"example-{index:02d}",
                "platform": row.platform.value,
                "item_type": row.item_type,
                "source_url": self._redact_source_url(row.source_url or row.account_url),
                "excerpt": self._trim_text(row.text, 700),
            }
            for index, row in enumerate(selected, start=1)
        ]

    def _example_score(self, text: str) -> int:
        cleaned = text.strip()
        score = min(len(cleaned), 600)
        score += cleaned.count("\n") * 24
        score += sum(cleaned.count(marker) for marker in ["!", "！", "?", "？"]) * 8
        score += len(_HASHTAG_RE.findall(cleaned)) * 14
        score += len(_EMOJI_RE.findall(cleaned)) * 16
        return score

    def _build_style_profile(
        self,
        stored: StoredPersona,
        rows: list[CorpusRecord],
    ) -> dict[str, object]:
        text = "\n".join(row.text for row in rows if row.text.strip())
        post_text = "\n".join(row.text for row in rows if row.item_type == "post" and row.text.strip())
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin_chars = len(re.findall(r"[A-Za-z]", text))
        if cjk_chars > latin_chars * 1.5:
            language = "Chinese-dominant"
        elif latin_chars > cjk_chars * 1.5:
            language = "English-dominant"
        else:
            language = "Mixed Chinese/English"

        if cjk_chars > 80 and latin_chars > 40:
            code_switch = "Frequent code-switching between Chinese and English terms."
        elif cjk_chars and latin_chars:
            code_switch = "Occasional code-switching appears in the public corpus."
        else:
            code_switch = "Little visible code-switching in the public corpus."

        post_rows = [row for row in rows if row.item_type == "post" and row.text.strip()]
        avg_paragraphs = (
            sum(max(1, len([chunk for chunk in row.text.splitlines() if chunk.strip()])) for row in post_rows)
            / max(len(post_rows), 1)
        )
        avg_sentence_chars = self._average_sentence_chars(post_text or text)
        punctuation_total = sum(text.count(marker) for marker in ["!", "！", "?", "？", "...", "——"])
        emoji_total = len(_EMOJI_RE.findall(text))
        hashtag_total = len(_HASHTAG_RE.findall(text))
        emotive_hits = sum(text.count(marker) for marker in ["哈哈", "呜呜", "开心", "喜欢", "nice", "wow", "lol"])

        formality = "casual" if (emoji_total or hashtag_total or avg_sentence_chars < 42) else "structured"
        warmth = "high" if (emoji_total + emotive_hits) >= max(2, len(post_rows)) else "medium" if emotive_hits else "low"
        intensity = "high" if punctuation_total >= max(4, len(post_rows) * 2) else "medium" if punctuation_total else "low"

        sentence_length = (
            "short-to-medium sentences"
            if avg_sentence_chars < 36
            else "medium sentences"
            if avg_sentence_chars < 72
            else "long, dense sentences"
        )
        paragraph_habits = (
            "usually multi-paragraph with visible breaks"
            if avg_paragraphs >= 2.2
            else "mostly single-paragraph or lightly segmented"
        )
        emoji_habit = (
            f"uses emoji or expressive symbols regularly ({emoji_total} observed in the sampled corpus)"
            if emoji_total
            else "little to no emoji usage in the sampled corpus"
        )
        punctuation_habit = (
            "leans on emphatic punctuation and visible emotional markers"
            if punctuation_total
            else "punctuation is comparatively restrained"
        )
        hashtag_habit = (
            f"regular hashtag framing ({hashtag_total} hashtag markers observed)"
            if hashtag_total
            else "hashtags are rare in the sampled corpus"
        )

        openings = self._top_openings(post_rows)
        transitions = self._top_transitions(text)
        emphases = self._top_emphases(text, emoji_total, hashtag_total, punctuation_total)
        if not post_rows and stored.person.talking_style_summary:
            sentence_length = f"limited direct evidence; distilled summary says: {stored.person.talking_style_summary}"

        return {
            "language": language,
            "code_switch": code_switch,
            "formality": formality,
            "warmth": warmth,
            "intensity": intensity,
            "sentence_length": sentence_length,
            "paragraph_habits": paragraph_habits,
            "emoji_habit": emoji_habit,
            "punctuation_habit": punctuation_habit,
            "hashtag_habit": hashtag_habit,
            "openings": openings,
            "transitions": transitions,
            "emphases": emphases,
        }

    def _top_openings(self, rows: list[CorpusRecord]) -> list[str]:
        seen: list[str] = []
        for row in rows:
            first_line = row.text.strip().splitlines()[0].strip()
            first_line = re.sub(r"\s+", " ", first_line)
            if not first_line:
                continue
            trimmed = self._trim_text(first_line, 48)
            if trimmed not in seen:
                seen.append(trimmed)
            if len(seen) >= 3:
                break
        return seen

    def _top_transitions(self, text: str) -> list[str]:
        candidates = [
            "因为",
            "所以",
            "然后",
            "但是",
            "此外",
            "另外",
            "其实",
            "and",
            "but",
            "so",
        ]
        ranked = sorted(
            ((token, text.lower().count(token.lower())) for token in candidates),
            key=lambda item: (-item[1], item[0]),
        )
        return [token for token, count in ranked if count > 0][:3]

    def _top_emphases(
        self,
        text: str,
        emoji_total: int,
        hashtag_total: int,
        punctuation_total: int,
    ) -> list[str]:
        emphases: list[str] = []
        if punctuation_total:
            emphases.append("Uses emphatic punctuation to push energy or reaction.")
        if emoji_total:
            emphases.append("Uses emoji or expressive symbols as visible tone markers.")
        if hashtag_total:
            emphases.append("Uses hashtags to frame topics and index themes.")
        if "\n" in text:
            emphases.append("Uses line breaks to separate beats, lists, or emotional pivots.")
        return emphases[:3]

    def _topic_clusters(self, rows: list[CorpusRecord]) -> list[str]:
        hashtag_counts = Counter()
        token_counts = Counter()
        for row in rows:
            text = row.text.strip()
            for item in _HASHTAG_RE.findall(text):
                normalized = item.strip().strip("[]")
                if normalized:
                    hashtag_counts[normalized] += 1
            for token in _LATIN_TOKEN_RE.findall(text):
                normalized = token.casefold()
                if normalized not in _EN_STOPWORDS:
                    token_counts[normalized] += 1
            for token in _CJK_TOKEN_RE.findall(text):
                if token not in _ZH_STOPWORDS:
                    token_counts[token] += 1

        topics = [item for item, _ in hashtag_counts.most_common(4)]
        for item, _ in token_counts.most_common(12):
            if item not in topics:
                topics.append(item)
            if len(topics) >= 6:
                break
        return topics

    def _source_hash(self, stored: StoredPersona) -> str:
        payload = {
            "person": asdict(stored.person),
            "sources": [asdict(source) for source in stored.sources],
            "corpora": {
                url: [asdict(row) for row in rows]
                for url, rows in sorted(stored.corpora.items())
            },
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return sha1(serialized.encode("utf-8")).hexdigest()

    def _average_sentence_chars(self, text: str) -> float:
        sentences = [item.strip() for item in re.split(r"[.!?。！？\n]+", text) if item.strip()]
        if not sentences:
            return 0.0
        return sum(len(item) for item in sentences) / len(sentences)

    def _utc_now(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()

    def _slugify(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value).casefold().strip()
        chars = []
        for char in normalized:
            if char.isalnum() or char in {"-", "_", "."}:
                chars.append(char)
            else:
                chars.append("-")
        slug = re.sub(r"-{2,}", "-", "".join(chars)).strip("-._")
        return slug

    def _slugify_agent(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold().strip()
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return re.sub(r"-{2,}", "-", slug)

    def _trim_text(self, text: str, limit: int) -> str:
        compact = text.strip()
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def _blockquote(self, text: str) -> str:
        return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())

    def _redact_source_url(self, url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def _portable_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(Path.cwd().resolve())
        except ValueError:
            return resolved.as_posix()
        return relative.as_posix() or "."

    def _materialize_manifest_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (Path.cwd() / path).resolve()


ClaudeSkillBuilder = PersonaSkillBuilder
