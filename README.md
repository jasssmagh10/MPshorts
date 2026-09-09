# MoneyPrinterTurbo → Telegram Daily Shorts

This repository runs one MoneyPrinterTurbo generation job on GitHub Actions each day, sends the resulting MP4 to Telegram, and leaves YouTube upload to you for manual review.

## What it does

1. GitHub starts a temporary Ubuntu runner on schedule.
2. MoneyPrinterTurbo is cloned and installed.
3. A topic is selected from `topics.txt` using the day of the year.
4. Gemini generates the script and Pexels supplies stock footage.
5. MoneyPrinterTurbo creates one vertical Short with Edge subtitles.
6. The MP4 is sent to your Telegram bot.
7. The temporary runner is discarded.

## Required GitHub Secrets

Create these under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | Your Google Gemini API key |
| `GEMINI_MODEL` | Optional; defaults to `gemini-3.6-flash` in this template |
| `PEXELS_API_KEY` | Your Pexels API key |
| `TELEGRAM_BOT_TOKEN` | Token from `@BotFather` |
| `TELEGRAM_CHAT_ID` | Your Telegram personal chat, group, or channel ID |

No Telegram API ID or API hash is needed. This uses the Telegram Bot API.

## Setup

1. Create a new GitHub repository. A private repository is recommended.
2. Copy all files in this folder into the repository.
3. Add the four secrets above.
4. Edit `topics.txt` with your own topics.
5. Open **Actions → Daily Short → Run workflow** for the first test.
6. After a successful test, the workflow will run daily at 09:07 Asia/Kolkata.

The workflow deliberately does not upload to YouTube. Review the video in Telegram and upload it manually.

## Notes

- The workflow generates one video per run.
- `subtitle_provider = "edge"` avoids downloading the large Whisper model.
- A Telegram Bot API upload must be below Telegram's current 50 MB multipart upload limit. The workflow re-encodes oversized videos before sending.
- MoneyPrinterTurbo and its upstream APIs may change. If the upstream CLI changes, update the command in `.github/workflows/daily-short.yml`.
- API provider charges and quotas are separate from GitHub Actions.
- Only use footage, music, voices, and scripts you are licensed to use. Review content before publishing.
