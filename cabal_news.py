import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException


EU_BASE_URL = "https://cabaleu.playthisgame.com"
NA_BASE_URL = "https://cabal.playthisgame.com"

EU_LIST_URL = f"{EU_BASE_URL}/en/all/List"
NA_LIST_URL = f"{NA_BASE_URL}/en/all/List"

OFFICIAL_STATE_FILE = Path("state.json")
LEAK_STATE_FILE = Path("leaks.json")

LEAK_LOOKAHEAD = 20
TITLE_MAX_ID_DISTANCE = 30

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
UNKNOWN_WEBHOOK_URL = os.environ.get("DISCORD_UNKNOWN_WEBHOOK_URL")

MIRON_NEWS_WEBHOOK_URL = os.environ.get("DISCORD_MIRON_NEWS")
MIRON_UNKNOWN_WEBHOOK_URL = os.environ.get(
    "DISCORD_MIRON_UNKNOWN_LEAKS"
)

NEWS_WEBHOOKS = [
    ("main", WEBHOOK_URL),
    ("miron", MIRON_NEWS_WEBHOOK_URL),
]

UNKNOWN_WEBHOOKS = [
    ("main-unknown", UNKNOWN_WEBHOOK_URL),
    ("miron-unknown", MIRON_UNKNOWN_WEBHOOK_URL),
]

MIRON_NEWS_WEBHOOKS = [
    ("miron", MIRON_NEWS_WEBHOOK_URL),
]

MIRON_UNKNOWN_WEBHOOKS = [
    ("miron-unknown", MIRON_UNKNOWN_WEBHOOK_URL),
]

TEST_MESSAGE = os.environ.get(
    "TEST_MESSAGE", ""
).lower() in {"1", "true", "yes", "on"}

POPULATE_LAST_10 = os.environ.get(
    "POPULATE_LAST_10", ""
).lower() in {"1", "true", "yes", "on"}

RUN_LEAK_SCAN = os.environ.get(
    "RUN_LEAK_SCAN", ""
).lower() in {"1", "true", "yes", "on"}

MIRON_TEST_MESSAGE = os.environ.get(
    "MIRON_TEST_MESSAGE", ""
).lower() in {"1", "true", "yes", "on"}

MIRON_POPULATE_LAST_10 = os.environ.get(
    "MIRON_POPULATE_LAST_10", ""
).lower() in {"1", "true", "yes", "on"}

MIRON_LEAK_SCAN = os.environ.get(
    "MIRON_LEAK_SCAN", ""
).lower() in {"1", "true", "yes", "on"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/156 Safari/537.36"
    )
}

DetectorFactory.seed = 0


# =========================================================
# LIST PARSING
# =========================================================

def extract_image_url(element, base_url):
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

    return urljoin(base_url, image_src)


def extract_list_metadata(link, base_url):
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
                description_parts.append(text)

        description = " ".join(
            description_parts
        ).strip()

    return {
        "date": date,
        "title": title.strip(),
        "description": description,
        "image_url": extract_image_url(
            best,
            base_url,
        ),
    }


def get_news_list(list_url, base_url):
    response = requests.get(
        list_url,
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
            r"/(?:all|news)/Content/(\d+)",
            href,
            re.IGNORECASE,
        )

        if not match:
            continue

        news_id = match.group(1)

        if news_id in found_ids:
            continue

        found_ids.add(news_id)

        metadata = extract_list_metadata(
            link,
            base_url,
        )

        news.append(
            {
                "id": news_id,
                "url": urljoin(
                    base_url,
                    href,
                ),
                "date": metadata["date"],
                "title": (
                    metadata["title"]
                    or f"CABAL News #{news_id}"
                ),
                "description": metadata[
                    "description"
                ],
                "image_url": metadata[
                    "image_url"
                ],
            }
        )

    news.sort(
        key=lambda item: int(
            item["id"]
        ),
        reverse=True,
    )

    return news


# =========================================================
# STATE.JSON
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
            for news_id in data.get(
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
# LEAKS.JSON
# =========================================================

def load_leak_state():
    default_state = {
        "leak_ids": [],
        "ignored_na_ids": [],
        "ignored_mobile_ids": [],
        "pending_ids": [],
        "unknown_notified_ids": [],
    }

    if not LEAK_STATE_FILE.exists():
        return default_state

    try:
        data = json.loads(
            LEAK_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return {
            "leak_ids": [
                str(x)
                for x in data.get(
                    "leak_ids",
                    [],
                )
            ],
            "ignored_na_ids": [
                str(x)
                for x in data.get(
                    "ignored_na_ids",
                    [],
                )
            ],
            "ignored_mobile_ids": [
                str(x)
                for x in data.get(
                    "ignored_mobile_ids",
                    [],
                )
            ],
            "pending_ids": [
                str(x)
                for x in data.get(
                    "pending_ids",
                    [],
                )
            ],
            "unknown_notified_ids": [
                str(x)
                for x in data.get(
                    "unknown_notified_ids",
                    [],
                )
            ],
        }

    except Exception as error:
        print(
            f"Could not read "
            f"leaks.json: {error}"
        )

        return default_state


def save_leak_state(state):
    data = {
        "leak_ids": sorted(
            set(state["leak_ids"]),
            key=lambda x: int(x),
            reverse=True,
        )[:500],

        "ignored_na_ids": sorted(
            set(state["ignored_na_ids"]),
            key=lambda x: int(x),
            reverse=True,
        )[:500],

        "ignored_mobile_ids": sorted(
            set(state["ignored_mobile_ids"]),
            key=lambda x: int(x),
            reverse=True,
        )[:500],

        "pending_ids": sorted(
            set(state["pending_ids"]),
            key=lambda x: int(x),
            reverse=True,
        )[:500],

        "unknown_notified_ids": sorted(
            set(state["unknown_notified_ids"]),
            key=lambda x: int(x),
            reverse=True,
        )[:500],
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

def send_payload_to_webhooks(
    webhook_group,
    payload,
    group_name,
):
    configured = [
        (name, url)
        for name, url in webhook_group
        if url
    ]

    if not configured:
        print(
            f"No configured Discord webhooks "
            f"for group: {group_name}"
        )
        return 0

    sent = 0

    for name, webhook_url in configured:
        response = requests.post(
            webhook_url,
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
                f"Discord rate limit "
                f"for {group_name}/{name}. "
                f"Retrying after "
                f"{retry_after} seconds."
            )

            time.sleep(
                retry_after
            )

            response = requests.post(
                webhook_url,
                json=payload,
                timeout=30,
            )

        response.raise_for_status()

        sent += 1

        print(
            f"Discord sent: "
            f"{group_name}/{name}"
        )

    return sent


def send_discord_embed(
    item,
    test=False,
    leak=False,
    webhook_group=None,
    group_name="news",
):
    if webhook_group is None:
        webhook_group = NEWS_WEBHOOKS

    if not any(
        url
        for _, url in webhook_group
    ):
        raise RuntimeError(
            f"No Discord webhook configured "
            f"for group: {group_name}"
        )

    base_title = (
        item.get("title")
        or "CABAL EU News"
    )

    news_id = str(
        item.get("id", "")
    ).strip()

    title = (
        f"{base_title} - {news_id}"
        if news_id
        else base_title
    )

    url = item["url"]
    date = item.get("date", "")
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
            "🚨 **LEAK — EU candidate not yet visible "
            "on the official EU News list**"
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

    send_payload_to_webhooks(
        webhook_group,
        payload,
        group_name,
    )



def send_unknown_discord_embed(
    article,
    reason,
    webhook_group=None,
    group_name="unknown",
):
    if webhook_group is None:
        webhook_group = UNKNOWN_WEBHOOKS

    if not any(
        url
        for _, url in webhook_group
    ):
        print(
            "No UNKNOWN Discord webhook is configured. "
            "UNKNOWN notification skipped."
        )
        return False

    news_id = str(
        article.get("id", "")
    ).strip()

    base_title = (
        article.get("title")
        or "CABAL Unknown Leak Candidate"
    )

    title = (
        f"{base_title} - {news_id}"
        if news_id
        else base_title
    )

    text = (
        f"{article.get('title', '')}\n"
        f"{article.get('body', '')}"
    )

    prices = extract_ecoin_prices(
        article.get("body", "")
    )

    eu_currency, na_currency = currency_markers(
        text
    )

    date = article.get(
        "date",
        "",
    )

    description_parts = [
        "⚠️ **UNKNOWN LEAK CANDIDATE**",
        (
            "An unpublished English article was found, "
            "but its region cannot yet be determined."
        ),
    ]

    if date:
        description_parts.append(
            f"📅 **{date}**"
        )

    description_parts.append(
        f"🪙 **eCoins:** "
        f"{prices if prices else 'none detected'}"
    )

    description_parts.append(
        f"🇪🇺 **EUR markers:** "
        f"{eu_currency if eu_currency else 'none'}"
    )

    description_parts.append(
        f"🇺🇸 **USD markers:** "
        f"{na_currency if na_currency else 'none'}"
    )

    description_parts.append(
        f"🧭 **Reason:** {reason}"
    )

    embed = {
        "title": title,
        "url": article["url"],
        "description": "\n".join(
            description_parts
        ),
        "color": 16753920,
        "footer": {
            "text": "CABAL News Scanner • UNKNOWN"
        },
    }

    image_url = article.get(
        "image_url",
        "",
    )

    if image_url:
        embed["thumbnail"] = {
            "url": image_url
        }

    payload = {
        "username": "CABAL Leak Scanner",
        "embeds": [
            embed
        ],
    }

    sent = send_payload_to_webhooks(
        webhook_group,
        payload,
        group_name,
    )

    return sent > 0


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
                    "Latest known EU article:\n"
                    f"**{latest['title']} "
                    f"- {latest['id']}**\n"
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

    send_payload_to_webhooks(
        NEWS_WEBHOOKS,
        payload,
        "news",
    )


# =========================================================
# POPULATE
# =========================================================

def populate_last_news(
    current_news,
    limit=10,
    miron_only=False,
):
    items = current_news[:limit]

    webhook_group = (
        MIRON_NEWS_WEBHOOKS
        if miron_only
        else NEWS_WEBHOOKS
    )

    group_name = (
        "miron-news"
        if miron_only
        else "news"
    )

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
            item,
            webhook_group=webhook_group,
            group_name=group_name,
        )
        time.sleep(0.5)

    print(
        f"Successfully populated "
        f"Discord with "
        f"{len(items)} articles."
    )


# =========================================================
# OFFICIAL EU MONITOR
# =========================================================

def process_official_news(
    current_news,
    official_ids,
    leak_state,
):
    leak_ids = set(
        leak_state["leak_ids"]
    )

    if not OFFICIAL_STATE_FILE.exists():
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
            f"official EU articles."
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

        if news_id in leak_ids:
            print(
                f"EU leak {news_id} "
                f"is now official: "
                f"{item['title']} - "
                f"adding to state.json "
                f"without reposting."
            )

            promoted_leaks += 1

        else:
            print(
                f"New official EU article: "
                f"{news_id} - "
                f"{item['title']}"
            )

            send_discord_embed(item)

            sent += 1
            time.sleep(0.5)

        official_ids.add(
            news_id
        )

    for item in current_news:
        official_ids.add(
            item["id"]
        )

    save_official_ids(
        official_ids
    )

    print(
        f"Official EU update complete: "
        f"sent {sent}, "
        f"promoted leaks without "
        f"reposting {promoted_leaks}."
    )

    return official_ids


# =========================================================
# ARTICLE PARSING
# =========================================================

def get_article_page(
    news_id,
    cache=None,
):
    str_id = str(news_id)

    if (
        cache is not None
        and str_id in cache
    ):
        return cache[str_id]

    url = (
        f"{EU_BASE_URL}"
        f"/en/all/Content/"
        f"{str_id}"
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
            f"Article {str_id}: "
            f"request failed: "
            f"{error}"
        )

        if cache is not None:
            cache[str_id] = None

        return None

    if response.status_code == 404:
        if cache is not None:
            cache[str_id] = None
        return None

    if response.status_code >= 400:
        print(
            f"Article {str_id}: "
            f"HTTP "
            f"{response.status_code} "
            f"- skipped"
        )

        if cache is not None:
            cache[str_id] = None

        return None

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    generic_titles = {
        "playthisgame",
        "cabal",
        "news",
    }

    title_node = None

    for heading in soup.find_all(
        ["h1", "h2"]
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
            break

    if title_node is None:
        if cache is not None:
            cache[str_id] = None
        return None

    title = title_node.get_text(
        " ",
        strip=True,
    )

    date_pattern = re.compile(
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"(?:\s+"
        r"\d{1,2}:\d{2}:\d{2}"
        r"\s*(?:AM|PM)?)?",
        re.IGNORECASE,
    )

    date = ""
    body_parts = []

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

    if (
        not date
        or len(body) < 10
    ):
        if cache is not None:
            cache[str_id] = None
        return None

    image_url = ""

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
                EU_BASE_URL,
                image_src,
            )
            break

    article = {
        "id": str_id,
        "url": url,
        "date": date,
        "title": title,
        "description": body,
        "body": body,
        "image_url": image_url,
    }

    if cache is not None:
        cache[str_id] = article

    return article


# =========================================================
# PRODUCT / PLATFORM DETECTION
# =========================================================

def is_mobile_article(article):
    """
    CABAL Mobile articles can appear in the same numeric Content ID space.
    An exact cabalmobile.playthisgame.com link is treated as a hard signal
    that the article belongs to CABAL Mobile, not CABAL Online EU/NA.
    """
    text = (
        f"{article.get('title', '')}\n"
        f"{article.get('body', '')}\n"
        f"{article.get('description', '')}"
    ).lower()

    return (
        "cabalmobile.playthisgame.com"
        in text
    )


# =========================================================
# LANGUAGE
# =========================================================

def detect_english(text):
    cleaned = " ".join(
        text.split()
    )

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

    return (
        english_probability >= 0.80,
        summary,
    )


# =========================================================
# TITLE MATCHING
# =========================================================

def normalize_title(title):
    text = (
        title.lower()
        .replace("’", "'")
    )

    text = re.sub(
        r"\b\d{1,2}[/-]"
        r"\d{1,2}[/-]"
        r"\d{2,4}\b",
        " ",
        text,
    )

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text,
    )

    return " ".join(
        text.split()
    )


def titles_match(
    title_a,
    title_b,
):
    a = normalize_title(
        title_a
    )
    b = normalize_title(
        title_b
    )

    if not a or not b:
        return False

    if a == b:
        return True

    if (
        len(a) < 8
        or len(b) < 8
    ):
        return False

    similarity = SequenceMatcher(
        None,
        a,
        b,
    ).ratio()

    return similarity >= 0.92


def find_matching_items(
    candidate_id,
    candidate_title,
    news_list,
    max_id_distance=TITLE_MAX_ID_DISTANCE,
):
    candidate_id = int(
        candidate_id
    )

    matches = []

    for item in news_list:
        item_id = int(
            item["id"]
        )

        if (
            abs(
                item_id
                - candidate_id
            )
            > max_id_distance
        ):
            continue

        if titles_match(
            candidate_title,
            item["title"],
        ):
            matches.append(
                item
            )

    return matches


# =========================================================
# ECOIN COMPARISON
# =========================================================

def extract_ecoin_prices(text):
    if not text:
        return []

    matches = re.findall(
        r"(\d[\d,\.\s]*)"
        r"\s*e\s*coins?\b",
        text,
        re.IGNORECASE,
    )

    prices = []

    for raw_value in matches:
        digits = re.sub(
            r"[^\d]",
            "",
            raw_value,
        )

        if not digits:
            continue

        try:
            prices.append(
                int(digits)
            )
        except ValueError:
            continue

    return prices


def compare_ecoin_prices(
    article_a,
    article_b,
):
    prices_a = sorted(
        extract_ecoin_prices(
            article_a.get(
                "body",
                "",
            )
        )
    )

    prices_b = sorted(
        extract_ecoin_prices(
            article_b.get(
                "body",
                "",
            )
        )
    )

    if (
        not prices_a
        or not prices_b
    ):
        return None

    if len(prices_a) != len(
        prices_b
    ):
        return None

    a_not_lower = all(
        a >= b
        for a, b in zip(
            prices_a,
            prices_b,
        )
    )

    b_not_lower = all(
        b >= a
        for a, b in zip(
            prices_a,
            prices_b,
        )
    )

    a_strictly_higher = any(
        a > b
        for a, b in zip(
            prices_a,
            prices_b,
        )
    )

    b_strictly_higher = any(
        b > a
        for a, b in zip(
            prices_a,
            prices_b,
        )
    )

    if (
        a_not_lower
        and a_strictly_higher
    ):
        return (
            "A_HIGHER",
            prices_a,
            prices_b,
        )

    if (
        b_not_lower
        and b_strictly_higher
    ):
        return (
            "B_HIGHER",
            prices_a,
            prices_b,
        )

    return None


# =========================================================
# REGION SIGNALS
# =========================================================

def region_markers(text):
    upper = text.upper()

    eu_patterns = [
        r"\bCET\b",
        r"\bCEST\b",
        r"\bEUROPEAN SERVER\b",
        r"\bEU SERVER\b",
    ]

    na_patterns = [
        r"\bPST\b",
        r"\bPDT\b",
        r"\bEST\b",
        r"\bEDT\b",
        r"\bMST\b",
        r"\bMDT\b",
        r"\bCST\b",
        r"\bCDT\b",
        r"\bNORTH AMERICA\b",
        r"\bNA SERVER\b",
    ]

    eu_hits = [
        pattern
        for pattern in eu_patterns
        if re.search(
            pattern,
            upper,
        )
    ]

    na_hits = [
        pattern
        for pattern in na_patterns
        if re.search(
            pattern,
            upper,
        )
    ]

    return (
        eu_hits,
        na_hits,
    )



def currency_markers(text):
    """
    Detect real-money regional price markers.

    CABAL regional heuristic:
    - EUR / euro / € -> EU
    - USD / dollar / $ -> NA

    We only count currency markers attached to a numeric price.
    """
    if not text:
        return [], []

    eu_patterns = [
        r"€\s*\d[\d\s,\.]*",
        r"\d[\d\s,\.]*\s*€",
        r"\bEUR\s*\d[\d\s,\.]*",
        r"\d[\d\s,\.]*\s*EUR\b",
        r"\d[\d\s,\.]*\s*euros?\b",
        r"\beuros?\s*\d[\d\s,\.]*",
    ]

    na_patterns = [
        r"\$\s*\d[\d\s,\.]*",
        r"\d[\d\s,\.]*\s*\$",
        r"\bUSD\s*\d[\d\s,\.]*",
        r"\d[\d\s,\.]*\s*USD\b",
        r"\d[\d\s,\.]*\s*dollars?\b",
        r"\bdollars?\s*\d[\d\s,\.]*",
    ]

    eu_hits = []
    na_hits = []

    for pattern in eu_patterns:
        eu_hits.extend(
            re.findall(
                pattern,
                text,
                re.IGNORECASE,
            )
        )

    for pattern in na_patterns:
        na_hits.extend(
            re.findall(
                pattern,
                text,
                re.IGNORECASE,
            )
        )

    return eu_hits, na_hits


def add_score(
    evidence,
    news_id,
    region,
    points,
    reason,
):
    bucket = evidence[
        news_id
    ]

    bucket[
        f"{region.lower()}_score"
    ] += points

    bucket["reasons"].append(
        f"{region} +{points}: "
        f"{reason}"
    )


def build_region_evidence(
    candidates,
    eu_news,
    na_news,
    article_cache,
):
    evidence = {
        item["id"]: {
            "eu_score": 0,
            "na_score": 0,
            "reasons": [],
        }
        for item in candidates
    }

    candidate_by_id = {
        item["id"]: item
        for item in candidates
    }

    # -----------------------------------------
    # 1. Regional markers inside article text.
    # -----------------------------------------

    for article in candidates:
        news_id = article["id"]

        text = (
            f"{article['title']}\n"
            f"{article['body']}"
        )

        eu_markers, na_markers = (
            region_markers(text)
        )

        if (
            eu_markers
            and not na_markers
        ):
            add_score(
                evidence,
                news_id,
                "EU",
                80,
                "EU time/region marker",
            )

        if (
            na_markers
            and not eu_markers
        ):
            add_score(
                evidence,
                news_id,
                "NA",
                80,
                "NA time/region marker",
            )

        if (
            eu_markers
            and na_markers
        ):
            evidence[
                news_id
            ]["reasons"].append(
                "Conflicting EU/NA markers"
            )

        eu_currency, na_currency = currency_markers(
            text
        )

        if (
            eu_currency
            and not na_currency
        ):
            add_score(
                evidence,
                news_id,
                "EU",
                120,
                (
                    "EUR/euro price marker found: "
                    f"{eu_currency}"
                ),
            )

        if (
            na_currency
            and not eu_currency
        ):
            add_score(
                evidence,
                news_id,
                "NA",
                120,
                (
                    "USD/dollar price marker found: "
                    f"{na_currency}"
                ),
            )

        if (
            eu_currency
            and na_currency
        ):
            evidence[
                news_id
            ]["reasons"].append(
                "Conflicting EUR/USD price markers"
            )

    # -----------------------------------------
    # 2. Candidate vs candidate:
    # same title + eCoin price comparison.
    #
    # User-observed CABAL rule:
    # EU eCoin prices are higher than NA.
    # -----------------------------------------

    candidate_ids = list(
        candidate_by_id.keys()
    )

    for index_a in range(
        len(candidate_ids)
    ):
        for index_b in range(
            index_a + 1,
            len(candidate_ids),
        ):
            id_a = candidate_ids[
                index_a
            ]
            id_b = candidate_ids[
                index_b
            ]

            article_a = (
                candidate_by_id[id_a]
            )
            article_b = (
                candidate_by_id[id_b]
            )

            if (
                abs(
                    int(id_a)
                    - int(id_b)
                )
                > TITLE_MAX_ID_DISTANCE
            ):
                continue

            if not titles_match(
                article_a["title"],
                article_b["title"],
            ):
                continue

            comparison = (
                compare_ecoin_prices(
                    article_a,
                    article_b,
                )
            )

            if not comparison:
                continue

            result, prices_a, prices_b = (
                comparison
            )

            if result == "A_HIGHER":
                add_score(
                    evidence,
                    id_a,
                    "EU",
                    120,
                    (
                        f"same-title candidate {id_b} "
                        f"has lower eCoin prices "
                        f"{prices_b}; this article "
                        f"has {prices_a}"
                    ),
                )

                add_score(
                    evidence,
                    id_b,
                    "NA",
                    120,
                    (
                        f"same-title candidate {id_a} "
                        f"has higher eCoin prices "
                        f"{prices_a}; this article "
                        f"has {prices_b}"
                    ),
                )

            elif result == "B_HIGHER":
                add_score(
                    evidence,
                    id_b,
                    "EU",
                    120,
                    (
                        f"same-title candidate {id_a} "
                        f"has lower eCoin prices "
                        f"{prices_a}; this article "
                        f"has {prices_b}"
                    ),
                )

                add_score(
                    evidence,
                    id_a,
                    "NA",
                    120,
                    (
                        f"same-title candidate {id_b} "
                        f"has higher eCoin prices "
                        f"{prices_b}; this article "
                        f"has {prices_a}"
                    ),
                )

    # -----------------------------------------
    # 3. Compare candidate to recent official
    # matching titles from EU and NA lists.
    # -----------------------------------------

    for article in candidates:
        news_id = article["id"]

        eu_matches = find_matching_items(
            news_id,
            article["title"],
            eu_news,
        )

        na_matches = find_matching_items(
            news_id,
            article["title"],
            na_news,
        )

        # Title alone is only a weak hint.
        if (
            na_matches
            and not eu_matches
        ):
            add_score(
                evidence,
                news_id,
                "EU",
                40,
                "matching recent title exists on NA /List",
            )

        if (
            eu_matches
            and not na_matches
        ):
            add_score(
                evidence,
                news_id,
                "NA",
                40,
                "matching recent title exists on EU /List",
            )

        # Stronger: compare actual eCoin prices
        # against a known official region.
        for match in na_matches:
            official_na_article = (
                get_article_page(
                    match["id"],
                    cache=article_cache,
                )
            )

            if not official_na_article:
                continue

            comparison = (
                compare_ecoin_prices(
                    article,
                    official_na_article,
                )
            )

            if not comparison:
                continue

            result, candidate_prices, na_prices = (
                comparison
            )

            if result == "A_HIGHER":
                add_score(
                    evidence,
                    news_id,
                    "EU",
                    120,
                    (
                        f"higher eCoin prices "
                        f"{candidate_prices} than "
                        f"official NA {match['id']} "
                        f"{na_prices}"
                    ),
                )

        for match in eu_matches:
            official_eu_article = (
                get_article_page(
                    match["id"],
                    cache=article_cache,
                )
            )

            if not official_eu_article:
                continue

            comparison = (
                compare_ecoin_prices(
                    article,
                    official_eu_article,
                )
            )

            if not comparison:
                continue

            result, candidate_prices, eu_prices = (
                comparison
            )

            if result == "B_HIGHER":
                add_score(
                    evidence,
                    news_id,
                    "NA",
                    120,
                    (
                        f"lower eCoin prices "
                        f"{candidate_prices} than "
                        f"official EU {match['id']} "
                        f"{eu_prices}"
                    ),
                )

    return evidence


def decide_region(
    evidence_entry,
):
    eu_score = evidence_entry[
        "eu_score"
    ]
    na_score = evidence_entry[
        "na_score"
    ]

    reasons = evidence_entry[
        "reasons"
    ]

    reason_text = (
        "; ".join(reasons)
        if reasons
        else "no reliable regional signal"
    )

    # Strong enough signal and clear lead.
    if (
        eu_score >= 80
        and eu_score - na_score >= 40
    ):
        return (
            "EU",
            reason_text,
        )

    if (
        na_score >= 80
        and na_score - eu_score >= 40
    ):
        return (
            "NA",
            reason_text,
        )

    return (
        "UNKNOWN",
        (
            f"{reason_text}; "
            f"EU={eu_score}, "
            f"NA={na_score}"
        ),
    )


# =========================================================
# LEAK SCANNER
# =========================================================

def scan_for_leaks(
    eu_news,
    na_news,
    official_ids,
    leak_state,
):
    if not LEAK_STATE_FILE.exists():
        save_leak_state(
            leak_state
        )

    leak_ids = set(
        leak_state["leak_ids"]
    )

    ignored_na_ids = set(
        leak_state["ignored_na_ids"]
    )

    ignored_mobile_ids = set(
        leak_state["ignored_mobile_ids"]
    )

    pending_ids = set(
        leak_state["pending_ids"]
    )

    unknown_notified_ids = set(
        leak_state["unknown_notified_ids"]
    )

    eu_ids = {
        item["id"]
        for item in eu_news
    }

    na_ids = {
        item["id"]
        for item in na_news
    }

    highest_eu_id = max(
        int(news_id)
        for news_id in eu_ids
    )

    highest_na_id = max(
        int(news_id)
        for news_id in na_ids
    )

    start_id = (
        highest_eu_id + 1
    )

    end_id = (
        max(
            highest_eu_id,
            highest_na_id,
        )
        + LEAK_LOOKAHEAD
    )

    print(
        f"Leak scan: "
        f"EU latest={highest_eu_id}, "
        f"NA latest={highest_na_id}, "
        f"checking "
        f"{start_id} -> {end_id}"
    )

    article_cache = {}
    candidates = []

    # -----------------------------------------
    # Phase 1:
    # collect existing English unpublished
    # candidate pages.
    # -----------------------------------------

    for news_id in range(
        start_id,
        end_id + 1,
    ):
        str_id = str(
            news_id
        )

        if str_id in official_ids:
            pending_ids.discard(
                str_id
            )
            continue

        if str_id in leak_ids:
            continue

        if str_id in na_ids:
            ignored_na_ids.add(
                str_id
            )

            pending_ids.discard(
                str_id
            )

            print(
                f"{news_id}: "
                f"NA official - ignored"
            )

            continue

        # Already confirmed as NA by an earlier
        # strong classification.
        if str_id in ignored_na_ids:
            continue

        # Already confirmed as CABAL Mobile.
        if str_id in ignored_mobile_ids:
            continue

        article = get_article_page(
            news_id,
            cache=article_cache,
        )

        if not article:
            pending_ids.discard(
                str_id
            )

            print(
                f"{news_id}: "
                f"no valid article"
            )

            time.sleep(0.20)
            continue

        if is_mobile_article(article):
            ignored_mobile_ids.add(
                str_id
            )

            pending_ids.discard(
                str_id
            )

            print(
                f"{news_id}: "
                f"CABAL Mobile article - ignored"
            )

            time.sleep(0.20)
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
            pending_ids.discard(
                str_id
            )

            print(
                f"{news_id}: "
                f"non-English "
                f"({language_info}) "
                f"- ignored"
            )

            time.sleep(0.20)
            continue

        article[
            "_language_info"
        ] = language_info

        candidates.append(
            article
        )

        prices = extract_ecoin_prices(
            article["body"]
        )

        eu_currency, na_currency = currency_markers(
            language_text
        )

        print(
            f"{news_id}: English candidate | "
            f"title='{article['title']}' | "
            f"eCoins={prices} | "
            f"EUR={eu_currency} | "
            f"USD={na_currency}"
        )

        time.sleep(0.15)

    print(
        f"English unpublished "
        f"candidates collected: "
        f"{len(candidates)}"
    )

    # -----------------------------------------
    # Phase 2:
    # compare candidates as a group.
    # -----------------------------------------

    evidence = build_region_evidence(
        candidates,
        eu_news,
        na_news,
        article_cache,
    )

    found = 0

    for article in sorted(
        candidates,
        key=lambda item: int(
            item["id"]
        ),
    ):
        news_id = article["id"]

        region, reason = decide_region(
            evidence[news_id]
        )

        language_info = article.get(
            "_language_info",
            "",
        )

        if region == "EU":
            print(
                f"EU LEAK FOUND: "
                f"{news_id} - "
                f"{article['title']} "
                f"[{reason}] "
                f"({language_info})"
            )

            send_discord_embed(
                article,
                leak=True,
            )

            leak_ids.add(
                news_id
            )

            pending_ids.discard(
                news_id
            )

            ignored_na_ids.discard(
                news_id
            )

            leak_state["leak_ids"] = list(
                leak_ids
            )

            leak_state[
                "ignored_na_ids"
            ] = list(
                ignored_na_ids
            )

            leak_state[
                "ignored_mobile_ids"
            ] = list(
                ignored_mobile_ids
            )

            leak_state[
                "pending_ids"
            ] = list(
                pending_ids
            )

            leak_state[
                "unknown_notified_ids"
            ] = list(
                unknown_notified_ids
            )

            save_leak_state(
                leak_state
            )

            found += 1
            time.sleep(0.5)

        elif region == "NA":
            print(
                f"{news_id}: "
                f"NA candidate - ignored "
                f"[{reason}]"
            )

            ignored_na_ids.add(
                news_id
            )

            pending_ids.discard(
                news_id
            )

        else:
            print(
                f"{news_id}: "
                f"UNKNOWN region - pending "
                f"[{reason}]"
            )

            pending_ids.add(
                news_id
            )

            if news_id not in unknown_notified_ids:
                print(
                    f"{news_id}: sending one-time "
                    f"UNKNOWN notification."
                )

                sent_unknown = send_unknown_discord_embed(
                    article,
                    reason,
                )

                if sent_unknown:
                    unknown_notified_ids.add(
                        news_id
                    )

                    leak_state["leak_ids"] = list(
                        leak_ids
                    )

                    leak_state[
                        "ignored_na_ids"
                    ] = list(
                        ignored_na_ids
                    )

                    leak_state[
                        "pending_ids"
                    ] = list(
                        pending_ids
                    )

                    leak_state[
                        "unknown_notified_ids"
                    ] = list(
                        unknown_notified_ids
                    )

                    save_leak_state(
                        leak_state
                    )

                    print(
                        f"{news_id}: UNKNOWN notification sent "
                        f"and remembered."
                    )
            else:
                print(
                    f"{news_id}: UNKNOWN already notified "
                    f"- no repeat message."
                )

    # Clean up IDs that have since become official.
    for news_id in na_ids:
        if news_id in pending_ids:
            pending_ids.discard(
                news_id
            )
            ignored_na_ids.add(
                news_id
            )

    for news_id in eu_ids:
        pending_ids.discard(
            news_id
        )

    leak_state["leak_ids"] = list(
        leak_ids
    )

    leak_state[
        "ignored_na_ids"
    ] = list(
        ignored_na_ids
    )

    leak_state[
        "ignored_mobile_ids"
    ] = list(
        ignored_mobile_ids
    )

    leak_state[
        "pending_ids"
    ] = list(
        pending_ids
    )

    leak_state[
        "unknown_notified_ids"
    ] = list(
        unknown_notified_ids
    )

    save_leak_state(
        leak_state
    )

    print(
        f"Leak scan complete. "
        f"New EU leaks: {found}. "
        f"Pending: "
        f"{len(pending_ids)}. "
        f"Known NA ignored: "
        f"{len(ignored_na_ids)}. "
        f"Mobile ignored: "
        f"{len(ignored_mobile_ids)}. "
        f"UNKNOWN notified: "
        f"{len(unknown_notified_ids)}."
    )


def scan_for_leaks_miron_only(
    eu_news,
    na_news,
    official_ids,
    leak_state,
):
    """
    Manual dry-run leak scan for Miron's Discord only.

    It sends:
    - confident EU leaks -> DISCORD_MIRON_NEWS
    - UNKNOWN candidates -> DISCORD_MIRON_UNKNOWN_LEAKS

    It never writes state.json or leaks.json and does not mark
    anything as seen/notified.
    """

    ignored_na_ids = set(
        leak_state["ignored_na_ids"]
    )

    ignored_mobile_ids = set(
        leak_state["ignored_mobile_ids"]
    )

    eu_ids = {
        item["id"]
        for item in eu_news
    }

    na_ids = {
        item["id"]
        for item in na_news
    }

    highest_eu_id = max(
        int(news_id)
        for news_id in eu_ids
    )

    highest_na_id = max(
        int(news_id)
        for news_id in na_ids
    )

    start_id = (
        highest_eu_id + 1
    )

    end_id = (
        max(
            highest_eu_id,
            highest_na_id,
        )
        + LEAK_LOOKAHEAD
    )

    print(
        "MIRON ONLY leak scan (dry-run): "
        f"EU latest={highest_eu_id}, "
        f"NA latest={highest_na_id}, "
        f"checking {start_id} -> {end_id}"
    )

    article_cache = {}
    candidates = []

    for news_id in range(
        start_id,
        end_id + 1,
    ):
        str_id = str(news_id)

        if str_id in official_ids:
            continue

        if str_id in na_ids:
            print(
                f"{news_id}: "
                f"NA official - ignored"
            )
            continue

        if str_id in ignored_na_ids:
            print(
                f"{news_id}: "
                f"known NA - ignored"
            )
            continue

        if str_id in ignored_mobile_ids:
            print(
                f"{news_id}: "
                f"known CABAL Mobile - ignored"
            )
            continue

        article = get_article_page(
            news_id,
            cache=article_cache,
        )

        if not article:
            print(
                f"{news_id}: "
                f"no valid article"
            )
            time.sleep(0.20)
            continue

        if is_mobile_article(article):
            print(
                f"{news_id}: "
                f"CABAL Mobile article - ignored"
            )
            time.sleep(0.20)
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
                f"{news_id}: "
                f"non-English "
                f"({language_info}) "
                f"- ignored"
            )
            time.sleep(0.20)
            continue

        article[
            "_language_info"
        ] = language_info

        candidates.append(
            article
        )

        prices = extract_ecoin_prices(
            article["body"]
        )

        eu_currency, na_currency = currency_markers(
            language_text
        )

        print(
            f"{news_id}: English candidate | "
            f"title='{article['title']}' | "
            f"eCoins={prices} | "
            f"EUR={eu_currency} | "
            f"USD={na_currency}"
        )

        time.sleep(0.15)

    print(
        f"MIRON ONLY English unpublished "
        f"candidates collected: "
        f"{len(candidates)}"
    )

    evidence = build_region_evidence(
        candidates,
        eu_news,
        na_news,
        article_cache,
    )

    sent_eu = 0
    sent_unknown = 0

    for article in sorted(
        candidates,
        key=lambda item: int(
            item["id"]
        ),
    ):
        news_id = article["id"]

        region, reason = decide_region(
            evidence[news_id]
        )

        if region == "EU":
            print(
                f"MIRON ONLY EU LEAK: "
                f"{news_id} - "
                f"{article['title']} "
                f"[{reason}]"
            )

            send_discord_embed(
                article,
                leak=True,
                webhook_group=MIRON_NEWS_WEBHOOKS,
                group_name="miron-news",
            )

            sent_eu += 1
            time.sleep(0.5)

        elif region == "NA":
            print(
                f"{news_id}: "
                f"NA candidate - not sent "
                f"[{reason}]"
            )

        else:
            print(
                f"MIRON ONLY UNKNOWN: "
                f"{news_id} - "
                f"{article['title']} "
                f"[{reason}]"
            )

            sent = send_unknown_discord_embed(
                article,
                reason,
                webhook_group=MIRON_UNKNOWN_WEBHOOKS,
                group_name="miron-unknown",
            )

            if sent:
                sent_unknown += 1

            time.sleep(0.5)

    print(
        "MIRON ONLY leak scan complete. "
        f"EU leaks sent: {sent_eu}. "
        f"UNKNOWN sent: {sent_unknown}. "
        "No state files were changed."
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not WEBHOOK_URL:
        raise RuntimeError(
            "Missing GitHub secret: "
            "DISCORD_WEBHOOK_URL"
        )

    selected_modes = sum(
        [
            TEST_MESSAGE,
            POPULATE_LAST_10,
            RUN_LEAK_SCAN,
            MIRON_TEST_MESSAGE,
            MIRON_POPULATE_LAST_10,
            MIRON_LEAK_SCAN,
        ]
    )

    if selected_modes > 1:
        raise RuntimeError(
            "Select only one workflow option at a time."
        )

    eu_news = get_news_list(
        EU_LIST_URL,
        EU_BASE_URL,
    )

    if not eu_news:
        raise RuntimeError(
            "No CABAL EU news entries "
            "found. "
            "The EU website structure "
            "may have changed."
        )

    print(
        f"Found "
        f"{len(eu_news)} "
        f"official EU entries. "
        f"Latest EU ID: "
        f"{eu_news[0]['id']}"
    )

    if MIRON_TEST_MESSAGE:
        latest = eu_news[0]

        print(
            f"Sending MIRON ONLY TEST message: "
            f"{latest['id']} - "
            f"{latest['title']}"
        )

        send_discord_embed(
            latest,
            test=True,
            webhook_group=MIRON_NEWS_WEBHOOKS,
            group_name="miron-news",
        )

        print(
            "Miron-only test message "
            "sent successfully."
        )

        return

    if MIRON_POPULATE_LAST_10:
        print(
            "MIRON ONLY: populating latest 10 news."
        )

        populate_last_news(
            eu_news,
            limit=10,
            miron_only=True,
        )

        return

    if MIRON_LEAK_SCAN:
        official_ids = (
            load_official_ids()
        )

        leak_state = (
            load_leak_state()
        )

        na_news = get_news_list(
            NA_LIST_URL,
            NA_BASE_URL,
        )

        if not na_news:
            raise RuntimeError(
                "Miron leak scan requested, "
                "but no NA news entries were found."
            )

        print(
            f"Found "
            f"{len(na_news)} "
            f"official NA entries. "
            f"Latest NA ID: "
            f"{na_news[0]['id']}"
        )

        scan_for_leaks_miron_only(
            eu_news,
            na_news,
            official_ids,
            leak_state,
        )

        return

    if TEST_MESSAGE:
        latest = eu_news[0]

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

    if POPULATE_LAST_10:
        populate_last_news(
            eu_news,
            limit=10,
        )

        return

    official_ids = (
        load_official_ids()
    )

    leak_state = (
        load_leak_state()
    )

    official_ids = (
        process_official_news(
            eu_news,
            official_ids,
            leak_state,
        )
    )

    if RUN_LEAK_SCAN:
        na_news = get_news_list(
            NA_LIST_URL,
            NA_BASE_URL,
        )

        if not na_news:
            raise RuntimeError(
                "Leak scan requested, "
                "but no NA news entries "
                "were found. "
                "The NA website structure "
                "may have changed."
            )

        print(
            f"Found "
            f"{len(na_news)} "
            f"official NA entries. "
            f"Latest NA ID: "
            f"{na_news[0]['id']}"
        )

        scan_for_leaks(
            eu_news,
            na_news,
            official_ids,
            leak_state,
        )


if __name__ == "__main__":
    main()
