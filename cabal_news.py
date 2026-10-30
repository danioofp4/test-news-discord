import json
import os
import re
import time
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

POPULATE_LAST_10 = os.environ.get(
    "POPULATE_LAST_10", ""
).lower() in {"1", "true", "yes", "on"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/156 Safari/537.36"
    )
}


def extract_image_url(element):
    image = element.find("img")

    if image:
        image_src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-original")
            or image.get("data-lazy-src")
        )

        if image_src:
            return urljoin(BASE_URL, image_src)

    # Fallback for images stored as CSS background-image.
    style = element.get("style", "")
    match = re.search(
        r'background-image\s*:\s*url\([\'"]?([^\'")]+)',
        style,
        re.IGNORECASE,
    )

    if match:
        return urljoin(
            BASE_URL,
            match.group(1),
        )

    return ""


def extract_news_metadata(link):
    """
    Find the container that contains the article's
    date, title, description and thumbnail.
    """

    date_pattern = re.compile(
        r"\d{1,2}/\d{1,2}/\d{4}"
    )

    candidates = [link]
    parent = link.parent

    # The CABAL page may have the title link inside
    # a larger article/card container.
    for _ in range(6):
        if parent is None:
            break

        candidates.append(parent)
        parent = parent.parent

    best = link
    best_score = -1

    for candidate in candidates:
        text = candidate.get_text(
            " ",
            strip=True,
        )

        score = 0

        if date_pattern.search(text):
            score += 3

        if candidate.find("img"):
            score += 2

        if len(text) > 20:
            score += 1

        # Prefer the smallest useful container.
        if score > best_score:
            best = candidate
            best_score = score

        if score >= 6:
            break

    parts = [
        text.strip()
        for text in best.stripped_strings
        if text.strip()
    ]

    date = ""
    title = ""
    description = ""

    date_index = None

    for index, text in enumerate(parts):
        if date_pattern.fullmatch(text):
            date = text
            date_index = index
            break

    # Prefer the clickable anchor text as the title.
    link_title = link.get_text(
        " ",
        strip=True,
    )

    if link_title:
        title = link_title

    # If the anchor contains too much text,
    # use the text immediately following the date.
    if date_index is not None:
        if (
            not title
            or date_pattern.search(title)
            or len(title) > 150
        ):
            if len(parts) > date_index + 1:
                title = parts[
                    date_index + 1
                ]

        remaining = parts[
            date_index + 1:
        ]

        if title in remaining:
            title_pos = remaining.index(
                title
            )

            remaining = remaining[
                title_pos + 1:
            ]

        elif remaining:
            # Usually first text after date
            # is the title itself.
            remaining = remaining[1:]

        description_parts = []

        for text in remaining:
            if text == title:
                continue

            if text == date:
                continue

            if text not in description_parts:
                description_parts.append(
                    text
                )

        description = " ".join(
            description_parts
        ).strip()

    image_url = extract_image_url(
        best
    )

    return {
        "date": date,
        "title": title.strip(),
        "description": description,
        "image_url": image_url,
    }


def get_news_list():
    response = requests.get(
        LIST_URL,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    news = []
    found_ids = set()

    for link in soup.find_all(
        "a",
        href=True,
    ):
        href = link["href"]

        match = re.search(
            r"/Content/(\d+)",
            href,
            re.IGNORECASE,
        )

        if not match:
            continue

        news_id = match.group(1)

        if news_id in found_ids:
            continue

        found_ids.add(
            news_id
        )

        url = urljoin(
            BASE_URL,
            href,
        )

        metadata = extract_news_metadata(
            link
        )

        title = metadata["title"]

        if not title:
            title = (
                f"CABAL EU News "
                f"#{news_id}"
            )

        news.append(
            {
                "id": news_id,
                "url": url,
                "date": metadata[
                    "date"
                ],
                "title": title,
                "description": metadata[
                    "description"
                ],
                "image_url": metadata[
                    "image_url"
                ],
            }
        )

    return news


def load_seen_ids():
    if not STATE_FILE.exists():
        return set()

    try:
        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return set(
            str(news_id)
            for news_id
            in data.get(
                "seen_ids",
                [],
            )
        )

    except Exception as error:
        print(
            f"Could not read "
            f"state.json: {error}"
        )

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
        json.dumps(
            data,
            indent=2,
        ),
        encoding="utf-8",
    )


def send_discord_embed(
    item,
    test=False,
):
    if not WEBHOOK_URL:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL "
            "is not configured."
        )

    title = (
        item.get("title")
        or "CABAL EU News"
    )

    url = item["url"]

    date = item.get(
        "date",
        "",
    )

    description = item.get(
        "description",
        "",
    )

    image_url = item.get(
        "image_url",
        "",
    )

    description_parts = []

    if test:
        description_parts.append(
            "🧪 **TEST MESSAGE**"
        )

    if date:
        description_parts.append(
            f"📅 **{date}**"
        )

    if description:
        # Keep plenty of margin below
        # Discord's embed description limit.
        if len(description) > 1000:
            description = (
                description[:997]
                + "..."
            )

        description_parts.append(
            description
        )

    embed = {
        "title": title,
        "url": url,
        "color": 16744192,
        "footer": {
            "text": (
                "CABAL EU News Monitor • TEST"
                if test
                else "CABAL EU News"
            )
        },
    }

    if description_parts:
        embed["description"] = (
            "\n".join(
                description_parts
            )
        )

    # Small thumbnail on the right.
    if image_url:
        embed["thumbnail"] = {
            "url": image_url
        }

    payload = {
        "username": "CABAL EU News",
        "embeds": [
            embed
        ],
    }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    # Handle Discord rate limiting.
    if response.status_code == 429:
        retry_after = (
            response.json().get(
                "retry_after",
                1,
            )
        )

        print(
            f"Discord rate limit. "
            f"Retrying after "
            f"{retry_after} seconds."
        )

        time.sleep(
            float(retry_after)
        )

        response = requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=30,
        )

    response.raise_for_status()


def send_startup_message(latest):
    payload = {
        "username": "CABAL EU News",
        "embeds": [
            {
                "title": (
                    "✅ CABAL EU News "
                    "Monitor is active"
                ),
                "description": (
                    "The news monitor has "
                    "been started successfully."
                    "\n\n"
                    "Latest known article:\n"
                    f"**{latest['title']}**\n"
                    f"[Open article]"
                    f"({latest['url']})"
                ),
                "color": 5763719,
                "footer": {
                    "text": (
                        "Future CABAL EU news "
                        "will appear here "
                        "automatically."
                    )
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


def populate_last_news(
    current_news,
    limit=10,
):
    items = current_news[:limit]

    print(
        f"Populating Discord with "
        f"{len(items)} latest "
        f"CABAL EU articles."
    )

    # CABAL list is newest-first.
    # Discord receives oldest -> newest,
    # so newest appears at the bottom.
    for item in reversed(items):
        print(
            f"Populate article: "
            f"{item['id']} - "
            f"{item['title']}"
        )

        send_discord_embed(
            item
        )

        # Small delay when sending
        # many messages at once.
        time.sleep(0.5)

    print(
        f"Successfully populated "
        f"Discord with "
        f"{len(items)} articles."
    )


def main():
    if not WEBHOOK_URL:
        raise RuntimeError(
            "Missing GitHub secret: "
            "DISCORD_WEBHOOK_URL"
        )

    current_news = get_news_list()

    if not current_news:
        raise RuntimeError(
            "No CABAL news entries found. "
            "The website structure "
            "may have changed."
        )

    print(
        f"Found {len(current_news)} "
        f"CABAL EU news entries. "
        f"Latest ID: "
        f"{current_news[0]['id']}"
    )

    # Do not allow both manual modes
    # at the same time.
    if (
        TEST_MESSAGE
        and POPULATE_LAST_10
    ):
        raise RuntimeError(
            "Select either TEST_MESSAGE "
            "or POPULATE_LAST_10, "
            "not both."
        )

    # Manual population:
    # send latest 10 articles.
    # DOES NOT modify state.json.
    if POPULATE_LAST_10:
        populate_last_news(
            current_news,
            limit=10,
        )

        return

    # Manual test:
    # send only latest article.
    # DOES NOT modify state.json.
    if TEST_MESSAGE:
        latest = current_news[0]

        print(
            f"Sending TEST message: "
            f"{latest['id']} - "
            f"{latest['title']}"
        )

        send_discord_embed(
            latest,
            test=True,
        )

        print(
            "Test message sent "
            "successfully."
        )

        return

    seen_ids = load_seen_ids()

    # First ever normal run:
    # remember all currently visible
    # articles and send startup info.
    if not STATE_FILE.exists():
        for item in current_news:
            seen_ids.add(
                item["id"]
            )

        save_seen_ids(
            seen_ids
        )

        send_startup_message(
            current_news[0]
        )

        print(
            f"Initial setup complete. "
            f"Remembered "
            f"{len(current_news)} "
            f"existing articles."
        )

        return

    new_items = [
        item
        for item in current_news
        if item["id"]
        not in seen_ids
    ]

    if not new_items:
        print(
            "No new CABAL EU news."
        )

        return

    # Website is newest-first.
    # Send new posts oldest -> newest.
    for item in reversed(
        new_items
    ):
        print(
            f"New article: "
            f"{item['id']} - "
            f"{item['title']}"
        )

        send_discord_embed(
            item
        )

        # Only mark as seen after
        # Discord accepts the message.
        seen_ids.add(
            item["id"]
        )

        time.sleep(0.5)

    # Also remember all currently
    # visible entries.
    for item in current_news:
        seen_ids.add(
            item["id"]
        )

    save_seen_ids(
        seen_ids
    )

    print(
        f"Sent {len(new_items)} "
        f"new article(s) "
        f"to Discord."
    )


if __name__ == "__main__":
    main()
