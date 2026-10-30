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

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

TEST_MESSAGE = os.environ.get(
    "TEST_MESSAGE", ""
).lower() in {"1", "true", "yes", "on"}

POPULATE_LAST_10 = os.environ.get(
    "POPULATE_LAST_10", ""
).lower() in {"1", "true", "yes", "on"}

RUN_LEAK_SCAN = os.environ.get(
    "RUN_LEAK_SCAN", ""
).lower() in {"1", "true", "yes", "on"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/156 Safari/537.36"
    )
}

DetectorFactory.seed = 0


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
    date_pattern = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")

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
        text = candidate.get_text(" ", strip=True)
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

    link_title = link.get_text(" ", strip=True)
    if link_title:
        title = link_title

    if date_index is not None:
        if (
            not title
            or date_pattern.search(title)
            or len(title) > 150
        ):
            if len(parts) > date_index + 1:
                title = parts[date_index + 1]

        remaining = parts[date_index + 1:]

        if title in remaining:
            remaining = remaining[remaining.index(title) + 1:]
        elif remaining:
            remaining = remaining[1:]

        description_parts = []

        for text in remaining:
            if text in {title, date}:
                continue
            if text not in description_parts:
                description_parts.append(text)

        description = " ".join(description_parts).strip()

    return {
        "date": date,
        "title": title.strip(),
        "description": description,
        "image_url": extract_image_url(best, base_url),
    }


def get_news_list(list_url, base_url):
    response = requests.get(
        list_url,
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    news = []
    found_ids = set()

    for link in soup.find_all("a", href=True):
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
        metadata = extract_list_metadata(link, base_url)

        news.append(
            {
                "id": news_id,
                "url": urljoin(base_url, href),
                "date": metadata["date"],
                "title": (
                    metadata["title"]
                    or f"CABAL News #{news_id}"
                ),
                "description": metadata["description"],
                "image_url": metadata["image_url"],
            }
        )

    news.sort(
        key=lambda item: int(item["id"]),
        reverse=True,
    )

    return news


def load_official_ids():
    if not OFFICIAL_STATE_FILE.exists():
        return set()

    try:
        data = json.loads(
            OFFICIAL_STATE_FILE.read_text(encoding="utf-8")
        )
        return {
            str(news_id)
            for news_id in data.get("seen_ids", [])
        }
    except Exception as error:
        print(f"Could not read state.json: {error}")
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
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def load_leak_state():
    default_state = {
        "leak_ids": [],
        "ignored_na_ids": [],
        "pending_ids": [],
    }

    if not LEAK_STATE_FILE.exists():
        return default_state

    try:
        data = json.loads(
            LEAK_STATE_FILE.read_text(encoding="utf-8")
        )

        return {
            "leak_ids": [
                str(x)
                for x in data.get("leak_ids", [])
            ],
            "ignored_na_ids": [
                str(x)
                for x in data.get("ignored_na_ids", [])
            ],
            "pending_ids": [
                str(x)
                for x in data.get("pending_ids", [])
            ],
        }

    except Exception as error:
        print(f"Could not read leaks.json: {error}")
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
        "pending_ids": sorted(
            set(state["pending_ids"]),
            key=lambda x: int(x),
            reverse=True,
        )[:500],
    }

    LEAK_STATE_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def send_discord_embed(item, test=False, leak=False):
    if not WEBHOOK_URL:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL is not configured."
        )

    base_title = item.get("title") or "CABAL EU News"
    news_id = str(item.get("id", "")).strip()

    title = (
        f"{base_title} - {news_id}"
        if news_id
        else base_title
    )

    url = item["url"]
    date = item.get("date", "")
    description = item.get("description", "")
    image_url = item.get("image_url", "")

    description_parts = []

    if leak:
        description_parts.append(
            "🚨 **LEAK — EU candidate not yet visible "
            "on the official EU News list**"
        )
    elif test:
        description_parts.append("🧪 **TEST MESSAGE**")

    if date:
        description_parts.append(f"📅 **{date}**")

    if description:
        if len(description) > 1200:
            description = description[:1197] + "..."
        description_parts.append(description)

    if leak:
        color = 15158332
        footer = "CABAL EU News • LEAK"
    elif test:
        color = 16776960
        footer = "CABAL EU News Monitor • TEST"
    else:
        color = 16744192
        footer = "CABAL EU News"

    embed = {
        "title": title,
        "url": url,
        "color": color,
        "footer": {"text": footer},
    }

    if description_parts:
        embed["description"] = "\n".join(description_parts)

    if image_url:
        embed["thumbnail"] = {"url": image_url}

    payload = {
        "username": "CABAL EU News",
        "embeds": [embed],
    }

    response = requests.post(
        WEBHOOK_URL,
        json=payload,
        timeout=30,
    )

    if response.status_code == 429:
        retry_after = float(
            response.json().get("retry_after", 1)
        )

        print(
            f"Discord rate limit. "
            f"Retrying after {retry_after} seconds."
        )

        time.sleep(retry_after)

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
                "title": "✅ CABAL EU News Monitor is active",
                "description": (
                    "The news monitor has been started successfully."
                    "\n\n"
                    "Latest known EU article:\n"
                    f"**{latest['title']} - {latest['id']}**\n"
                    f"[Open article]({latest['url']})"
                ),
                "color": 5763719,
                "footer": {
                    "text": (
                        "Future CABAL EU news will appear here "
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


def populate_last_news(current_news, limit=10):
    items = current_news[:limit]

    print(
        f"Populating Discord with "
        f"{len(items)} latest CABAL EU articles."
    )

    for item in reversed(items):
        print(
            f"Populate article: "
            f"{item['id']} - {item['title']}"
        )

        send_discord_embed(item)
        time.sleep(0.5)

    print(
        f"Successfully populated Discord with "
        f"{len(items)} articles."
    )


def process_official_news(
    current_news,
    official_ids,
    leak_state,
):
    leak_ids = set(leak_state["leak_ids"])

    if not OFFICIAL_STATE_FILE.exists():
        for item in current_news:
            official_ids.add(item["id"])

        save_official_ids(official_ids)
        send_startup_message(current_news[0])

        print(
            f"Initial setup complete. "
            f"Remembered {len(current_news)} official EU articles."
        )
        return official_ids

    new_items = [
        item
        for item in current_news
        if item["id"] not in official_ids
    ]

    if not new_items:
        print("No new official CABAL EU news.")
        return official_ids

    sent = 0
    promoted_leaks = 0

    for item in reversed(new_items):
        news_id = item["id"]

        if news_id in leak_ids:
            print(
                f"EU leak {news_id} is now official: "
                f"{item['title']} - "
                f"adding to state.json without reposting."
            )
            promoted_leaks += 1
        else:
            print(
                f"New official EU article: "
                f"{news_id} - {item['title']}"
            )

            send_discord_embed(item)
            sent += 1
            time.sleep(0.5)

        official_ids.add(news_id)

    for item in current_news:
        official_ids.add(item["id"])

    save_official_ids(official_ids)

    print(
        f"Official EU update complete: "
        f"sent {sent}, "
        f"promoted leaks without reposting {promoted_leaks}."
    )

    return official_ids


def get_article_page(news_id):
    url = (
        f"{EU_BASE_URL}"
        f"/en/all/Content/{news_id}"
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
            f"Leak candidate {news_id}: "
            f"request failed: {error}"
        )
        return None

    if response.status_code == 404:
        return None

    if response.status_code >= 400:
        print(
            f"Leak candidate {news_id}: "
            f"HTTP {response.status_code} - skipped"
        )
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    generic_titles = {
        "playthisgame",
        "cabal",
        "news",
    }

    title_node = None

    for heading in soup.find_all(["h1", "h2"]):
        text = heading.get_text(" ", strip=True)

        if (
            text
            and text.lower() not in generic_titles
        ):
            title_node = heading
            break

    if title_node is None:
        return None

    title = title_node.get_text(" ", strip=True)

    date_pattern = re.compile(
        r"\d{1,2}/\d{1,2}/\d{4}"
        r"(?:\s+\d{1,2}:\d{2}:\d{2}"
        r"\s*(?:AM|PM)?)?",
        re.IGNORECASE,
    )

    date = ""
    body_parts = []

    for node in title_node.find_all_next(string=True):
        parent_name = getattr(node.parent, "name", "")

        if parent_name in {
            "script",
            "style",
            "noscript",
        }:
            continue

        text = " ".join(str(node).split())

        if not text:
            continue

        if text.lower() == "view all news":
            break

        if (
            not date
            and date_pattern.fullmatch(text)
        ):
            date = text
            continue

        if (
            text == title
            or text.lower() == "news"
        ):
            continue

        if text not in body_parts:
            body_parts.append(text)

    body = " ".join(body_parts).strip()

    if not date or len(body) < 10:
        return None

    image_url = ""

    for image in title_node.find_all_next("img"):
        image_src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-original")
            or image.get("data-lazy-src")
        )

        if image_src:
            image_url = urljoin(
                EU_BASE_URL,
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


def detect_english(text):
    cleaned = " ".join(text.split())

    if len(cleaned) < 40:
        return False, "too little text"

    try:
        results = detect_langs(cleaned)
    except LangDetectException:
        return False, "language detection failed"

    summary = ", ".join(
        f"{item.lang}:{item.prob:.2f}"
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


def normalize_title(title):
    text = title.lower().replace("’", "'")

    text = re.sub(
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        " ",
        text,
    )

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text,
    )

    return " ".join(text.split())


def titles_match(title_a, title_b):
    a = normalize_title(title_a)
    b = normalize_title(title_b)

    if not a or not b:
        return False

    if a == b:
        return True

    if len(a) < 8 or len(b) < 8:
        return False

    similarity = SequenceMatcher(
        None,
        a,
        b,
    ).ratio()

    return similarity >= 0.92


def title_exists_in_list(
    candidate_title,
    news_list,
):
    return any(
        titles_match(
            candidate_title,
            item["title"],
        )
        for item in news_list
    )


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
        if re.search(pattern, upper)
    ]

    na_hits = [
        pattern
        for pattern in na_patterns
        if re.search(pattern, upper)
    ]

    return eu_hits, na_hits


def classify_region(
    article,
    eu_news,
    na_news,
    eu_ids,
    na_ids,
):
    news_id = article["id"]
    title = article["title"]

    if news_id in eu_ids:
        return (
            "EU_OFFICIAL",
            "ID is already present on EU /List",
        )

    if news_id in na_ids:
        return (
            "NA",
            "ID is present on NA /List",
        )

    in_eu_titles = title_exists_in_list(
        title,
        eu_news,
    )

    in_na_titles = title_exists_in_list(
        title,
        na_news,
    )

    text = (
        f"{article['title']}\n"
        f"{article['body']}"
    )

    eu_markers, na_markers = region_markers(text)

    eu_score = 0
    na_score = 0
    reasons = []

    if in_na_titles and not in_eu_titles:
        eu_score += 80
        reasons.append(
            "matching title already exists on NA /List"
        )

    if in_eu_titles and not in_na_titles:
        na_score += 80
        reasons.append(
            "matching title already exists on EU /List"
        )

    if eu_markers and not na_markers:
        eu_score += 60
        reasons.append(
            "EU time/region marker found"
        )

    if na_markers and not eu_markers:
        na_score += 60
        reasons.append(
            "NA time/region marker found"
        )

    if eu_markers and na_markers:
        reasons.append(
            "conflicting EU and NA markers"
        )

    if (
        eu_score >= 60
        and eu_score - na_score >= 30
    ):
        return (
            "EU",
            "; ".join(reasons)
            or f"EU score {eu_score}",
        )

    if (
        na_score >= 60
        and na_score - eu_score >= 30
    ):
        return (
            "NA",
            "; ".join(reasons)
            or f"NA score {na_score}",
        )

    if in_eu_titles and in_na_titles:
        reasons.append(
            "matching title already exists on both lists"
        )

    return (
        "UNKNOWN",
        "; ".join(reasons)
        or (
            f"no reliable regional signal "
            f"(EU score {eu_score}, NA score {na_score})"
        ),
    )


def scan_for_leaks(
    eu_news,
    na_news,
    official_ids,
    leak_state,
):
    if not LEAK_STATE_FILE.exists():
        save_leak_state(leak_state)

    leak_ids = set(leak_state["leak_ids"])
    ignored_na_ids = set(
        leak_state["ignored_na_ids"]
    )
    pending_ids = set(
        leak_state["pending_ids"]
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

    start_id = highest_eu_id + 1
    end_id = (
        max(highest_eu_id, highest_na_id)
        + LEAK_LOOKAHEAD
    )

    print(
        f"Leak scan: "
        f"EU latest={highest_eu_id}, "
        f"NA latest={highest_na_id}, "
        f"checking {start_id} -> {end_id}"
    )

    found = 0

    for news_id in range(start_id, end_id + 1):
        str_id = str(news_id)

        if str_id in official_ids:
            pending_ids.discard(str_id)
            continue

        if str_id in leak_ids:
            continue

        if str_id in na_ids:
            ignored_na_ids.add(str_id)
            pending_ids.discard(str_id)

            print(
                f"{news_id}: NA official - ignored"
            )
            continue

        article = get_article_page(news_id)

        if not article:
            pending_ids.discard(str_id)

            print(
                f"{news_id}: no valid article"
            )

            time.sleep(0.20)
            continue

        language_text = (
            f"{article['title']}\n"
            f"{article['body']}"
        )

        is_english, language_info = detect_english(
            language_text
        )

        if not is_english:
            pending_ids.discard(str_id)

            print(
                f"{news_id}: non-English "
                f"({language_info}) - ignored"
            )

            time.sleep(0.20)
            continue

        region, reason = classify_region(
            article,
            eu_news,
            na_news,
            eu_ids,
            na_ids,
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

            leak_ids.add(str_id)
            pending_ids.discard(str_id)
            ignored_na_ids.discard(str_id)

            found += 1

            leak_state["leak_ids"] = list(leak_ids)
            leak_state["ignored_na_ids"] = list(
                ignored_na_ids
            )
            leak_state["pending_ids"] = list(
                pending_ids
            )

            save_leak_state(leak_state)

            time.sleep(0.5)
            continue

        if region == "NA":
            print(
                f"{news_id}: NA candidate - "
                f"not posted [{reason}]"
            )

            pending_ids.add(str_id)
            time.sleep(0.20)
            continue

        if region == "EU_OFFICIAL":
            pending_ids.discard(str_id)
            continue

        print(
            f"{news_id}: UNKNOWN region - pending "
            f"[{reason}]"
        )

        pending_ids.add(str_id)
        time.sleep(0.20)

    for news_id in na_ids:
        if news_id in pending_ids:
            pending_ids.discard(news_id)
            ignored_na_ids.add(news_id)

    for news_id in eu_ids:
        pending_ids.discard(news_id)

    leak_state["leak_ids"] = list(leak_ids)
    leak_state["ignored_na_ids"] = list(
        ignored_na_ids
    )
    leak_state["pending_ids"] = list(
        pending_ids
    )

    save_leak_state(leak_state)

    print(
        f"Leak scan complete. "
        f"New EU leaks: {found}. "
        f"Pending: {len(pending_ids)}. "
        f"Known NA ignored: {len(ignored_na_ids)}."
    )


def main():
    if not WEBHOOK_URL:
        raise RuntimeError(
            "Missing GitHub secret: "
            "DISCORD_WEBHOOK_URL"
        )

    if TEST_MESSAGE and POPULATE_LAST_10:
        raise RuntimeError(
            "Select either TEST_MESSAGE "
            "or POPULATE_LAST_10, not both."
        )

    eu_news = get_news_list(
        EU_LIST_URL,
        EU_BASE_URL,
    )

    if not eu_news:
        raise RuntimeError(
            "No CABAL EU news entries found. "
            "The EU website structure may have changed."
        )

    print(
        f"Found {len(eu_news)} official EU entries. "
        f"Latest EU ID: {eu_news[0]['id']}"
    )

    if TEST_MESSAGE:
        latest = eu_news[0]

        print(
            f"Sending TEST message: "
            f"{latest['id']} - {latest['title']}"
        )

        send_discord_embed(
            latest,
            test=True,
        )

        print(
            "Test message sent successfully."
        )
        return

    if POPULATE_LAST_10:
        populate_last_news(
            eu_news,
            limit=10,
        )
        return

    official_ids = load_official_ids()
    leak_state = load_leak_state()

    official_ids = process_official_news(
        eu_news,
        official_ids,
        leak_state,
    )

    if RUN_LEAK_SCAN:
        na_news = get_news_list(
            NA_LIST_URL,
            NA_BASE_URL,
        )

        if not na_news:
            raise RuntimeError(
                "Leak scan requested, but no NA news "
                "entries were found. "
                "The NA website structure may have changed."
            )

        print(
            f"Found {len(na_news)} official NA entries. "
            f"Latest NA ID: {na_news[0]['id']}"
        )

        scan_for_leaks(
            eu_news,
            na_news,
            official_ids,
            leak_state,
        )


if __name__ == "__main__":
    main()
