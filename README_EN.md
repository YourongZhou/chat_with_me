<div align="center">

# Influencer.skill

> *Distill public social-media text into a Persona Skill that can chat, analyze, and rewrite in someone's style.*

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet)](https://claude.ai/code)
[![X](https://img.shields.io/badge/Platform-X%20%2F%20Twitter-black)](https://x.com)
[![Xiaohongshu](https://img.shields.io/badge/Platform-%E5%B0%8F%E7%BA%A2%E4%B9%A6-red)](https://www.xiaohongshu.com)

<br>

The creator you follow stopped posting, and the vibe is gone?<br>
Your favorite opinionated account changed direction and lost the old edge?<br>
You want to talk to a beloved account, but they never reply?<br>
The celebrity you follow never really interacts with fans?<br>

**What we want is the emotional value, not the literal person.**


Give this project a creator profile link (a public web URL).<br>
It generates a skill that imitates that person's tone and style.<br>
Use it to discuss events in that style, or chat in that voice.<br>


<br>



Current version:
**Collect text -> organize corpus -> distill persona -> compile Claude Skill**

 · [Supported Platforms](#supported-platforms) · [Installation](#installation) · [Usage](#usage) · [How To Chat In Claude](#how-to-chat-in-claude) · [Runtime Layout](#runtime-layout) · [Tests](#tests) · [Credits And Third-Party Projects](#credits-and-third-party-projects) · [Project Structure](#project-structure)

</div>

---

## What This Is

This is an engineering-oriented persona distillation repository, not a collection of chat prompt templates.

What it does is fairly specific:

- Collect **text content** from public social-media accounts
- Normalize everything into corpus JSONL
- Generate persisted persona artifacts:
  - `person.json`
  - `profile.md`
  - `sources.json`
  - `corpora/*.jsonl`
- Compile the result into a Claude Code loadable skill

The current version focuses on text only. Later iterations may handle:
- Images
- Video
- Comments
- Social graph relationships

---

## Supported Platforms

> Currently supported backends: Twitter and Xiaohongshu.

| Platform | Backend | Status | Collected Content | Login Mode |
|------|------|----------|----------|----------|
| X / Twitter | `Scweet` | ✅ Implemented | bio, timeline posts | `auth_token` |
| Xiaohongshu | `MediaCrawler` | ✅ Implemented | profile bio, note text | QR-code login cache |
| Instagram | `Instaloader` | 📝 Planned | profile bio, post text | TBD |
| Zhihu | `MediaCrawler` | 📝 Planned | profile bio, answers, articles | TBD |
| GitHub | `PyGithub` | 📝 Planned | profile, README, issues / PR / commit text | TBD |

Current product assumptions:

- A persona can start from one platform, then attach more accounts over time
- On the Claude side:
  - `one persona = one skill`
  - `three modes = roleplay / ask / rewrite`

---

## Features

### 1. Unified Persona Persistence

Each persona is stored as:

```text
personas/<person_id>/
  person.json
  profile.md
  sources.json
  corpora/
    x/
      <account_slug>.jsonl
    xiaohongshu/
      <account_slug>.jsonl
  skill/
    manifest.json
    persona.md
    style.md
    examples.md
    commands.json
```

### 2. Incremental Updates

- You can create a persona from a single URL first
- Then attach additional platform accounts to an existing persona
- Existing corpora are not overwritten

### 3. Claude Skill Compilation

`skill build` produces two layers of artifacts:

- Human-readable source artifacts: `personas/<person_id>/skill/`
- Claude Code installed skill: `.claude/skills/persona-<slug>/`

### 4. One Skill, Three Modes

The current design no longer depends on three separate slash commands.

Actual usage looks like this:

- `/persona-<slug>`
- Then type `roleplay: ...`
- Or `ask: ...`
- Or `rewrite: ...`

---

## Installation

### Environment

Clone the repo:

```bash
git clone <https://github.com/YourongZhou/chat_with_me>
cd chat_with_me
```

Project requirement:

- Python `3.11+`

If you have not created the environment yet:

```bash
conda create -y -n chat python=3.11
```

### Install Project Dependencies

```bash
conda run -n chat python -m pip install -e .[dev]
```

### X / Twitter Backend

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend bootstrap x
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend login x
```

The X token is read from:
- `.runtime/auth_tokens`

How to get the token:
Log in to `x.com` -> open Developer Tools (`F12`) -> `Application` -> `Cookies` -> `https://x.com` -> copy the value of `auth_token`.

Expected format:

```text
# X (twitter):
your_auth_token_here # token
```

### Xiaohongshu Backend

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend bootstrap xiaohongshu
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend login xiaohongshu
```

Xiaohongshu login:

- Requires a desktop session
- Launches a browser for QR-code login
- Stores browser state in `.runtime/state/xiaohongshu/browser_state/`
- Reuses the cached login state for later collection

---

## Usage

### 1. Create a Persona

Create from a single platform:

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  persona create https://x.com/karpathy
```

You can also pass multiple URLs at once:

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  persona create \
  https://x.com/karpathy \
  https://www.xiaohongshu.com/user/profile/xxx
```

### 2. Attach More Accounts To An Existing Persona

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  persona attach \
  --person-id <id> \
  https://www.xiaohongshu.com/user/profile/xxx
```

### 3. Build the Claude Skill

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  --storage-dir personas \
  skill build \
  --person-id <id>
```

If you want to specify the slug explicitly:

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  --storage-dir personas \
  skill build \
  --person-id <id> \
  --slug my-persona
```

---

## How To Chat In Claude

After the build finishes, Claude Code exposes:

- `/persona-<slug>`

Use it like this.

### Roleplay

```text
/persona-andrej-karpathy
roleplay: How should I learn to build LLM infra from scratch?
```

### Ask About Persona

```text
/persona-andrej-karpathy
ask: What are the most obvious style traits in this person's public writing?
```

### Rewrite

```text
/persona-andrej-karpathy
rewrite: We should simplify the stack and reduce operational complexity.
```

---

## Runtime Layout

Backend dependencies and login state are isolated under `.runtime/`:

```text
.runtime/
  auth_tokens
  backends/
    x/
      venv/
      scweet_state.db
    xiaohongshu/
      repo/
      venv/
  state/
    xiaohongshu/
      browser_state/
```

Notes:

- `Scweet` is installed at `.runtime/backends/x/venv/`
- The `MediaCrawler` repo is cloned to `.runtime/backends/xiaohongshu/repo/`
- The `MediaCrawler` Python environment lives at `.runtime/backends/xiaohongshu/venv/`
- Xiaohongshu browser state is stored at `.runtime/state/xiaohongshu/browser_state/`

---

## Tests

Default tests:

```bash
conda run -n chat pytest -q tests
```

Live smoke tests must be enabled explicitly, and require local credentials and login state:

```bash
SOCIAL_PERSONA_RUN_LIVE=1 conda run -n chat pytest -q tests/live
```

Notes:

- Default tests do not access real sites
- Live tests access real X and Xiaohongshu pages
- `.runtime/`, `.claude/`, and `personas/` are local directories and are already ignored by `.gitignore`

---

## Credits And Third-Party Projects

The README structure and presentation were inspired by [titanwings/colleague-skill](https://github.com/titanwings/colleague-skill/). The current collection capability also builds on existing open-source projects. Credit goes to the original authors.

| Project | Purpose | Current Integration | License / Usage Notes |
|------|------|--------------|----------------|
| [colleague-skill](https://github.com/titanwings/colleague-skill/) | README structure and presentation reference | Only the README organization was referenced; no code is included | Respect the original repository's attribution and license |
| [Scweet](https://github.com/Altimis/Scweet) | X / Twitter text collection | Installed at runtime in an isolated venv at a pinned commit | The GitHub repository page labels it as MIT License; usage still needs to comply with platform rules and local law |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler/) | Xiaohongshu login-state reuse and note collection | Cloned at runtime into `.runtime/backends/xiaohongshu/repo/` at a pinned commit | The upstream project uses `Non-Commercial Learning License 1.1`; it should be treated as learning/research-only, not for commercial use or large-scale scraping |

Additional notes:

- This repository does not vendor the source code of `Scweet` or `MediaCrawler`. It mainly handles runtime installation, login-state integration, collection normalization, and Persona / Skill compilation.
- If you plan to redistribute, commercialize, or integrate the collection flow into a production product, verify the upstream licenses, target platform terms, and legal/compliance requirements in your jurisdiction first.
- Before using any collection backend, keep requests minimal, restrained, and lawful, and avoid creating unnecessary load for target platforms.

---

## Project Structure

```text
.
├── README.md
├── README_EN.md
├── docs/
│   └── collector_backends.md
├── pyproject.toml
├── src/social_persona_skill/
│   ├── adapters.py
│   ├── backends.py
│   ├── cli.py
│   ├── models.py
│   ├── runtime.py
│   ├── service.py
│   ├── skills.py
│   ├── storage.py
│   ├── workflow.py
│   └── backend_helpers/
│       ├── scweet_collect.py
│       └── xiaohongshu_collect.py
└── tests/
    ├── test_backends.py
    ├── test_skills.py
    ├── test_workflow.py
    └── live/
        ├── test_platform_backends.py
        └── test_skill_build.py
```
