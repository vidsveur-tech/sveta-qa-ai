# Jira → Telegram Notifier (GitHub Actions)

A free, serverless Python + GitHub Actions bot that checks your Jira project on a schedule (by default, hourly from 09:00 to 18:00 UTC on weekdays) and sends notifications to Telegram:
- 🆕 New issues
- 🔄 Issue updates (status, assignee, priority, summary, description, attachments, resolution, issue type)
- 💬 New comments

> **Important (VPN & Cloud note):**
> GitHub Actions runs in the GitHub cloud, so this setup works when your Jira instance is reachable over the public internet **without a VPN** — for example, **Jira Cloud** (`*.atlassian.net`) or a publicly accessible Jira Server / Data Center.
> If your Jira is behind a corporate VPN, GitHub Actions won't be able to connect to it — in that case, use a self-hosted runner inside your network or run the script locally / on an internal server.

## Quick Start (4 Steps)

1. **Create a Telegram bot:**
   - Message `@BotFather` → `/newbot` → save the token (`TG_BOT_TOKEN`).
   - Open your new bot and send `/start` (otherwise the bot won't be able to message you).
   - Get your `TG_CHAT_ID` (for example, by messaging `@userinfobot`).
2. **Create a private GitHub repository** and copy all files from this folder (`notifier.py` and `.github/workflows/jira_notify.yml`).
   - Edit `.github/workflows/jira_notify.yml` and set your `JIRA_URL` and `JIRA_PROJECT`.
3. **Add repository secrets** under `Settings → Secrets and variables → Actions → New repository secret`:
   - `TG_BOT_TOKEN` — Telegram bot token
   - `TG_CHAT_ID` — your chat ID
   - Jira authentication *(choose one)*:
     - **Jira Server / Data Center:** `JIRA_TOKEN` (Personal Access Token, sent as `Bearer`) **or** `JIRA_USER` + `JIRA_PASSWORD`
     - **Jira Cloud:** `JIRA_USER` (your Atlassian email) + `JIRA_PASSWORD` (Atlassian API Token — Jira Cloud disables regular passwords for REST API, so the API token is passed as the password in Basic Auth)
   - `JIRA_IGNORE_USER` *(optional)* — your Jira `username`, email, or `displayName` to ignore your own actions.
4. **First run:** open **Actions → Jira Telegram Notifier → Run workflow**.
   - The bot will automatically create `state.json` (recording the current baseline IDs so it doesn't spam old issues) and send a startup confirmation to Telegram. After that, it runs automatically on schedule.

## Schedule Customization (cron)

GitHub Actions cron schedules always use **UTC**.
Edit the `cron` line in `.github/workflows/jira_notify.yml`:
- `0 9-18 * * 1-5` — every hour, 09:00–18:00 UTC, weekdays only
- `0 9-18 * * *` — every day (including weekends), 09:00–18:00 UTC
- `0 * * * *` — 24/7, every hour
