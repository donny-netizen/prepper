# prepper

This repo is the unattended card-judgement layer. It runs once a day, after the 14:00 UTC collector, and decides three supply cards, one ammo label, and whether Slack has anything to say.

The Mac app, Chrome helper, Railway collector, live SQLite, and retailer operations stay in Dropbox. They are not in this repo.

`judgement.py` is the contract those later runs import. It does not collect stock or prices, and it has no webhook URL and no tokens. A quiet day produces no Slack text.

## Daily vote (GitHub Actions)

Workflow: `.github/workflows/daily-judge.yml`

- Cron: `30 14 * * *` (after Railway collector `0 14 * * *`)
- Script: `vote_models.py`
- Models (free):
  - Groq `openai/gpt-oss-20b` (free-tier replacement after Llama was retired)
  - Google Gemini `gemini-3.8-flash`
  - OpenRouter `meta-llama/llama-3.1-8b-instruct:free`
- Tie-break: Food→Groq, Water storage→Gemini, Power→OpenRouter

### Required GitHub secrets

| Secret | Purpose |
| --- | --- |
| `PREPPING_HUB_URL` | Railway results HTTPS origin |
| `PREPPING_HUB_TOKEN` | Bearer token that can call `/v1/judge-day` and `/v1/judge-result` |
| `GROQ_API_KEY` | Groq chat completions |
| `GEMINI_API_KEY` | Google AI Studio key for Flash |
| `OPENROUTER_API_KEY` | OpenRouter free-model slot |

### How to create the three free API keys

Paste each secret into GitHub → `donny-netizen/prepper` → Settings → Secrets and variables → Actions. Do not commit keys.

1. **GROQ_API_KEY** — [console.groq.com/keys](https://console.groq.com/keys) → sign in → Create API Key → copy once (`gsk_…`).
2. **GEMINI_API_KEY** — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → Google account → Create API key → copy (`AIza…`). Free tier; no billing required for light daily use.
3. **OPENROUTER_API_KEY** — [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys) → Create → copy (`sk-or-v1-…`). Free `:free` models allow a small daily quota without buying credits.

Slack for supply text is posted by Railway from `PREPPING_SLACK_*` when the judgement result has `notify` and `slack`. Do not put Slack tokens in this public repo.

### Local dry run

```bash
export PREPPING_HUB_URL=... PREPPING_HUB_TOKEN=... GROQ_API_KEY=... GEMINI_API_KEY=... OPENROUTER_API_KEY=...
python3 vote_models.py --dry-run
```

## AEWS

The AEWS rule lives in the same module and is not part of the daily card pass. A level of 1 or 2 is quiet. Above 2, a message is due immediately and again every five minutes until a reply acknowledges that episode. The episode ends when the level falls to 2 or below. A later rise starts a new one. This module decides that timing. It does not download the dashboard and it does not hold a Slack token.

## Cursor Automation (deferred)

Slack-triggered reruns from `#appdev` need the Cursor Agents Window Automations editor. They are not required for the daily vote once Actions secrets are set. After Actions is green, create one automation on this repo that only reacts to `#appdev` if you want manual re-judges from Slack.
