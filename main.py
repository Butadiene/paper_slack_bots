from __future__ import annotations

import os
import datetime as dt
from datetime import timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import logging
import time
import yaml
import feedparser
import arxiv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import openai

# ── ファイルパス ──────────────────────────────────────
ROOT = Path(__file__).parent
CONFIG_FILE   = Path(os.getenv("CONFIG_FILE",   ROOT / "config.yaml"))
SECRETS_FILE  = Path(os.getenv("SECRETS_FILE",  ROOT / "secrets.yaml"))

# ── YAML ローダ ──────────────────────────────────────
def load_yaml(path: Path) -> dict | list:
    with path.open(encoding="utf-8") as fp:
        return yaml.safe_load(fp)

config: dict         = load_yaml(CONFIG_FILE)
secrets: dict        = load_yaml(SECRETS_FILE)

# ── 共通オブジェクト ─────────────────────────────────
TZ_TOKYO = ZoneInfo("Asia/Tokyo")
openai.api_key = secrets["openai_api_key"]

# ── ユーティリティ ──────────────────────────────────
def summarize(title: str, abstract_en: str) -> str:
    """
    OpenAI ChatCompletion でタイトルの和訳＋要点 3 点（日本語）を生成。
    """
    messages = [{
        "role": "user",
        "content": (
            f"{abstract_en}\n\n"
            f"これは “{title}” というタイトルの論文のAbstractです。"
            "要点を 3 点、日本語箇条書きで示し、冒頭にタイトルの和訳を付けてください。"
        )}]
    rsp = openai.ChatCompletion.create(
        model=secrets["openai_model"],
        messages=messages,
    )
    return rsp.choices[0].message.content

def post(client: WebClient, channel: str, *,
         title: str, link: str, summary: str, abstract: str) -> None:
    """
    Slack に 2 メッセージ投稿:
      1) 論文タイトル + リンク
      2) 要点（日本語）+ Abstract (英語原文)
    """
    try:
        # 1 本目: タイトルとリンク
        client.chat_postMessage(
            channel=channel,
            attachments=[{"title": title, "text": link}],
        )
        # 2 本目: 要点と原文 Abstract
        client.chat_postMessage(
            channel=channel,
            attachments=[{"title": summary, "text": abstract}],
        )
    except SlackApiError as e:
        logging.error("Slack posting failed: %s", e)

# auth_test() で BOT_USER_ID / BOT_ID を取得する部分は以前と同じ

def prune_old_messages(client: WebClient, channel: str,
                       bot_user_id: str, bot_id: str) -> None:
    """
    Delete bot messages 120-140 days ago that are not pinned.
    Handles rate limits and permission errors.
    """
    now = dt.datetime.now(tz=TZ_TOKYO)
    start_ts = (now - timedelta(days=140)).timestamp()
    end_ts = (now - timedelta(days=120)).timestamp()

    cursor: str | None = None
    while True:
        hist = client.conversations_history(
            channel=channel, cursor=cursor, oldest=0, limit=200
        )
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
def fetch_and_post_rss(client: WebClient, bot_user_id: str, bot_id: str,
                       journals: list[dict], days_back: int) -> None:
    target_date = (dt.datetime.now(tz=TZ_TOKYO) -
                   timedelta(days=days_back)).date()

    for journal in journals:
        feed = feedparser.parse(journal["rss_url"])
        for entry in feed.entries:
            # --- 1) 日付の取り出し ---
            pub_dt = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_dt = dt.datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=TZ_TOKYO)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                pub_dt = dt.datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=TZ_TOKYO)
            else:
                # ScienceDirect の場合は description から "Publication date:" を抜く
                m = re.search(r"Publication date:\s*(\d+\s+\w+\s+\d{4})", entry.description)
                if m:
                    pub_dt = dt.datetime.strptime(m.group(1), "%d %B %Y").replace(tzinfo=TZ_TOKYO)
    
            if not pub_dt or pub_dt.date() != target_date:
                continue

            title = entry.title
            link = entry.link
            
            abstract_en = (
                entry.content[0].value
                if journal["abstract_tag"] == "content"
                else entry.summary
            ).replace("\n", " ")

            if journal["abstract_tag"] == "description":
                abstract_en = re.sub(r"<\/?[^>]+>", "", entry.description)  # HTML タグを落とす
            else:
                abstract_en = entry.content[0].value

            summary = summarize(title, abstract_en)

            post(client, journal["slack_channel_id"],
                 title=title, link=link,
                 summary=summary, abstract=abstract_en)
            time.sleep(5)

        prune_old_messages(client, journal["slack_channel_id"], bot_user_id, bot_id)

# ── arXiv 収集 ───────────────────────────────────────
def fetch_and_post_arxiv(client: WebClient, bot_user_id: str, bot_id: str,
                         arxiv_cfg: dict, days_back: int) -> None:
    target_date = (dt.date.today() - timedelta(days=days_back))

    channel_id = arxiv_cfg.get("slack_channel_id", "")
    categories = arxiv_cfg.get("categories", [])
    keywords_lc = [kw.lower() for kw in arxiv_cfg.get("keywords", [])]

    client_arxiv = arxiv.Client()
    query = " OR ".join(categories)
    search = arxiv.Search(
        query=query,
        max_results=100,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )

    for result in client_arxiv.results(search):
        pub_date = result.published.date()
        if pub_date != target_date:
            continue

        abstract_en = " ".join(result.summary.splitlines())
        if not any(kw in abstract_en.lower() for kw in keywords_lc):
            continue

        title = result.title
        link = result.entry_id
        summary = summarize(title, abstract_en)

        post(client, channel_id,
             title=title, link=link,
             summary=summary, abstract=abstract_en)
        time.sleep(5)

    prune_old_messages(client, channel_id, bot_user_id, bot_id)

def main() -> None:
    days_back = int(config.get("days_back", 4))
    workspaces = config.get("workspaces", [])
    for ws in workspaces:
        name = ws.get("name")
        # Slack API トークンの取得（複数ワークスペース対応、またはレガシー対応）
        tokens = secrets.get("slack_api_tokens") or {}
        # レガシートークン対応: secrets.slack_api_token
        if not tokens and secrets.get("slack_api_token"):
            tokens = {"default": secrets.get("slack_api_token")}
        token = tokens.get(name)
        if not token:
            logging.error("Slack token not found for workspace: %s", name)
            continue
        client = WebClient(token=token)
        auth = client.auth_test()
        bot_user_id = auth.get("user_id")
        bot_id = auth.get("bot_id")

        # RSS notifications
        journals_cfg = ws.get("journals", [])
        if journals_cfg:
            fetch_and_post_rss(client, bot_user_id, bot_id, journals_cfg, days_back)

        # arXiv notifications
        arxiv_cfg = ws.get("arxiv", {})
        if arxiv_cfg:
            fetch_and_post_arxiv(client, bot_user_id, bot_id, arxiv_cfg, days_back)

if __name__ == "__main__":
    main()
