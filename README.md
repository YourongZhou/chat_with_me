<div align="center">

# 博主.skill

> *把公开社交媒体语料整理成一个能对话、能分析、能改写风格的 Persona Skill。*

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet)](https://claude.ai/code)
[![X](https://img.shields.io/badge/Platform-X%20%2F%20Twitter-black)](https://x.com)
[![Xiaohongshu](https://img.shields.io/badge/Platform-%E5%B0%8F%E7%BA%A2%E4%B9%A6-red)](https://www.xiaohongshu.com)

<br>

你关注的博主断更了，留下万千粉丝在风中凌乱？<br>
你崇拜的大 V 转型了，曾经犀利的笔触一夜之间消失？<br>
你想和最爱的推主聊天，发现门槛太高、不回消息？<br>
你粉的明星从不下场互动，泡泡🫧也是几乎不发？<br>

**咱要的是情绪价值，不是具体的人！**


提供博主链接（网页链接）<br>
生成一个模拟他语气和风格的 skill <br>
用他的风格评价事件，用他的语气和你聊天！<br>


<br>



当前版本：
**采集文字 -> 组织语料 -> 生成个性 -> 编译 Claude Skill**

[English README](./README_EN.md)

 · [支持的平台](#支持的平台) · [安装](#安装) · [使用](#使用) · [Claude 中怎么聊](#claude-中怎么聊) · [运行时目录](#运行时目录) · [测试](#测试) · [致谢与第三方项目](#致谢与第三方项目) · [项目结构](#项目结构)

</div>

---

## 这是什么

这是一个 persona 制作仓库。

它现在做的事情很具体：

- 从公开社交账号抓取**文字内容**
- 统一归一化为 corpus JSONL
- 生成 persona 持久化文件：
  - `person.json`
  - `profile.md`
  - `sources.json`
  - `corpora/*.jsonl`
- 再编译成 Claude Code 可加载的 skill

当前版本只关注文字，后续计划处理：
- 图片
- 视频
- 评论
- 社交关系图
---

## 支持的平台

> 当前支持两个后端：twitter和小红书。

| 平台 | 后端 | 当前状态 | 采集内容 | 登录方式 |
|------|------|----------|----------|----------|
| X / Twitter | `Scweet` | ✅ 已实现 | bio、用户正文 timeline | `auth_token` |
| 小红书 / Xiaohongshu | `MediaCrawler` | ✅ 已实现 | 主页简介、笔记正文 | 扫码登录缓存 |
| Instagram | `Instaloader` | 📝 预留 | 个人简介、帖子正文 | 待定 |
| 知乎 / Zhihu | `MediaCrawler` | 📝 预留 | 个人简介、回答、文章 | 待定 |
| GitHub | `PyGithub` | 📝 预留 | profile、README、issues / PR / commit 文本 | 待定 |

目前的产品假设是：

- persona 可以先单平台创建，再逐步 attach 新账号
- Claude 侧：
  - `一个 persona = 一个 skill`
  - `三种模式 = roleplay / ask / rewrite`

---

## 功能特性

### 1. 统一 Persona 持久化

每个 persona 都被保存在：

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

### 2. 增量更新

- 可以先从一个 URL 创建 persona
- 再给已有 persona attach 新平台账号
- 旧语料不会被覆盖

### 3. Claude Skill 编译

`skill build` 会产出两层文件：

- 人可读、可追踪的源产物：`personas/<person_id>/skill/`
- Claude Code 实际加载的 skill：`.claude/skills/persona-<slug>/`

### 4. 单 Skill 三模式

当前不再依赖三个独立 slash commands。

实际使用方式是：

- `/persona-<slug>`
- 然后输入 `roleplay: ...`
- 或 `ask: ...`
- 或 `rewrite: ...`

---

## 安装

### 环境

git clone

```bash
git clone <https://github.com/YourongZhou/chat_with_me>
cd chat_with_me
```

项目本身要求：

- Python `3.11+`

如果环境还没建：

```bash
conda create -y -n chat python=3.11
```

### 安装项目依赖

```bash
conda run -n chat python -m pip install -e .[dev]
```

### X / Twitter 后端

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend bootstrap x
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend login x
```

X token 默认从下面这个文件读取：
- `.runtime/auth_tokens`

获取 token 的方式：
登录 x.com → 开发者模式 F12 → Application/应用程序 → Cookie → https://x.com → 复制 auth_token 的值。

格式是：

```text
# X (twitter):
your_auth_token_here # token
```

### 小红书后端

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend bootstrap xiaohongshu
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend login xiaohongshu
```

小红书登录：

- 需要桌面会话
- 会调用浏览器做扫码登录
- 登录缓存会写入 `.runtime/state/xiaohongshu/browser_state/`
- 后续采集直接复用缓存

---

## 使用

### 1. 创建 persona

从单个平台创建：

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  persona create https://x.com/karpathy
```

也可以一次带多个 URL：

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  persona create \
  https://x.com/karpathy \
  https://www.xiaohongshu.com/user/profile/xxx
```

### 2. 给已有 persona 追加账号

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  persona attach \
  --person-id <id> \
  https://www.xiaohongshu.com/user/profile/xxx
```

### 3. 编译 Claude Skill

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  --storage-dir personas \
  skill build \
  --person-id <id>
```

如果你要显式指定 slug：

```bash
conda run -n chat python -m social_persona_skill.cli \
  --runtime-root .runtime \
  --storage-dir personas \
  skill build \
  --person-id <id> \
  --slug my-persona
```

---

## Claude 中怎么聊


构建完成后，Claude Code 里暴露的是：

- `/persona-<slug>`


正确用法如下。

### Roleplay

```text
/persona-andrej-karpathy
roleplay: How should I learn to build LLM infra from scratch?
```

### Ask About Persona

```text
/persona-andrej-karpathy
ask: 他公开表达里最明显的风格特征是什么？
```

### Rewrite

```text
/persona-andrej-karpathy
rewrite: We should simplify the stack and reduce operational complexity.
```

---

## 运行时目录

后端依赖和登录态都隔离在 `.runtime/` 下：

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

说明：

- `Scweet` 安装在 `.runtime/backends/x/venv/`
- `MediaCrawler` repo 安装在 `.runtime/backends/xiaohongshu/repo/`
- `MediaCrawler` Python 环境在 `.runtime/backends/xiaohongshu/venv/`
- 小红书浏览器状态在 `.runtime/state/xiaohongshu/browser_state/`

---

## 测试

默认测试：

```bash
conda run -n chat pytest -q tests
```

live smoke tests 需要显式开启，并要求本地凭证和登录态已经准备好：

```bash
SOCIAL_PERSONA_RUN_LIVE=1 conda run -n chat pytest -q tests/live
```

说明：

- 默认测试不会访问真实站点
- live 测试会访问 X 和小红书真实页面
- `.runtime/`、`.claude/`、`personas/` 都是本地目录，已在 `.gitignore` 中排除

---

## 致谢与第三方项目

本项目 README 的排版和组织方式参考了 [titanwings/colleague-skill](https://github.com/titanwings/colleague-skill/)。当前采集能力也明确建立在成熟开源项目之上，感谢原作者的公开工作。

| 项目 | 用途 | 当前集成方式 | 许可与使用提示 |
|------|------|--------------|----------------|
| [colleague-skill](https://github.com/titanwings/colleague-skill/) | README 结构与表达参考 | 仅参考 README 的组织方式，不包含其代码 | 请尊重原仓库署名与许可 |
| [Scweet](https://github.com/Altimis/Scweet) | X / Twitter 文本采集 | 运行时在隔离 venv 中按固定 commit 安装 | GitHub 仓库页面标注为 MIT License；使用时仍需遵守目标平台规则与当地法律 |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler/) | 小红书登录态复用与笔记采集 | 运行时 clone 固定 commit 到 `.runtime/backends/xiaohongshu/repo/` | 上游仓库采用 `Non-Commercial Learning License 1.1`，仅建议用于学习研究，不应用于商业用途或大规模抓取 |

补充说明：

- 本仓库不直接 vendoring `Scweet` 或 `MediaCrawler` 的源码，主要做的是运行时安装、登录态衔接、采集结果归一化，以及 Persona / Skill 编译流程。
- 如果你计划二次分发、商用，或把抓取能力接入正式业务，请先自行核验上游许可证、目标平台服务条款，以及你所在地区的合规要求。
- 使用任何采集后端前，都应以最小化、克制、合法的方式访问公开信息，避免对目标平台造成干扰。

---

## 项目结构

```text
.
├── README.md
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
