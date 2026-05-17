from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import time

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

    persona_subparsers.add_parser("migrate", help="Upgrade persisted persona metadata to the latest schema.")

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

    web = subparsers.add_parser("web", help="Run the local Persona workbench web server.")
    web_subparsers = web.add_subparsers(dest="web_command", required=True)
    web_serve = web_subparsers.add_parser("serve", help="Serve the local Persona workbench.")
    web_serve.add_argument("--host", default="127.0.0.1")
    web_serve.add_argument("--port", type=int, default=8765)

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
        if args.persona_command == "migrate":
            results = workflow.migrate_personas()
            if args.as_json:
                print(json.dumps(results, ensure_ascii=False, indent=2, default=_json_default))
            else:
                changed = sum(1 for item in results if item.get("changed"))
                print(f"Migrated {changed} persona(s); inspected {len(results)} total.")
                for item in results:
                    state = "updated" if item.get("changed") else "unchanged"
                    print(f"- {item.get('person_id')}: {state}")
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

    if args.command == "web":
        if args.web_command == "serve":
            import uvicorn

            pid_file = _workbench_pid_file(Path(args.runtime_root), args.host, args.port)
            _stop_previous_workbench(pid_file)
            _stop_user_processes_on_port(args.host, args.port)
            _write_workbench_pid_file(pid_file, args.host, args.port)
            try:
                uvicorn.run(
                    "social_persona_skill.web:create_app",
                    factory=True,
                    host=args.host,
                    port=args.port,
                    reload=False,
                    ws="none",
                )
            finally:
                _remove_pid_file(pid_file)
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


def _workbench_pid_file(runtime_root: Path, host: str, port: int) -> Path:
    safe_host = host.replace(":", "_")
    return runtime_root.resolve() / f"workbench-{safe_host}-{port}.pid"


def _stop_previous_workbench(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    try:
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
    except Exception:
        pid_file.unlink(missing_ok=True)
        return

    pid = int(payload.get("pid", 0) or 0)
    if pid <= 0 or pid == os.getpid():
        pid_file.unlink(missing_ok=True)
        return
    if not _looks_like_workbench_process(pid):
        pid_file.unlink(missing_ok=True)
        return

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            break
        if _wait_for_exit(pid, timeout=5.0):
            break
    pid_file.unlink(missing_ok=True)


def _stop_user_processes_on_port(host: str, port: int) -> None:
    for pid in _listener_pids(host, port):
        if pid == os.getpid():
            continue
        if not _owned_by_current_user(pid):
            continue
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            except PermissionError:
                break
            if _wait_for_exit(pid, timeout=5.0):
                break


def _write_workbench_pid_file(pid_file: Path, host: str, port: int) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": host,
                "port": port,
                "argv": list(os.sys.argv),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _remove_pid_file(pid_file: Path) -> None:
    pid_file.unlink(missing_ok=True)


def _looks_like_workbench_process(pid: int) -> bool:
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.exists():
        return False
    raw = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    return "social_persona_skill.cli" in raw and "web" in raw and "serve" in raw


def _listener_pids(host: str, port: int) -> list[int]:
    completed = subprocess.run(
        ["ss", "-ltnp"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return []

    target = f"{host}:{port}"
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        if target not in line:
            continue
        marker = "pid="
        start = 0
        while True:
            idx = line.find(marker, start)
            if idx == -1:
                break
            idx += len(marker)
            digits: list[str] = []
            while idx < len(line) and line[idx].isdigit():
                digits.append(line[idx])
                idx += 1
            if digits:
                pids.append(int("".join(digits)))
            start = idx
    return sorted(set(pids))


def _owned_by_current_user(pid: int) -> bool:
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        return False
    uid_prefix = "Uid:"
    for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(uid_prefix):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1]) == os.getuid()
    return False


def _wait_for_exit(pid: int, *, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return True
        time.sleep(0.05)
    return not Path(f"/proc/{pid}").exists()


if __name__ == "__main__":
    main()
