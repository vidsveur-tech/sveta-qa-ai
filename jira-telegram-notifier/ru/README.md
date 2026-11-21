# Jira → Telegram Notifier (GitHub Actions)

Бесплатный бот на Python + GitHub Actions, который проверяет проект в Jira по расписанию (по умолчанию — раз в час с 9:00 до 18:00 UTC в будни) и присылает в Telegram:
- 🆕 Новые задачи
- 🔄 Обновления задач (статус, исполнитель, приоритет, название, описание, вложения, резолюция, тип)
- 💬 Новые комментарии

> **Важно про доступ к Jira (VPN):**
> GitHub Actions запускается в облаке GitHub, поэтому этот способ подходит, если ваша Jira доступна из интернета **без VPN** — например, **Jira Cloud** (`*.atlassian.net`) или публичный Jira Server / Data Center.
> Если Jira закрыта корпоративным VPN, GitHub Actions не сможет к ней достучаться — в таком случае нужен self-hosted runner внутри сети или запуск скрипта локально / на сервере внутри VPN.

## Быстрый запуск (4 шага)

1. **Создай Telegram-бота:**
   - Напиши `@BotFather` → `/newbot` → сохрани токен (`TG_BOT_TOKEN`).
   - Напиши своему боту `/start` и узнай свой `TG_CHAT_ID` (например, через `@userinfobot`).
2. **Создай приватный репозиторий на GitHub** и скопируй туда всё из этой папки (`notifier.py` и `.github/workflows/jira_notify.yml`).
   - В файле `.github/workflows/jira_notify.yml` укажи свои `JIRA_URL` и `JIRA_PROJECT`.
3. **Добавь секреты** в `Settings → Secrets and variables → Actions → New repository secret`:
   - `TG_BOT_TOKEN` — токен Telegram-бота
   - `TG_CHAT_ID` — ID чата
   - Авторизация в Jira *(выбери один из вариантов)*:
     - **Jira Server / Data Center:** `JIRA_TOKEN` (Personal Access Token) **или** `JIRA_USER` + `JIRA_PASSWORD`
     - **Jira Cloud:** `JIRA_USER` (твой email в Atlassian) + `JIRA_PASSWORD` (API Token из настроек аккаунта Atlassian)
   - `JIRA_IGNORE_USER` *(опционально)* — твой `username`, email или `displayName` в Jira, чтобы не получать уведомления о собственных действиях.
4. **Первый запуск:** зайди в **Actions → Jira Telegram Notifier → Run workflow**.
   - Бот сам создаст `state.json` (зафиксирует текущие ID, чтобы не спамить старыми задачами) и пришлёт в Telegram подтверждение запуска. Дальше будет работать автоматически по расписанию.

## Настройка расписания (cron)

Время в GitHub Actions указывается в **UTC** (например, для Москвы MSK = UTC+3).
В файле `.github/workflows/jira_notify.yml` можно поменять строку `cron`:
- `0 9-18 * * 1-5` — каждый час с 9 до 18 UTC по будням
- `0 6-15 * * 1-5` — каждый час с 9 до 18 по Москве (MSK) по будням
- `0 9-18 * * *` — каждый день (включая выходные)
- `0 * * * *` — 24/7 каждый час
