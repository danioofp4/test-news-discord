import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


LIST_URL = "https://cabaleu.playthisgame.com/en/all/List"
BASE_URL = "https://cabaleu.playthisgame.com"
STATE_FILE = Path("state.json")

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
TEST_MESSAGE = os.environ.get(
    "TEST_MESSAGE", ""
).lower() in {"1", "true", "yes", "on"}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/156 Safari/537.36"
    )
}


def get_news_list():
    response = requests.get(
        LIST_URL,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    news = []
    found_ids = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]

        match = re.search(r"/Content/(\d+)", href, re.IGNORECASE)

        if not match:
            continue

        news_id = match.group(1)

        if news_id in found_ids:
            continue

        found_ids.add(news_id)

        news.append(
            {
                "id": news_id,
                "url": urljoin(BASE_URL, href),
            }
        )

    return news


def get_news_title(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # CABAL ma kilka nagłówków H1, w tym logo PlayThisGame.
    # Szukamy właściwego tytułu artykułu.
    for heading in soup.find_all(["h1", "h2"]):
        text = heading.get_text(" ", strip=True)

        if (
            text
            and text.lower() not in {
                "playthisgame",
                "cabal",
                "news",
            }
        ):
            return text

    # Fallback: tytuł dokumentu HTML.
    if soup.title:
        title = soup.title.get_text(" ", strip=True)

        if " - " in title:
            return title.rsplit(" - ", 1)[-1].strip()

        return title

    return "New CABAL EU News"

def load_seen_ids():
    if not STATE_FILE.exists():
        return set()

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        return set(str(x) for x in data.get("seen_ids", []))

    except Exception:
        return set()


def save_seen_ids(seen_ids):
    data = {
        "seen_ids": sorted(
            seen_ids,
            key=lambda x: int(x),
            reverse=True,
        )[:500]
    }

    STATE_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def send_discord_embed(title, url, test=False):
    if not WEBHOOK_URL:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL is not configured."
        )

    if test:
        description = (
            "🧪 **TEST MESSAGE**\n\n"
            "This is a test of the CABAL EU news monitor."
        )
        footer = "CABAL EU News Monitor • TEST"
    else:
        description = (
            "A new announcement has appeared "
            "on the CABAL EU website."
        )
        footer = "CABAL EU News Monitor"

    payload = {
        "username": "CABAL EU News",
        "embeds": [
            {
                "title": title,
                "url": url,
                "description": description,
                "color": 16744192,
                "fields": [
                    {
                        "name": "🔗 Link",
                        "value": f"[Open on CABAL EU]({url})",
                        "inline": False,
                    }
                ],
                "footer": {
                    "text": footer
                },
            }
        ],
    }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

def send_startup_message(latest):
    title = get_news_title(latest["url"])

    payload = {
        "username": "CABAL EU News",
        "embeds": [
            {
                "title": "✅ CABAL EU News Monitor is active",
                "description": (
                    "The news monitor has been started successfully.\n\n"
                    f"Latest known article:\n"
                    f"**{title}**\n"
                    f"[Open article]({latest['url']})"
                ),
                "color": 5763719,
                "footer": {
                    "text": "Future CABAL EU news will appear here automatically."
                },
            }
        ],
    }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()


def main():
    if not WEBHOOK_URL:
        raise RuntimeError(
            "Missing GitHub secret: DISCORD_WEBHOOK_URL"
        )

    current_news = get_news_list()

    if not current_news:
        raise RuntimeError(
            "No CABAL news entries found. "
            "The website structure may have changed."
        )

    seen_ids = load_seen_ids()

    # First ever run:
    # remember everything currently on the page
    # and send only a startup/test message.
    if not STATE_FILE.exists():
        for item in current_news:
            seen_ids.add(item["id"])

        save_seen_ids(seen_ids)

        send_startup_message(current_news[0])

        print(
            f"Initial setup complete. "
            f"Remembered {len(current_news)} existing articles."
        )

        return

    new_items = [
        item
        for item in current_news
        if item["id"] not in seen_ids
    ]

    if not new_items:
        print("No new CABAL EU news.")
        return

    # Website is newest-first.
    # Send multiple new posts oldest -> newest.
    for item in reversed(new_items):
        title = get_news_title(item["url"])

        print(
            f"New article: {item['id']} - {title}"
        )

        send_discord_embed(
            title,
            item["url"],
        )

        # Only mark it as seen after Discord accepted it.
        seen_ids.add(item["id"])

    # Also remember all currently visible entries.
    for item in current_news:
        seen_ids.add(item["id"])

    save_seen_ids(seen_ids)

    print(
        f"Sent {len(new_items)} new article(s) to Discord."
    )


if __name__ == "__main__":
    main()
