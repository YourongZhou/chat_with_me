# Collector Backends

The current implementation binds two concrete backends:

- `X / Twitter` -> `Scweet`
- `小红书 / Xiaohongshu` -> `MediaCrawler`

Both are managed under `.runtime/`.

## Runtime layout

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

## X backend

Bootstrap:

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend bootstrap x
```

Login check:

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend login x
```

Token source:

- `.runtime/auth_tokens`

Expected format:

```text
# X (twitter):
your_auth_token_here # token
```

The X backend uses:

- `Scweet.get_user_info(...)` for profile metadata when available
- `Scweet.get_profile_tweets(...)` for the actual post corpus

## Xiaohongshu backend

Bootstrap:

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend bootstrap xiaohongshu
```

Login:

```bash
conda run -n chat python -m social_persona_skill.cli --runtime-root .runtime backend login xiaohongshu
```

The login flow launches MediaCrawler in QR-code mode and stores browser state under:

- `.runtime/state/xiaohongshu/browser_state`

Collection uses MediaCrawler creator mode and normalizes the resulting note text into persona corpus rows.
