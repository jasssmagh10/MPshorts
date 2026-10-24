# MoneyPrinterTurbo → Telegram Daily Shorts

This repository runs one MoneyPrinterTurbo generation job on GitHub Actions each day, sends the resulting MP4 to Telegram, and leaves YouTube upload to you for manual review.

## What it does

1. GitHub starts a temporary Ubuntu runner on two daily schedule windows.
2. A randomized 0–45 minute delay varies the actual scheduled run time.
3. Preflight checks live provider catalogs and tests generation on candidate models.
4. Script generation, independent fact-checking, search terms, and the YouTube hashtag description use model retries/fallbacks.
5. MoneyPrinterTurbo creates one vertical Short with Edge subtitles.
6. The MP4 is sent to Telegram and the configured YouTube upload step retries resumable upload chunks.
7. The temporary runner is discarded.

## GitHub Actions secrets and variables

Add these under **Settings → Secrets and variables → Actions**. Preflight requires at least two working LLM providers; configure at least two of the three provider API keys.

### Secrets

| Secret | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key |
| `GROQ_API_KEY` | Groq API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `PEXELS_API_KEY` | Pexels stock-video API key |
| `PIXABAY_API_KEY` | Optional video-source fallback |
| `TELEGRAM_BOT_TOKEN` | Token from `@BotFather` |
| `TELEGRAM_CHAT_ID` | Telegram personal chat, group, or channel ID |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | Optional; needed for the automated YouTube upload step |

### Optional variables

| Variable | Purpose |
|---|---|
| `GEMINI_MODEL`, `GROQ_MODEL`, `OPENROUTER_MODEL` | Preferred model IDs; each is tried first, followed by fallback candidates |
| `YT_PUBLISH_OFFSET_HOURS` | YouTube scheduled-publish offset; defaults to `2` hours |
| `YT_UPLOAD_STATUS`, `YT_CATEGORY_ID` | Optional YouTube upload settings |

No Telegram API ID or API hash is needed. This workflow uses the Telegram Bot API.

## Setup

1. Create a new GitHub repository. A private repository is recommended.
2. Copy all files in this folder into the repository.
3. Add the four secrets above.
4. Edit `topics.txt` with your own topics.
5. Open **Actions → Daily Short → Run workflow** for the first test.
6. After a successful test, the workflow is scheduled twice daily. Scheduled runs use trigger anchors of 09:47 IST and 17:13 IST plus a randomized 0–45 minute delay. The YouTube scheduled-publish time is calculated as upload-completion time plus the configured offset (2 hours by default), so render/upload duration shifts publication later; these slots target Indian lunch and evening viewing windows rather than exact guaranteed publish times.

The workflow can upload to YouTube when YouTube OAuth secrets are configured. Review the output and verify channel settings before enabling unattended public publishing.

## Notes

- The workflow generates one video per run.
- `subtitle_provider = "edge"` avoids downloading the large Whisper model.
- A Telegram Bot API upload must be below Telegram's current 50 MB multipart upload limit. The workflow re-encodes oversized videos before sending.
- MoneyPrinterTurbo and its upstream APIs may change. If the upstream CLI changes, update the command in `.github/workflows/daily-short.yml`.
- API provider charges and quotas are separate from GitHub Actions.
- Only use footage, music, voices, and scripts you are licensed to use. Review content before publishing.


### Provider preflight and fallback behavior

Before script generation, `preflight.py` checks Gemini, Groq, and OpenRouter. It tries each configured model first, then known fallback models and a limited set of models discovered from that provider's live catalog. OpenRouter automatic fallbacks are restricted to `:free` models so a fallback does not silently switch to a paid model. A provider is marked healthy when at least one candidate successfully completes a tiny generation request. The working model is passed to later workflow steps.

The workflow requires at least **two healthy providers**. Temporary network errors, timeouts, rate limits, and server errors are retried; failures are classified without printing API keys or full provider response bodies. Model probes are bounded to avoid an endless run: set `PREFLIGHT_MAX_MODELS_PER_PROVIDER` (default `24`), `PREFLIGHT_RETRIES` (default `2` retries), and `PREFLIGHT_REQUEST_GAP_SECONDS` (default `0.25`) as workflow environment variables to tune this behavior.

A healthy preflight confirms only that a model worked during the check; quotas and availability can still change afterward. If a run fails, open the failed GitHub Actions run and inspect the **Pre-flight provider check** step for per-model failure categories.

## Script generation and fact-check resilience

`generate_script.py` uses the model selected by preflight first, then tries provider-specific fallback models if that model fails. Each model request gets bounded retries for temporary failures before moving to the next model. The same model fallback path is used for script generation, search-term generation, and independent fact-checking. A successful fact-check is still required; provider/model errors are not treated as approval.

Fallbacks are intentionally bounded to the predefined candidate lists in `generate_script.py`. Preflight separately probes configured models and eligible live-catalog candidates using the API keys, then passes the selected working model into the workflow.


### Current free-tier model fallbacks

- **Gemini:** `gemini-3.8-flash` first, then `gemini-3.6-flash`, free-tier Gemma 4 (`gemma-4-31b-it`, `gemma-4-26b-a4b-it`), `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3-flash-preview`, latest Flash aliases, and Gemini 2.5 Flash/Flash-Lite. Gemini 4 Argon and Pro-tier models are excluded because they are not free-tier choices. The live API catalog is also checked at runtime.
- **Groq:** `qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, then `allam-2-7b` as a last-resort general chat fallback. Retired `llama-3.3-70b-versatile` was removed.
- **OpenRouter:** only explicit `:free` variants are used. Current curated choices prioritize Nemotron 3.5 Super VL, Gemma 4 31B/26B, Nemotron 3 Ultra, Nemotron 3.5 Lightning, Dots3-Note, and Inkling, followed by the other usable free text models shown in the supplied catalog screenshot. Content-safety-only, TTS, transcription, and other non-text-generation models are excluded. OpenRouter says the free listing for Laguna S 2.1 is scheduled to end October 31, 2026, so it is kept at the very end as a temporary last-resort only. The live catalog is re-checked by preflight because free model availability changes frequently.

`generate_youtube_description.py` retries temporary request/network failures, tries fallback models inside a provider, then tries other providers with keys present. If all description models fail, the workflow step is marked `continue-on-error` and can still upload with an empty description. `youtube_upload.py` also enables up to five retries for transient resumable-upload chunk failures.

GitHub Actions cron expressions are fixed UTC times, not genuinely random schedules. This workflow uses two fixed anchors (04:17 and 11:43 UTC) and a random 0–45 minute delay on scheduled runs to vary their actual start times. The anchors avoid the top of the hour, when GitHub documents scheduled runs are more prone to delay under load. Actual YouTube publication is scheduled relative to upload completion, not workflow start. Manual runs do not sleep.
