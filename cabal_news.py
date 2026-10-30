import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException


LIST_URL = "https://cabaleu.playthisgame.com/en/all/List"
BASE_URL = "https://cabaleu.playthisgame.com"

OFFICIAL_STATE_FILE = Path("state.json")
LEAK_STATE_FILE = Path("leaks.json")

# Ile ID ponad najwyższe oficjalne ID sprawdzamy.
LEAK_LOOKAHEAD = 20


WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL"
)

TEST_MESSAGE = os.environ.get(
    "TEST_MESSAGE",
    "",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}

POPULATE_LAST_10 = os.environ.get(
    "POPULATE_LAST_10",
    "",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}

RUN_LEAK_SCAN = os.environ.get(
    "RUN_LEAK_SCAN",
    "",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/156 Safari/537.36"
    )
}


# Dzięki temu langdetect daje powtarzalne wyniki.
DetectorFactory.seed = 0


# =========================================================
# LISTA OFICJALNYCH NEWSÓW
# =========================================================

def extract_image_url(element):
    image = element.find("img")

    if not image:
        return ""

    image_src = (
        image.get("src")
        or image.get("data-src")
        or image.get("data-original")
        or image.get("data-lazy-src")
    )

    if not image_src:
        return ""

    return urljoin(
        BASE_URL,
        image_src,
    )


def extract_list_metadata(link):
    date_pattern = re.compile(
        r"\d{1,2}/\d{1,2}/\d{4}"
    )

    candidates = [link]

    parent = link.parent

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

    link_title = link.get_text(
        " ",
        strip=True,
    )

    if link_title:
        title = link_title

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
            remaining = remaining[
                remaining.index(title) + 1:
            ]

        elif remaining:
            remaining = remaining[1:]

        description_parts = []

        for text in remaining:

            if text in {
                title,
                date,
            }:
                continue

            if text not in description_parts:
                description_parts.append(
                    text
                )

        description = " ".join(
            description_parts
        ).strip()

    return {
        "date": date,
        "title": title.strip(),
        "description": description,
        "image_url": extract_image_url(
            best
        ),
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

        metadata = extract_list_metadata(
            link
        )

        title = (
            metadata["title"]
            or f"CABAL EU News #{news_id}"
        )

        news.append(
            {
                "id": news_id,

                "url": urljoin(
                    BASE_URL,
                    href,
                ),

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


# =========================================================
# STATE.JSON — TYLKO OFICJALNE NEWSY
# =========================================================

def load_official_ids():

    if not OFFICIAL_STATE_FILE.exists():
        return set()

    try:

        data = json.loads(
            OFFICIAL_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return {
            str(news_id)
            for news_id
            in data.get(
                "seen_ids",
                [],
            )
        }

    except Exception as error:

        print(
            f"Could not read "
            f"state.json: {error}"
        )

        return set()


def save_official_ids(ids):

    data = {
        "seen_ids": sorted(
            ids,
            key=lambda x: int(x),
            reverse=True,
        )[:500]
    }

    OFFICIAL_STATE_FILE.write_text(
        json.dumps(
            data,
            indent=2,
        ),
        encoding="utf-8",
    )


# =========================================================
# LEAKS.JSON — TYLKO WYKRYTE LEAKI
# =========================================================

def load_leak_ids():

    if not LEAK_STATE_FILE.exists():
        return set()

    try:

        data = json.loads(
            LEAK_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return {
            str(news_id)
            for news_id
            in data.get(
                "leak_ids",
                [],
            )
        }

    except Exception as error:

        print(
            f"Could not read "
            f"leaks.json: {error}"
        )

        return set()


def save_leak_ids(ids):

    data = {
        "leak_ids": sorted(
            ids,
            key=lambda x: int(x),
            reverse=True,
        )[:500]
    }

    LEAK_STATE_FILE.write_text(
        json.dumps(
            data,
            indent=2,
        ),
        encoding="utf-8",
    )


# =========================================================
# DISCORD
# =========================================================

def send_discord_embed(
    item,
    test=False,
    leak=False,
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

    if leak:

        description_parts.append(
            "🚨 **LEAK — not yet visible "
            "on the official News list**"
        )

    elif test:

        description_parts.append(
            "🧪 **TEST MESSAGE**"
        )

    if date:

        description_parts.append(
            f"📅 **{date}**"
        )

    if description:

        if len(description) > 1200:
            description = (
                description[:1197]
                + "..."
            )

        description_parts.append(
            description
        )

    if leak:

        color = 15158332

        footer = (
            "CABAL EU News • LEAK"
        )

    elif test:

        color = 16776960

        footer = (
            "CABAL EU News Monitor • TEST"
        )

    else:

        color = 16744192

        footer = "CABAL EU News"

    embed = {
        "title": title,
        "url": url,
        "color": color,
        "footer": {
            "text": footer
        },
    }

    if description_parts:

        embed["description"] = (
            "\n".join(
                description_parts
            )
        )

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

    if response.status_code == 429:

        retry_after = float(
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
            retry_after
        )

        response = requests.post(
            WEBHOOK_URL,
            json=payload,
            timeout=30,
        )

    response.raise_for_status()


def send_startup_message(
    latest,
):

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


# =========================================================
# POPULATE LAST 10
# =========================================================

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

    for item in reversed(items):

        print(
            f"Populate article: "
            f"{item['id']} - "
            f"{item['title']}"
        )

        send_discord_embed(
            item
        )

        time.sleep(0.5)

    print(
        f"Successfully populated "
        f"Discord with "
        f"{len(items)} articles."
    )


# =========================================================
# NORMALNY MONITOR /LIST
# =========================================================

def process_official_news(
    current_news,
    official_ids,
    leak_ids,
):

    state_existed = (
        OFFICIAL_STATE_FILE.exists()
    )

    # Pierwszy start.
    if not state_existed:

        for item in current_news:
            official_ids.add(
                item["id"]
            )

        save_official_ids(
            official_ids
        )

        send_startup_message(
            current_news[0]
        )

        print(
            f"Initial setup complete. "
            f"Remembered "
            f"{len(current_news)} "
            f"official articles."
        )

        return official_ids

    new_items = [
        item
        for item in current_news
        if item["id"]
        not in official_ids
    ]

    if not new_items:

        print(
            "No new official "
            "CABAL EU news."
        )

        return official_ids

    sent = 0
    promoted_leaks = 0

    for item in reversed(
        new_items
    ):

        news_id = item["id"]

        # Był wcześniej leak.
        # Teraz pojawił się oficjalnie.
        if news_id in leak_ids:

            print(
                f"Leak {news_id} "
                f"is now official: "
                f"{item['title']} - "
                f"adding to state.json "
                f"without reposting."
            )

            promoted_leaks += 1

        else:

            print(
                f"New official article: "
                f"{news_id} - "
                f"{item['title']}"
            )

            send_discord_embed(
                item
            )

            sent += 1

            time.sleep(0.5)

        # Oficjalny news zawsze trafia
        # do state.json.
        official_ids.add(
            news_id
        )

    # state.json = oficjalne newsy z List.
    for item in current_news:

        official_ids.add(
            item["id"]
        )

    save_official_ids(
        official_ids
    )

    print(
        f"Official update complete: "
        f"sent {sent}, "
        f"promoted leaks without "
        f"reposting {promoted_leaks}."
    )

    return official_ids


# =========================================================
# POBIERANIE UKRYTEGO ARTYKUŁU
# =========================================================

def get_article_page(
    news_id,
):

    url = (
        f"{BASE_URL}"
        f"/en/all/Content/"
        f"{news_id}"
        f"?pageNum=1"
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True,
        )

    except requests.RequestException as error:

        print(
            f"Leak candidate "
            f"{news_id}: "
            f"request failed: "
            f"{error}"
        )

        return None

    if response.status_code == 404:
        return None

    if response.status_code >= 400:

        print(
            f"Leak candidate "
            f"{news_id}: "
            f"HTTP "
            f"{response.status_code} "
            f"- skipped"
        )

        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    # Strona ma również H1:
    # PlayThisGame
    # Cabal
    # dlatego je ignorujemy.
    generic_titles = {
        "playthisgame",
        "cabal",
        "news",
    }

    title_node = None

    for heading in soup.find_all(
        "h1"
    ):

        text = heading.get_text(
            " ",
            strip=True,
        )

        if (
            text
            and text.lower()
            not in generic_titles
        ):
            title_node = heading

    if title_node is None:
        return None

    title = title_node.get_text(
        " ",
        strip=True,
    )

    if (
        not title
        or title.lower()
        in generic_titles
    ):
        return None

    date_pattern = re.compile(
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"(?:\s+"
        r"\d{1,2}:\d{2}:\d{2}"
        r"\s*(?:AM|PM)?)?",
        re.IGNORECASE,
    )

    date = ""
    body_parts = []

    # Czytamy tylko zawartość
    # od nagłówka artykułu do
    # "View All News".
    for node in title_node.find_all_next(
        string=True
    ):

        parent_name = getattr(
            node.parent,
            "name",
            "",
        )

        if parent_name in {
            "script",
            "style",
            "noscript",
        }:
            continue

        text = " ".join(
            str(node).split()
        )

        if not text:
            continue

        if (
            text.lower()
            == "view all news"
        ):
            break

        if (
            not date
            and date_pattern.fullmatch(
                text
            )
        ):

            date = text
            continue

        if (
            text == title
            or text.lower()
            == "news"
        ):
            continue

        if text not in body_parts:

            body_parts.append(
                text
            )

    body = " ".join(
        body_parts
    ).strip()

    # Odrzucamy puste/placeholderowe strony.
    if (
        not date
        or len(body) < 10
    ):
        return None

    image_url = ""

    # Pierwszy obrazek po tytule
    # powinien być grafiką artykułu.
    for image in title_node.find_all_next(
        "img"
    ):

        image_src = (
            image.get("src")
            or image.get("data-src")
            or image.get(
                "data-original"
            )
            or image.get(
                "data-lazy-src"
            )
        )

        if image_src:

            image_url = urljoin(
                BASE_URL,
                image_src,
            )

            break

    return {
        "id": str(news_id),

        "url": url,

        "date": date,

        "title": title,

        "description": body,

        "body": body,

        "image_url": image_url,
    }


# =========================================================
# DETEKCJA JĘZYKA
# =========================================================

def detect_english(
    text,
):

    cleaned = " ".join(
        text.split()
    )

    # Za mało tekstu = nie ryzykujemy
    # fałszywego LEAK-a.
    if len(cleaned) < 40:

        return (
            False,
            "too little text",
        )

    try:

        results = detect_langs(
            cleaned
        )

    except LangDetectException:

        return (
            False,
            "language detection failed",
        )

    summary = ", ".join(
        f"{item.lang}:"
        f"{item.prob:.2f}"
        for item in results
    )

    english_probability = next(
        (
            item.prob
            for item in results
            if item.lang == "en"
        ),
        0.0,
    )

    # Minimum 80% pewności.
    return (
        english_probability >= 0.80,
        summary,
    )


# =========================================================
# LEAK SCANNER
# =========================================================

def scan_for_leaks(
    current_news,
    official_ids,
    leak_ids,
):

    # Utwórz leaks.json nawet,
    # jeśli jeszcze nic nie znaleziono.
    if not LEAK_STATE_FILE.exists():

        save_leak_ids(
            leak_ids
        )

    listed_ids = {
        int(item["id"])
        for item in current_news
    }

    highest_official_id = max(
        listed_ids
    )

    start_id = (
        highest_official_id
        + 1
    )

    end_id = (
        highest_official_id
        + LEAK_LOOKAHEAD
    )

    print(
        f"Leak scan: checking IDs "
        f"{start_id} -> {end_id}"
    )

    found = 0

    for news_id in range(
        start_id,
        end_id + 1,
    ):

        str_id = str(
            news_id
        )

        # Już oficjalny.
        if str_id in official_ids:
            continue

        # Już wcześniej znaleziony leak.
        if str_id in leak_ids:
            continue

        article = get_article_page(
            news_id
        )

        if not article:

            print(
                f"Leak candidate "
                f"{news_id}: "
                f"no valid article"
            )

            time.sleep(0.25)

            continue

        language_text = (
            f"{article['title']}\n"
            f"{article['body']}"
        )

        (
            is_english,
            language_info,
        ) = detect_english(
            language_text
        )

        if not is_english:

            print(
                f"Leak candidate "
                f"{news_id}: "
                f"exists but is not "
                f"confidently English "
                f"({language_info}) "
                f"- skipped"
            )

            time.sleep(0.25)

            continue

        print(
            f"LEAK FOUND: "
            f"{news_id} - "
            f"{article['title']} "
            f"({language_info})"
        )

        send_discord_embed(
            article,
            leak=True,
        )

        # Tylko leaks.json.
        leak_ids.add(
            str_id
        )

        # Zapisujemy od razu,
        # żeby po błędzie kolejnego ID
        # nie wysłać leaka ponownie.
        save_leak_ids(
            leak_ids
        )

        found += 1

        time.sleep(0.5)

    print(
        f"Leak scan complete. "
        f"New English leaks "
        f"found: {found}."
    )

    return leak_ids


# =========================================================
# MAIN
# =========================================================

def main():

    if not WEBHOOK_URL:

        raise RuntimeError(
            "Missing GitHub secret: "
            "DISCORD_WEBHOOK_URL"
        )

    if (
        TEST_MESSAGE
        and POPULATE_LAST_10
    ):

        raise RuntimeError(
            "Select either "
            "TEST_MESSAGE or "
            "POPULATE_LAST_10, "
            "not both."
        )

    current_news = get_news_list()

    if not current_news:

        raise RuntimeError(
            "No CABAL news entries "
            "found. "
            "The website structure "
            "may have changed."
        )

    print(
        f"Found "
        f"{len(current_news)} "
        f"official CABAL EU "
        f"news entries. "
        f"Latest ID: "
        f"{current_news[0]['id']}"
    )

    # ---------------------------------
    # TEST
    # ---------------------------------

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

    # ---------------------------------
    # POPULATE LAST 10
    # ---------------------------------

    if POPULATE_LAST_10:

        populate_last_news(
            current_news,
            limit=10,
        )

        return

    # ---------------------------------
    # NORMALNY MONITOR
    # ---------------------------------

    official_ids = (
        load_official_ids()
    )

    leak_ids = (
        load_leak_ids()
    )

    official_ids = (
        process_official_news(
            current_news,
            official_ids,
            leak_ids,
        )
    )

    # ---------------------------------
    # LEAK SCANNER
    # ---------------------------------

    if RUN_LEAK_SCAN:

        scan_for_leaks(
            current_news,
            official_ids,
            leak_ids,
        )


if __name__ == "__main__":
    main()
