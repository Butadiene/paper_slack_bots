from __future__ import annotations

# ── 標準ライブラリ ─────────────────────────────────────
import os
import re
import time
import yaml
import logging
import datetime as dt
from datetime import timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# ── サードパーティ ───────────────────────────────────
import feedparser
import arxiv
import openai
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ── ファイルパス ──────────────────────────────────────
ROOT = Path(__file__).parent
CONFIG_FILE = Path(os.getenv("CONFIG_FILE", ROOT / "config.yaml"))
SECRETS_FILE = Path(os.getenv("SECRETS_FILE", ROOT / "secrets.yaml"))

# ── YAML ローダ ──────────────────────────────────────
def load_yaml(path: Path) -> dict | list:
    with path.open(encoding="utf-8") as fp:
        return yaml.safe_load(fp)

config: dict = load_yaml(CONFIG_FILE)
secrets: dict = load_yaml(SECRETS_FILE)

# ── 共通オブジェクト ─────────────────────────────────
TZ_TOKYO = ZoneInfo("Asia/Tokyo")
openai.api_key = secrets["openai_api_key"]

# ── ユーティリティ ──────────────────────────────────
def summarize(title: str, abstract_en: str) -> str:
    """OpenAI ChatCompletion でタイトルの和訳＋要点 3 点（日本語）を生成。"""
    rsp = openai.ChatCompletion.create(
        model=secrets["openai_model"],
        messages=[{
            "role": "user",
            "content": (
                f"{abstract_en}\n\n"
                f"これは “{title}” というタイトルの論文のAbstractです。"
                "要点を 3 点、日本語箇条書きで示し、冒頭にタイトルの和訳を付けてください。"
            )
        }],
    )
    return rsp.choices[0].message.content.strip()

def post(client: WebClient, channel: str, *,
         title: str, link: str, summary: str, abstract: str) -> None:
    """Slack に 2 メッセージ投稿"""
    try:
        client.chat_postMessage(
            channel=channel,
            attachments=[{"title": title, "text": link}],
        )
        client.chat_postMessage(
            channel=channel,
            attachments=[{"title": summary, "text": abstract}],
        )
    except SlackApiError as e:
        logging.error("Slack posting failed: %s", e)

# ── メッセージ整理 ───────────────────────────────────
def prune_old_messages(client: WebClient, channel: str,
                       bot_user_id: str, bot_id: str) -> None:
    """120-140 日前の未ピン留め bot 投稿を削除"""
    now = dt.datetime.now(tz=TZ_TOKYO)
    start_ts = (now - timedelta(days=140)).timestamp()
    end_ts = (now - timedelta(days=120)).timestamp()

    cursor: str | None = None
    while True:
        hist = client.conversations_history(channel=channel, cursor=cursor,
                                            oldest=0, limit=200)
        for msg in hist.get("messages", []):
            ts = float(msg.get("ts", 0))
            is_own = (msg.get("user") == bot_user_id) or (msg.get("bot_id") == bot_id)
            if not (is_own and start_ts <= ts < end_ts and not msg.get("pinned_to")):
                continue
            while True:
                try:
                    client.chat_delete(channel=channel, ts=msg["ts"])
                    break
                except SlackApiError as e:
                    err = e.response.get("error", "")
                    if err == "ratelimited":
                        retry = int(e.response.headers.get("Retry-After", "1"))
                        logging.warning("rate-limited; wait %s s", retry)
                        time.sleep(retry + 1)
                        continue
                    if err == "cant_delete_message":
                        logging.info("skip not-own msg %s", msg["ts"])
                        break
                    logging.warning("delete failed: %s", err)
                    break
        cursor = hist.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

# ── RSS 収集 ────────────────────────────────────────
def _extract_pub_dt(entry) -> dt.datetime | None:
    """entry から発行日時を抽出（Tokyo TZ を付与）"""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return dt.datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=TZ_TOKYO)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return dt.datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=TZ_TOKYO)

    # description 内 “… 26 April 2025” 形式
    if hasattr(entry, "description"):
        m = (
            re.search(r"Publication date:\s*(\d+\s+\w+\s+\d{4})", entry.description) or
            re.search(r"Available online\s*(\d+\s+\w+\s+\d{4})", entry.description)
        )
        if m:
            return dt.datetime.strptime(m.group(1), "%d %B %Y").replace(tzinfo=TZ_TOKYO)
    return None

def _extract_abstract(entry, tag: str) -> str:
    """abstract / description をタグ指定で取得し \n 折返しを除去"""
    if tag == "description" and hasattr(entry, "description"):
        txt = re.sub(r"</?[^>]+>", "", entry.description)
    elif tag == "content" and hasattr(entry, "content"):
        txt = entry.content[0].value
    else:                         # "summary" を想定
        txt = getattr(entry, "summary", "")
    return txt.replace("\n", " ")

def fetch_and_post_rss(client: WebClient, bot_user_id: str, bot_id: str,
                       journals: list[dict], days_back: int) -> None:
    target_date = (dt.datetime.now(tz=TZ_TOKYO) -
                   timedelta(days=days_back)).date()

    for journal in journals:
        feed = feedparser.parse(journal["rss_url"])
        tag = journal.get("abstract_tag", "summary")

        for entry in feed.entries:
            pub_dt = _extract_pub_dt(entry)
            if not pub_dt or pub_dt.date() != target_date:
                continue

            title = entry.title
            link = entry.link
            abstract_en = _extract_abstract(entry, tag)

            summary = summarize(title, abstract_en)
            post(client, journal["slack_channel_id"],
                 title=title, link=link,
                 summary=summary, abstract=abstract_en)
            time.sleep(5)

        prune_old_messages(client, journal["slack_channel_id"],
                           bot_user_id, bot_id)

# ── arXiv 収集 ───────────────────────────────────────
def fetch_and_post_arxiv(client: WebClient, bot_user_id: str, bot_id: str,
                         arxiv_cfg: dict, days_back: int) -> None:
    target_date = dt.date.today() - timedelta(days=days_back)

    channel_id = arxiv_cfg.get("slack_channel_id", "")
    categories = arxiv_cfg.get("categories", [])
    keywords_lc = [kw.lower() for kw in arxiv_cfg.get("keywords", [])]

    client_arxiv = arxiv.Client()
    search = arxiv.Search(query=" OR ".join(categories),
                          max_results=100,
                          sort_by=arxiv.SortCriterion.SubmittedDate)

    for result in client_arxiv.results(search):
        if result.published.date() != target_date:
            continue

        abstract_en = " ".join(result.summary.splitlines())
        if keywords_lc and not any(kw in abstract_en.lower() for kw in keywords_lc):
            continue

        title = result.title
        link = result.entry_id
        summary = summarize(title, abstract_en)

        post(client, channel_id,
             title=title, link=link,
             summary=summary, abstract=abstract_en)
        time.sleep(5)

    prune_old_messages(client, channel_id, bot_user_id, bot_id)

# ── メインエントリ ─────────────────────────────────
def main() -> None:
    days_back = int(config.get("days_back", 4))
    for ws in config.get("workspaces", []):
        name = ws.get("name")
        # Slack トークン解決（複数ワークスペース対応）
        tokens = secrets.get("slack_api_tokens") or {}
        if not tokens and secrets.get("slack_api_token"):
            tokens = {"default": secrets["slack_api_token"]}
        token = tokens.get(name)
        if not token:
            logging.error("Slack token not found for workspace: %s", name)
            continue

        client = WebClient(token=token)
        auth = client.auth_test()
        bot_user_id = auth.get("user_id")
        bot_id = auth.get("bot_id")

        # RSS
        journals_cfg = ws.get("journals", [])
        if journals_cfg:
            fetch_and_post_rss(client, bot_user_id, bot_id,
                               journals_cfg, days_back)

        # arXiv
        arxiv_cfg = ws.get("arxiv", {})
        if arxiv_cfg:
            fetch_and_post_arxiv(client, bot_user_id, bot_id,
                                 arxiv_cfg, days_back)

if __name__ == "__main__":
    main()
