"""
Jira → Telegram Notifier
------------------------
Checks a Jira project on a schedule and sends Telegram notifications for:
  1. New issues
  2. Updates to tracked fields (status, assignee, priority, etc.)
  3. New comments

State (IDs of the last processed events) is persisted in state.json.
On the first run, the script does not spam old issues — it simply records
the current baseline and sends a startup confirmation message.
"""

import html
import json
import os
from pathlib import Path
from typing import Any

import requests

# --- Configuration from environment variables (set in GitHub Actions / Secrets) ---
JIRA_URL = os.environ["JIRA_URL"].rstrip("/")
JIRA_PROJECT = os.environ.get("JIRA_PROJECT", "PROJ")
JIRA_IGNORE_USER = os.environ.get("JIRA_IGNORE_USER", "").strip()
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TG_CHAT_ID"]

# File used to store the last processed event IDs between runs
STATE_FILE = Path("state.json")

# Jira fields whose changes will trigger a Telegram notification.
# Key = technical field name in Jira API, value = display label in Telegram.
# Leave empty {} to receive notifications for ALL field changes.
TRACKED_FIELDS = {
    "status": "Status",
    "assignee": "Assignee",
    "priority": "Priority",
    "summary": "Summary",
    "description": "Description",
    "Attachment": "Attachment",
    "resolution": "Resolution",
    "issuetype": "Issue Type",
}


def jira_headers_and_auth() -> tuple[dict[str, str], tuple[str, str] | None]:
    """
    Returns Jira authentication credentials:
    - Bearer PAT (JIRA_TOKEN) — for Jira Server / Data Center
    - Basic Auth (JIRA_USER + JIRA_PASSWORD) — for Jira Cloud (email + API Token,
      since regular passwords are disabled for REST API and the token is passed as the password)
      or standard username/password on Server/DC
    """
    token = os.environ.get("JIRA_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}, None
    return {}, (os.environ["JIRA_USER"], os.environ["JIRA_PASSWORD"])


def send_telegram(text: str) -> None:
    """Sends an HTML-formatted message to Telegram via the Bot API."""
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
    """Truncates long text to stay safely under Telegram's 4096-character limit."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "..."


def fetch_issues(init_mode: bool) -> list[dict[str, Any]]:
    """
    Fetches project issues from the Jira REST API along with their changelogs.
    - On the first run (init_mode=True), fetches the 50 most recently updated issues
      to establish the baseline IDs.
    - On regular runs, fetches issues updated in the last 72 hours
      (providing a buffer for weekends or schedule pauses).
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

    # If an issue has many comments and search returned only a subset, fetch all of them
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
    Extracts three ID-sorted event streams from the issues list:
    issues, comments, and changelog history entries.
    Sorting by numeric ID ensures notifications arrive in chronological order.
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
    Checks whether events triggered by this user should be skipped
    (so you don't get notified about your own actions).
    Supports username (Server/DC), email, accountId, and displayName (Cloud).
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
    # If state.json does not exist yet, run in initialization mode
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
            f"✅ <b>Jira Notifier started</b>\nProject: <b>{html.escape(JIRA_PROJECT)}</b>\n"
            f"New issues, updates, and comments will be sent here on schedule."
        )
        print(f"Initialized state: {state}")
        return

    # Load IDs of the last already-notified events
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    last_issue_id = int(state.get("last_issue_id", 0))
    last_comment_id = int(state.get("last_comment_id", 0))
    last_history_id = int(state.get("last_history_id", 0))

    # 1. New issues
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
        assignee_name = html.escape(assignee.get("displayName")) if assignee else "Unassigned"
        link = f"{JIRA_URL}/browse/{key}"
        send_telegram(
            f'🆕 <b><a href="{link}">{key}</a></b> — {summary}\n'
            f"👤 Created by: <b>{author_name}</b> | Assignee: <b>{assignee_name}</b>"
        )

    # 2. Issue updates (changelog)
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
            # Descriptions are usually long — send a short notice instead of the full diff
            if field == "description":
                changes.append(f"• <b>{label}:</b> updated")
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

    # 3. New comments
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

    # Save the updated max IDs (GitHub Actions will commit state.json back to the repo)
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
