#!/usr/bin/env python3
"""CASSELINIの特集ページRSSを監視し、新着記事をXに自動投稿する。"""

import json
import os
import sys
import xml.etree.ElementTree as ET

import requests
from requests_oauthlib import OAuth1

FEED_URL = "https://www.casselini-online.com/feature/feed/"
KNOWN_URLS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "known.json")
TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
TITLE_MAX_LEN = 100


def fetch_feed_items():
    resp = requests.get(FEED_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    items = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if link:
            items.append({"title": title, "link": link})
    return items


def load_known_urls():
    if not os.path.exists(KNOWN_URLS_PATH):
        return None
    with open(KNOWN_URLS_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_known_urls(urls):
    os.makedirs(os.path.dirname(KNOWN_URLS_PATH), exist_ok=True)
    with open(KNOWN_URLS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_tweet_text(title, link):
    if len(title) > TITLE_MAX_LEN:
        title = title[: TITLE_MAX_LEN - 1] + "…"
    return f"{title}\n{link}"


def post_tweet(text, auth):
    resp = requests.post(TWEET_ENDPOINT, json={"text": text}, auth=auth, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"tweet failed: {resp.status_code} {resp.text}")
    return resp.json()


def get_oauth():
    required = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
    return OAuth1(
        os.environ["X_API_KEY"],
        os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"],
        os.environ["X_ACCESS_SECRET"],
    )


def main():
    items = fetch_feed_items()
    if not items:
        print("feed is empty, nothing to do")
        return

    known = load_known_urls()
    current_urls = {item["link"] for item in items}

    if known is None:
        # 初回実行: 既存の特集を一括投稿しないよう、現在のフィードをそのまま既知として記録する
        save_known_urls(current_urls)
        print(f"initial seed: recorded {len(current_urls)} existing feature URLs, no tweets posted")
        return

    new_items = [item for item in items if item["link"] not in known]
    if not new_items:
        print("no new feature pages")
        return

    auth = get_oauth()
    # フィードは新着が先頭にくるため、投稿順は古い方から
    for item in reversed(new_items):
        text = build_tweet_text(item["title"], item["link"])
        post_tweet(text, auth)
        print(f"posted: {item['link']}")

    save_known_urls(known | current_urls)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
