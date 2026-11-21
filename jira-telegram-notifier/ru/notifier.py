"""
Jira → Telegram Notifier
------------------------
Скрипт проверяет проект в Jira и отправляет в Telegram уведомления о:
  1. Новых задачах
  2. Изменениях выбранных полей (статус, исполнитель, приоритет и т.д.)
  3. Новых комментариях

Состояние (ID последних обработанных событий) сохраняется в файле state.json.
При первом запуске скрипт не спамит старыми задачами — он просто запоминает
текущую точку отсчёта и присылает подтверждение запуска.
"""

import html
import json
import os
from pathlib import Path
from typing import Any

import requests

# --- Настройки из переменных окружения (задаются в GitHub Actions / Secrets) ---
JIRA_URL = os.environ["JIRA_URL"].rstrip("/")
JIRA_PROJECT = os.environ.get("JIRA_PROJECT", "PROJ")
JIRA_IGNORE_USER = os.environ.get("JIRA_IGNORE_USER", "").strip()
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

# Файл для хранения ID последних обработанных событий между запусками
STATE_FILE = Path("state.json")

# Поля Jira, изменения которых будут приходить в Telegram.
# Ключ — техническое имя поля в Jira API, значение — подпись в сообщении.
# Если оставить словарь пустым {}, будут приходить изменения вообще всех полей.
TRACKED_FIELDS = {
    "status": "Статус",
    "assignee": "Исполнитель",
    "priority": "Приоритет",
    "summary": "Название",
    "description": "Описание",
    "Attachment": "Вложение",
    "resolution": "Резолюция",
    "issuetype": "Тип",
}


def jira_headers_and_auth() -> tuple[dict[str, str], tuple[str, str] | None]:
    """
    Возвращает способ авторизации в Jira:
    - Bearer PAT (JIRA_TOKEN) — удобно для Jira Server / Data Center
    - Basic Auth (JIRA_USER + JIRA_PASSWORD) — для Jira Cloud (email + API Token)
      или обычного логина/пароля
    """
    token = os.environ.get("JIRA_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}, None
    return {}, (os.environ["JIRA_USER"], os.environ["JIRA_PASSWORD"])


def send_telegram(text: str) -> None:
    """Отправляет HTML-сообщение в Telegram через Bot API."""
    resp = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    resp.raise_for_status()


def truncate(text: str, limit: int = 2500) -> str:
    """Обрезает слишком длинный текст (у Telegram лимит 4096 символов на сообщение)."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "..."


def fetch_issues(init_mode: bool) -> list[dict[str, Any]]:
    """
    Загружает задачи проекта из Jira REST API вместе с историей изменений (changelog).
    - При первом запуске (init_mode=True) берём последние 50 обновлённых задач,
      чтобы зафиксировать максимальные ID.
    - В обычном режиме берём задачи, обновлённые за последние 72 часа
      (с запасом на выходные и возможные паузы).
    """
    headers, auth = jira_headers_and_auth()
    jql = (
        f"project = {JIRA_PROJECT} ORDER BY updated DESC"
        if init_mode
        else f'project = {JIRA_PROJECT} AND updated >= "-72h" ORDER BY updated DESC'
    )
    resp = requests.get(
        f"{JIRA_URL}/rest/api/2/search",
        params={
            "jql": jql,
            "fields": "summary,comment,status,assignee,creator",
            "expand": "changelog",
            "maxResults": 50,
        },
        headers=headers,
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()
    issues: list[dict[str, Any]] = resp.json().get("issues", [])

    # Если в задаче много комментариев и поиск вернул не все — дозагружаем их отдельным запросом
    for issue in issues:
        c_block = issue.get("fields", {}).get("comment", {})
        comments = c_block.get("comments", [])
        if c_block.get("total", 0) > len(comments):
            cr = requests.get(
                f"{JIRA_URL}/rest/api/2/issue/{issue['key']}/comment",
                headers=headers,
                auth=auth,
                timeout=30,
            )
            cr.raise_for_status()
            issue["fields"]["comment"]["comments"] = cr.json().get("comments", [])

    return issues


def collect_events(
    issues: list[dict[str, Any]],
) -> tuple[
    list[tuple[int, dict[str, Any]]],
    list[tuple[int, str, str, dict[str, Any]]],
    list[tuple[int, str, str, dict[str, Any]]],
]:
    """
    Извлекает из списка задач три отсортированных по ID потока событий:
    задачи, комментарии и записи истории изменений (changelog).
    Сортировка по числовому ID гарантирует хронологический порядок уведомлений.
    """
    issue_list: list[tuple[int, dict[str, Any]]] = []
    comments: list[tuple[int, str, str, dict[str, Any]]] = []
    histories: list[tuple[int, str, str, dict[str, Any]]] = []

    for issue in issues:
        issue_id = int(issue["id"])
        key = issue["key"]
        summary = issue.get("fields", {}).get("summary", "")
        issue_list.append((issue_id, issue))

        for c in issue.get("fields", {}).get("comment", {}).get("comments", []):
            comments.append((int(c["id"]), key, summary, c))

        for h in issue.get("changelog", {}).get("histories", []):
            histories.append((int(h["id"]), key, summary, h))

    issue_list.sort(key=lambda x: x[0])
    comments.sort(key=lambda x: x[0])
    histories.sort(key=lambda x: x[0])
    return issue_list, comments, histories


def is_ignored(user: dict[str, Any] | None) -> bool:
    """
    Проверяет, нужно ли пропустить событие от указанного пользователя
    (чтобы не получать уведомления о собственных действиях).
    Поддерживает username (Server/DC), email, accountId и displayName (Cloud).
    """
    if not JIRA_IGNORE_USER or not user:
        return False
    return JIRA_IGNORE_USER in {
        user.get("name"),
        user.get("emailAddress"),
        user.get("accountId"),
        user.get("displayName"),
    }


def main() -> None:
    # Если state.json ещё нет — это первый запуск (инициализация)
    init_mode = not STATE_FILE.exists()
    issues = fetch_issues(init_mode=init_mode)
    issue_list, comments, histories = collect_events(issues)

    max_issue_id = issue_list[-1][0] if issue_list else 0
    max_comment_id = comments[-1][0] if comments else 0
    max_history_id = histories[-1][0] if histories else 0

    if init_mode:
        state = {
            "last_issue_id": max_issue_id,
            "last_comment_id": max_comment_id,
            "last_history_id": max_history_id,
        }
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        send_telegram(
            f"✅ <b>Jira Notifier запущен</b>\nПроект: <b>{html.escape(JIRA_PROJECT)}</b>\n"
            f"Новые задачи, обновления и комментарии будут приходить сюда по расписанию."
        )
        print(f"Initialized state: {state}")
        return

    # Загружаем ID последних уже отправленных событий
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    last_issue_id = int(state.get("last_issue_id", 0))
    last_comment_id = int(state.get("last_comment_id", 0))
    last_history_id = int(state.get("last_history_id", 0))

    # 1. Новые задачи
    for iid, issue in issue_list:
        if iid <= last_issue_id:
            continue
        fields = issue.get("fields", {})
        creator = fields.get("creator") or {}
        if is_ignored(creator):
            continue
        key = issue["key"]
        summary = html.escape(fields.get("summary", ""))
        author_name = html.escape(creator.get("displayName") or creator.get("name") or "Unknown")
        assignee = fields.get("assignee")
        assignee_name = html.escape(assignee.get("displayName")) if assignee else "Не назначен"
        link = f"{JIRA_URL}/browse/{key}"
        send_telegram(
            f'🆕 <b><a href="{link}">{key}</a></b> — {summary}\n'
            f"👤 Создал: <b>{author_name}</b> | Исполнитель: <b>{assignee_name}</b>"
        )

    # 2. Обновления задач (changelog)
    for hid, key, summary, h in histories:
        if hid <= last_history_id:
            continue
        author = h.get("author") or {}
        if is_ignored(author):
            continue

        changes: list[str] = []
        for item in h.get("items", []):
            field = item.get("field", "")
            if TRACKED_FIELDS and field not in TRACKED_FIELDS:
                continue
            label = TRACKED_FIELDS.get(field, field)
            old_val = html.escape(truncate(item.get("fromString") or "—", 200))
            new_val = html.escape(truncate(item.get("toString") or "—", 200))
            # Описание обычно большое — не дублируем его целиком, пишем кратко
            if field == "description":
                changes.append(f"• <b>{label}:</b> обновлено")
            else:
                changes.append(f"• <b>{label}:</b> {old_val} → {new_val}")

        if not changes:
            continue

        author_name = html.escape(author.get("displayName") or author.get("name") or "Unknown")
        link = f"{JIRA_URL}/browse/{key}"
        send_telegram(
            f'🔄 <b><a href="{link}">{key}</a></b> — {html.escape(summary)}\n'
            f"👤 <b>{author_name}</b>\n" + "\n".join(changes)
        )

    # 3. Новые комментарии
    for cid, key, summary, c in comments:
        if cid <= last_comment_id:
            continue
        author = c.get("author") or {}
        if is_ignored(author):
            continue
        author_name = html.escape(author.get("displayName") or author.get("name") or "Unknown")
        body = html.escape(truncate(c.get("body", ""), 2500))
        link = f"{JIRA_URL}/browse/{key}?focusedCommentId={cid}#comment-{cid}"
        send_telegram(
            f'💬 <b><a href="{link}">{key}</a></b> — {html.escape(summary)}\n'
            f"👤 <b>{author_name}</b>\n\n{body}"
        )

    # Сохраняем новые максимальные ID (GitHub Actions затем закоммитит state.json)
    new_state = {
        "last_issue_id": max(last_issue_id, max_issue_id),
        "last_comment_id": max(last_comment_id, max_comment_id),
        "last_history_id": max(last_history_id, max_history_id),
    }
    if new_state != state:
        STATE_FILE.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
        print(f"Updated state: {new_state}")


if __name__ == "__main__":
    main()
