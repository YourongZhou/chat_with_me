from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json

from .models import Platform
from .workflow import PersonaWorkflow


_SKILL_HOST_CHOICES = ["claude", "codex", "opencode", "all"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect social text corpora and build personas.")
    parser.add_argument(
        "--storage-dir",
        default="personas",
        help="Directory for persisted persona folders.",
    )
    parser.add_argument(
        "--runtime-root",
        default=".runtime",
        help="Directory for backend runtimes, tokens, and browser state.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print JSON output for persona commands.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    backend = subparsers.add_parser("backend", help="Manage third-party backend runtimes.")
    backend_subparsers = backend.add_subparsers(dest="backend_command", required=True)

    backend_bootstrap = backend_subparsers.add_parser("bootstrap", help="Install a backend runtime.")
    backend_bootstrap.add_argument("platform", choices=[item.value for item in _supported_backend_platforms()])

    backend_login = backend_subparsers.add_parser("login", help="Prepare credentials or login state for a backend.")
    backend_login.add_argument("platform", choices=[item.value for item in _supported_backend_platforms()])

    persona = subparsers.add_parser("persona", help="Create or update persisted personas.")
    persona_subparsers = persona.add_subparsers(dest="persona_command", required=True)

    persona_create = persona_subparsers.add_parser("create", help="Create a new persona from one or more URLs.")
    persona_create.add_argument("urls", nargs="+", help="Profile URLs to ingest.")

    persona_attach = persona_subparsers.add_parser("attach", help="Attach one or more new accounts to an existing persona.")
    persona_attach.add_argument("--person-id", required=True, help="Existing persona ID.")
    persona_attach.add_argument("urls", nargs="+", help="Profile URLs to attach.")

    skill = subparsers.add_parser("skill", help="Build persona skill artifacts from a persisted persona.")
    skill_subparsers = skill.add_subparsers(dest="skill_command", required=True)

    skill_build = skill_subparsers.add_parser("build", help="Compile and install Claude, Codex, or OpenCode skill packs.")
    skill_build.add_argument("--person-id", required=True, help="Existing persona ID.")
    skill_build.add_argument("--slug", help="Optional explicit skill slug.")
    skill_build.add_argument(
        "--host",
        action="append",
        choices=_SKILL_HOST_CHOICES,
        dest="skill_hosts",
        help="Install target host. Repeat for multiple hosts, or use 'all'. Defaults to 'claude'.",
    )
    skill_build.add_argument(
        "--target-root",
        default=None,
        help="Install root override for the selected host. When multiple hosts are selected, this applies to Claude if included.",
    )
    skill_build.add_argument("--codex-root", default=None, help="Codex project root for installed skills.")
    skill_build.add_argument("--opencode-root", default=None, help="OpenCode project root for installed skills.")

    args = parser.parse_args()
    workflow = PersonaWorkflow(storage_dir=args.storage_dir, runtime_root=args.runtime_root)

    if args.command == "backend":
        platform = Platform(args.platform)
        if args.backend_command == "bootstrap":
            print(workflow.bootstrap_backend(platform))
            return
        if args.backend_command == "login":
            print(workflow.login_backend(platform))
            return

    if args.command == "persona":
        if args.persona_command == "create":
            result, saved_dir = workflow.create_persona(args.urls)
            _print_persona_result(result, saved_dir, args.as_json)
            return
        if args.persona_command == "attach":
            result, saved_dir = workflow.attach_persona(args.person_id, args.urls)
            _print_persona_result(result, saved_dir, args.as_json)
            return

    if args.command == "skill":
        if args.skill_command == "build":
            hosts = _resolve_skill_hosts(args.skill_hosts)
            install_roots = {
                host: root
                for host, root in {
                    "codex": args.codex_root,
                    "opencode": args.opencode_root,
                }.items()
                if root
            }
            result = workflow.build_skill(
                args.person_id,
                slug=args.slug,
                target_root=args.target_root,
                hosts=hosts,
                install_roots=install_roots or None,
            )
            _print_skill_result(result, args.as_json)
            return

    raise SystemExit("Unsupported command")


def _supported_backend_platforms() -> list[Platform]:
    return [Platform.X, Platform.GITHUB, Platform.XIAOHONGSHU, Platform.INSTAGRAM, Platform.ZHIHU]


def _resolve_skill_hosts(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    if "all" in values:
        return ["claude", "codex", "opencode"]

    resolved: list[str] = []
    for item in values:
        if item not in resolved:
            resolved.append(item)
    return resolved


def _print_persona_result(result, saved_dir, as_json: bool) -> None:
    if as_json:
        payload = {
            "person": asdict(result.person),
            "sources": [asdict(source) for source in result.sources],
            "saved_dir": str(saved_dir),
            "created": result.created,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
        return

    print(result.markdown)
    print(f"Saved persona to {saved_dir}")


def _print_skill_result(result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=_json_default))
        return

    print(f"Built persona skill for persona {result.person_id} with slug '{result.slug}'.")
    print(f"Source pack: {result.skill_source_dir}")
    print("Installs:")
    for install in result.installs:
        print(
            f"- {install.host}: {install.installed_skill_dir} "
            f"(entry: {install.entry_name})"
        )
    print("Modes:")
    for item in result.commands:
        print(f"- {item.mode}: {item.usage}")


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Platform):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


if __name__ == "__main__":
    main()
