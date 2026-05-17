from __future__ import annotations

from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Any
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .models import Platform, StoredPersona
from .workbench import PersonaWorkbench


class PersonaPatchRequest(BaseModel):
    persona_name: str | None = None
    primary_account_url: str | None = None


class BackendJobRequest(BaseModel):
    platform: Platform


class PersonaCreateRequest(BaseModel):
    urls: list[str] = Field(default_factory=list)


class PersonaAttachRequest(BaseModel):
    urls: list[str] = Field(default_factory=list)


class SkillBuildRequest(BaseModel):
    person_id: str
    slug: str | None = None


def create_app(
    *,
    storage_dir: str | Path = "personas",
    runtime_root: str | Path = ".runtime",
    workbench: PersonaWorkbench | None = None,
) -> FastAPI:
    bench = workbench or PersonaWorkbench(storage_dir=storage_dir, runtime_root=runtime_root)
    app = FastAPI(title="Persona Workbench")
    app.state.workbench = bench

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        needs_migration = bench.needs_migration()
        personas = bench.list_personas()
        jobs = bench.list_jobs()[:12]
        return _layout(
            "Persona Workbench",
            _index_body(personas, jobs, needs_migration),
        )

    @app.get("/personas/{person_id}", response_class=HTMLResponse)
    def persona_detail_page(person_id: str) -> str:
        stored = bench.get_persona(person_id)
        jobs = bench.list_jobs()[:12]
        return _layout(
            f"Persona {stored.person.persona_name}",
            _persona_body(stored, jobs, bench.workflow.storage.person_needs_migration(person_id)),
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(job_id: str) -> str:
        job = bench.get_job(job_id)
        log = bench.get_job_log(job_id)
        return _layout(
            f"Job {job_id}",
            _job_body(job, log),
        )

    @app.get("/api/personas")
    def list_personas() -> list[dict[str, Any]]:
        return bench.list_personas()

    @app.get("/api/personas/{person_id}")
    def get_persona(person_id: str) -> dict[str, Any]:
        stored = bench.get_persona(person_id)
        return _stored_persona_payload(stored)

    @app.patch("/api/personas/{person_id}")
    def patch_persona(person_id: str, payload: PersonaPatchRequest) -> dict[str, Any]:
        stored = bench.update_persona(
            person_id,
            persona_name=payload.persona_name,
            primary_account_url=payload.primary_account_url,
        )
        return _stored_persona_payload(stored)

    @app.post("/api/jobs/backend/bootstrap")
    def submit_backend_bootstrap(payload: BackendJobRequest) -> dict[str, Any]:
        _ensure_not_blocked_by_migration(bench, "backend.bootstrap")
        return asdict(bench.submit_backend_bootstrap(payload.platform))

    @app.post("/api/jobs/backend/login")
    def submit_backend_login(payload: BackendJobRequest) -> dict[str, Any]:
        _ensure_not_blocked_by_migration(bench, "backend.login")
        return asdict(bench.submit_backend_login(payload.platform))

    @app.post("/api/jobs/personas/create")
    def submit_persona_create(payload: PersonaCreateRequest) -> dict[str, Any]:
        _ensure_not_blocked_by_migration(bench, "persona.create")
        return asdict(bench.submit_persona_create(_normalize_urls(payload.urls)))

    @app.post("/api/jobs/personas/{person_id}/attach")
    def submit_persona_attach(person_id: str, payload: PersonaAttachRequest) -> dict[str, Any]:
        _ensure_not_blocked_by_migration(bench, "persona.attach")
        return asdict(bench.submit_persona_attach(person_id, _normalize_urls(payload.urls)))

    @app.post("/api/jobs/personas/migrate")
    def submit_persona_migrate() -> dict[str, Any]:
        return asdict(bench.submit_persona_migrate())

    @app.post("/api/jobs/skills/build")
    def submit_skill_build(payload: SkillBuildRequest) -> dict[str, Any]:
        _ensure_not_blocked_by_migration(bench, "skill.build")
        return asdict(bench.submit_skill_build(payload.person_id, slug=payload.slug))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return bench.get_job(job_id)

    @app.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse)
    def get_job_log(job_id: str) -> str:
        return bench.get_job_log(job_id)

    return app


def _ensure_not_blocked_by_migration(bench: PersonaWorkbench, requested_job_type: str) -> None:
    if bench.needs_migration() and requested_job_type != "persona.migrate":
        raise HTTPException(status_code=409, detail="Stored personas require migration before other actions can run.")


def _normalize_urls(urls: list[str]) -> list[str]:
    normalized = [item.strip() for item in urls if item.strip()]
    if not normalized:
        raise HTTPException(status_code=422, detail="At least one URL is required.")
    return normalized


def _stored_persona_payload(stored: StoredPersona) -> dict[str, Any]:
    return {
        "person": asdict(stored.person),
        "sources": [asdict(item) for item in stored.sources],
        "corpora": {
            key: [asdict(row) for row in rows]
            for key, rows in stored.corpora.items()
        },
        "markdown": stored.markdown,
    }


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #f4efe7;
      --ink: #182027;
      --muted: #5f6a72;
      --card: #fffaf2;
      --line: #d9cebb;
      --accent: #17594a;
      --accent-soft: #e2f0eb;
      --warn: #9f4a19;
      --shadow: 0 18px 50px rgba(54, 42, 22, 0.08);
      --radius: 18px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.7), transparent 28%),
        linear-gradient(135deg, #efe7da 0%, #f7f3ec 35%, #e7efe8 100%);
      min-height: 100vh;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .page {{ max-width: 1220px; margin: 0 auto; padding: 32px 20px 60px; }}
    .hero {{
      background: linear-gradient(135deg, rgba(23,89,74,0.96), rgba(39,71,86,0.92));
      color: #f7fbf9;
      border-radius: 28px;
      padding: 28px;
      box-shadow: var(--shadow);
      margin-bottom: 24px;
    }}
    .hero h1 {{ margin: 0 0 10px; font-size: 32px; }}
    .hero p {{ margin: 0; color: rgba(247,251,249,0.84); max-width: 760px; line-height: 1.6; }}
    .grid {{ display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 20px; }}
    .stack {{ display: grid; gap: 20px; }}
    .card {{
      background: rgba(255, 250, 242, 0.95);
      border: 1px solid rgba(217, 206, 187, 0.9);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 20px;
    }}
    .card h2, .card h3 {{ margin-top: 0; }}
    .toolbar {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0 0; }}
    button, input, select, textarea {{
      font: inherit;
      border-radius: 12px;
      border: 1px solid var(--line);
      padding: 10px 12px;
      background: white;
    }}
    button {{
      background: var(--accent);
      color: white;
      border: none;
      cursor: pointer;
    }}
    button.secondary {{ background: #dae5df; color: var(--ink); }}
    button:disabled {{ opacity: 0.6; cursor: not-allowed; }}
    textarea {{ width: 100%; min-height: 108px; resize: vertical; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 10px 8px; border-bottom: 1px solid rgba(217, 206, 187, 0.75); vertical-align: top; }}
    .muted {{ color: var(--muted); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 13px;
      margin-right: 6px;
      margin-bottom: 6px;
    }}
    .warn {{
      background: #fff2e8;
      border: 1px solid #efc7a8;
      color: var(--warn);
      border-radius: 16px;
      padding: 14px 16px;
      margin-bottom: 20px;
    }}
    .jobs pre {{
      background: #1e252b;
      color: #edf3ee;
      border-radius: 14px;
      padding: 16px;
      overflow-x: auto;
      max-height: 520px;
    }}
    .stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px; }}
    .stat {{
      min-width: 120px;
      background: rgba(255,255,255,0.12);
      border: 1px solid rgba(255,255,255,0.14);
      border-radius: 16px;
      padding: 12px 14px;
    }}
    .stat b {{ display: block; font-size: 22px; }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .form-row {{ display: grid; gap: 8px; margin-bottom: 12px; }}
    .inline-form {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    code {{ background: rgba(23, 89, 74, 0.08); padding: 2px 6px; border-radius: 8px; }}
    @media (max-width: 900px) {{
      .grid, .two-col {{ grid-template-columns: 1fr; }}
      .page {{ padding: 18px 14px 40px; }}
      .hero {{ padding: 22px; }}
      .hero h1 {{ font-size: 28px; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Persona Workbench</h1>
      <p>把 persona 主名、平台账号 ID、展示名和任务状态拆开看清楚。这个工作台优先解决可见、可操作、可追踪，而不是继续堆 CLI 心智负担。</p>
      <div class="toolbar">
        <a href="/">首页</a>
      </div>
    </section>
    {body}
  </div>
</body>
</html>"""


def _index_body(personas: list[dict[str, Any]], jobs: list[dict[str, Any]], needs_migration: bool) -> str:
    warning = ""
    if needs_migration:
        warning = """
        <div class="warn">
          检测到旧版 persona 数据。迁移完成前，首页只开放迁移任务，其他动作会被阻塞。
          <div class="toolbar" style="margin-top:10px;">
            <button onclick="submitJob('/api/jobs/personas/migrate', {}, null)">执行迁移</button>
          </div>
        </div>
        """

    rows = "".join(
        f"""
        <tr>
          <td><a href="/personas/{escape(item['person_id'])}">{escape(item['persona_name'])}</a></td>
          <td><code>{escape(item['person_id'])}</code></td>
          <td>{" ".join(f'<span class="pill">{escape(platform)}</span>' for platform in item["platforms"])}</td>
          <td>{item['account_count']}</td>
          <td>{item['item_count']}</td>
        </tr>
        """
        for item in personas
    ) or '<tr><td colspan="5" class="muted">还没有 persona。</td></tr>'

    job_rows = "".join(
        f"""
        <tr>
          <td><a href="/jobs/{escape(item['job_id'])}"><code>{escape(item['job_id'])}</code></a></td>
          <td>{escape(item['job_type'])}</td>
          <td>{escape(item['status'])}</td>
        </tr>
        """
        for item in jobs
    ) or '<tr><td colspan="3" class="muted">还没有任务。</td></tr>'

    disabled = "disabled" if needs_migration else ""
    return f"""
    {warning}
    <div class="grid">
      <div class="stack">
        <section class="card">
          <h2>Persona 列表</h2>
          <table>
            <thead>
              <tr>
                <th>persona_name</th>
                <th>person_id</th>
                <th>平台</th>
                <th>账号数</th>
                <th>语料数</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        <section class="card">
          <h2>新建 Persona</h2>
          <p class="muted">每行一个账号 URL。支持一次从多个平台生成一个 persona。</p>
          <div class="form-row">
            <textarea id="create-urls" placeholder="https://github.com/your_name&#10;https://www.zhihu.com/people/your-id"></textarea>
          </div>
          <div class="toolbar">
            <button {disabled} onclick="submitUrlListJob('/api/jobs/personas/create', 'create-urls')">创建 Persona</button>
          </div>
        </section>
      </div>
      <div class="stack">
        <section class="card">
          <h2>Backend 操作</h2>
          <div class="two-col">
            <div class="form-row">
              <label for="platform-bootstrap">bootstrap 平台</label>
              <select id="platform-bootstrap">{_platform_options()}</select>
              <button {disabled} onclick="submitJob('/api/jobs/backend/bootstrap', {{platform: valueOf('platform-bootstrap')}}, null)">运行 bootstrap</button>
            </div>
            <div class="form-row">
              <label for="platform-login">login 平台</label>
              <select id="platform-login">{_platform_options()}</select>
              <button {disabled} onclick="submitJob('/api/jobs/backend/login', {{platform: valueOf('platform-login')}}, null)">运行 login</button>
            </div>
          </div>
        </section>
        <section class="card">
          <h2>任务中心</h2>
          <table>
            <thead>
              <tr><th>job_id</th><th>类型</th><th>状态</th></tr>
            </thead>
            <tbody>{job_rows}</tbody>
          </table>
        </section>
      </div>
    </div>
    {_shared_script()}
    """


def _persona_body(stored: StoredPersona, jobs: list[dict[str, Any]], needs_migration: bool) -> str:
    person = stored.person
    account_rows = "".join(
        f"""
        <tr>
          <td>{escape(person.persona_name)}</td>
          <td>{escape(account.platform.value)}</td>
          <td>{escape(account.display_name or '(no display name)')}</td>
          <td><code>{escape(account.profile_id)}</code></td>
          <td><a href="{escape(account.url)}" target="_blank" rel="noreferrer">{escape(account.url)}</a></td>
        </tr>
        """
        for account in person.accounts
    )
    source_rows = "".join(
        f"""
        <tr>
          <td>{escape(source.platform.value)}</td>
          <td>{escape(source.display_name or source.profile_id)}</td>
          <td>{escape(source.backend)}</td>
          <td>{source.item_count}</td>
          <td><code>{escape(source.corpus_path)}</code></td>
        </tr>
        """
        for source in stored.sources
    )
    job_rows = "".join(
        f"""
        <tr>
          <td><a href="/jobs/{escape(item['job_id'])}"><code>{escape(item['job_id'])}</code></a></td>
          <td>{escape(item['job_type'])}</td>
          <td>{escape(item['status'])}</td>
        </tr>
        """
        for item in jobs
    ) or '<tr><td colspan="3" class="muted">还没有任务。</td></tr>'
    corpus_count = sum(len(rows) for rows in stored.corpora.values())
    disabled = "disabled" if needs_migration else ""
    options = "".join(
        f'<option value="{escape(account.url)}" {"selected" if account.url == person.primary_account_url else ""}>{escape(account.platform.value)} | {escape(account.display_name or account.profile_id)}</option>'
        for account in person.accounts
    )
    return f"""
    <div class="grid">
      <div class="stack">
        <section class="card">
          <h2>{escape(person.persona_name)}</h2>
          <div class="stats">
            <div class="stat"><span class="muted">person_id</span><b>{escape(person.person_id)}</b></div>
            <div class="stat"><span class="muted">账号数</span><b>{len(person.accounts)}</b></div>
            <div class="stat"><span class="muted">语料数</span><b>{corpus_count}</b></div>
            <div class="stat"><span class="muted">schema</span><b>{person.schema_version}</b></div>
          </div>
          <p class="muted" style="margin-top:14px;">`persona_name` 是人类主名；`profile_id` 是平台账号 ID；`display_name` 是平台展示名。它们现在会被并排展示，而不是混成一个字段。</p>
        </section>
        <section class="card">
          <h2>账号映射表</h2>
          <table>
            <thead>
              <tr>
                <th>persona_name</th>
                <th>platform</th>
                <th>display_name</th>
                <th>profile_id</th>
                <th>url</th>
              </tr>
            </thead>
            <tbody>{account_rows}</tbody>
          </table>
        </section>
        <section class="card">
          <h2>来源摘要</h2>
          <table>
            <thead>
              <tr><th>平台</th><th>来源名</th><th>backend</th><th>条目数</th><th>语料路径</th></tr>
            </thead>
            <tbody>{source_rows}</tbody>
          </table>
          <h3>概要</h3>
          <pre>{escape(stored.markdown)}</pre>
        </section>
      </div>
      <div class="stack">
        <section class="card">
          <h2>编辑 Persona</h2>
          <div class="form-row">
            <label for="persona-name">persona_name</label>
            <input id="persona-name" value="{escape(person.persona_name)}">
          </div>
          <div class="form-row">
            <label for="primary-account-url">primary_account_url</label>
            <select id="primary-account-url">{options}</select>
          </div>
          <div class="toolbar">
            <button onclick="updatePersona('{escape(person.person_id)}')">保存</button>
          </div>
        </section>
        <section class="card">
          <h2>附加账号</h2>
          <div class="form-row">
            <textarea id="attach-urls" placeholder="https://www.instagram.com/your_name/"></textarea>
          </div>
          <div class="toolbar">
            <button {disabled} onclick="submitUrlListJob('/api/jobs/personas/{escape(person.person_id)}/attach', 'attach-urls')">附加账号</button>
          </div>
        </section>
        <section class="card">
          <h2>编译 Skill</h2>
          <div class="form-row">
            <label for="skill-slug">可选 slug</label>
            <input id="skill-slug" placeholder="my-persona">
          </div>
          <div class="toolbar">
            <button {disabled} onclick="submitJob('/api/jobs/skills/build', {{person_id: '{escape(person.person_id)}', slug: valueOf('skill-slug') || null}}, null)">编译 Skill</button>
          </div>
        </section>
        <section class="card">
          <h2>最近任务</h2>
          <table>
            <thead><tr><th>job_id</th><th>类型</th><th>状态</th></tr></thead>
            <tbody>{job_rows}</tbody>
          </table>
        </section>
      </div>
    </div>
    {_shared_script()}
    <script>
      async function updatePersona(personId) {{
        const payload = {{
          persona_name: valueOf('persona-name'),
          primary_account_url: valueOf('primary-account-url')
        }};
        const response = await fetch(`/api/personas/${{personId}}`, {{
          method: 'PATCH',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(payload)
        }});
        if (!response.ok) {{
          alert(await response.text());
          return;
        }}
        window.location.reload();
      }}
    </script>
    """


def _job_body(job: dict[str, Any], log: str) -> str:
    result_json = escape(json.dumps(job.get("result") or {}, ensure_ascii=False, indent=2))
    return f"""
    <section class="card jobs">
      <h2>任务详情</h2>
      <div class="stats">
        <div class="stat"><span class="muted">job_id</span><b>{escape(job['job_id'])}</b></div>
        <div class="stat"><span class="muted">类型</span><b>{escape(job['job_type'])}</b></div>
        <div class="stat"><span class="muted">状态</span><b>{escape(job['status'])}</b></div>
      </div>
      <div class="two-col" style="margin-top:18px;">
        <div>
          <h3>元数据</h3>
          <pre>{escape(json.dumps(job, ensure_ascii=False, indent=2))}</pre>
        </div>
        <div>
          <h3>结果</h3>
          <pre>{result_json}</pre>
        </div>
      </div>
      <h3>日志</h3>
      <pre id="job-log">{escape(log)}</pre>
    </section>
    <script>
      async function refreshLog() {{
        const response = await fetch(`/api/jobs/{escape(job['job_id'])}/log`);
        if (!response.ok) {{
          return;
        }}
        const text = await response.text();
        document.getElementById('job-log').textContent = text;
      }}
      refreshLog();
      setInterval(refreshLog, 1500);
    </script>
    """


def _platform_options() -> str:
    return "".join(
        f'<option value="{platform.value}">{platform.value}</option>'
        for platform in Platform
    )


def _shared_script() -> str:
    return """
    <script>
      function valueOf(id) {
        const node = document.getElementById(id);
        return node ? node.value : "";
      }
      function urlListFrom(id) {
        return valueOf(id)
          .split(/\\n+/)
          .map(item => item.trim())
          .filter(Boolean);
      }
      async function submitJob(path, payload, onSuccess) {
        const response = await fetch(path, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        if (!response.ok) {
          const text = await response.text();
          alert(text);
          return;
        }
        const job = await response.json();
        if (onSuccess) {
          onSuccess(job);
          return;
        }
        window.location.href = `/jobs/${job.job_id}`;
      }
      async function submitUrlListJob(path, textareaId) {
        return submitJob(path, {urls: urlListFrom(textareaId)}, null);
      }
    </script>
    """


app = create_app()
