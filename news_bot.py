import os
import re
import io
import json
import time
import random
import hashlib
import textwrap
import subprocess
import difflib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

# ==========================================
# 1. CONFIGURATION (GitHub Secrets માંથી આવશે)
# ==========================================
IST_TZ = timezone(timedelta(hours=5, minutes=30))

CLIENT_ID = os.environ.get("CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "").strip()
BLOG_ID = os.environ.get("BLOG_ID", "6996150531679127159").strip()
BLOG_BASE_URL = os.environ.get("BLOG_BASE_URL", "https://marketmunafa.blogspot.com").strip()

RAW_GEMINI_KEYS = (
    os.environ.get("GEMINI_API_KEYS", "") + "," + os.environ.get("GEMINI_API_KEY", "")
)
GEMINI_KEYS = [
    k.strip() for k in re.split(r"[\s,;]+", RAW_GEMINI_KEYS) if len(k.strip()) > 10
]

PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
MODEL_FALLBACKS = list(dict.fromkeys([
    PRIMARY_MODEL,
    "gemini-3.5-flash-lite",
    "gemini-3.0-flash"
]))

MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "3"))
DAILY_MAX_POSTS = int(os.environ.get("DAILY_MAX_POSTS", "20"))
MAX_ARTICLE_AGE_HOURS = int(os.environ.get("MAX_ARTICLE_AGE_HOURS", "12"))

GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
AUTHOR_IMAGE_URL = os.environ.get("AUTHOR_IMAGE_URL", "").strip()

AUTHOR_IMAGE_CANDIDATES = [
    "author.png",
    "1001185905_2.png",
    "1001185905.png",
    "author.jpg",
    "author.jpeg",
    "1001181954.jpg"
]

THUMBNAIL_DIR = "thumbnails"
HISTORY_FILE = "posted_urls.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9"
}

# બધી મોટી બિઝનેસ & માર્કેટ વેબસાઈટ્સ (દરેકમાંથી અલગ-અલગ તાજા ન્યૂઝ માટે)
NEWS_FEEDS = [
    ("Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml", False),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml", False),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml", False),
    ("LiveMint", "https://www.livemint.com/rss/markets", False),
    ("LiveMint", "https://www.livemint.com/rss/companies", False),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", False),
    ("Economic Times", "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms", False),
    ("NDTV Business", "https://feeds.feedburner.com/ndtvprofit-latest", False),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss", False),
    ("Business Standard", "https://www.business-standard.com/rss/companies-101.rss", False),
    ("Business Standard", "https://www.business-standard.com/rss/finance-103.rss", False),
    ("Zee Business", "https://www.zeebiz.com/latest.xml", False),
    ("Zee Business", "https://www.zeebiz.com/india-markets.xml", False),
    ("Financial Express", "https://www.financialexpress.com/market/feed/", False),
    ("CNBC TV18", "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml", False),
    ("NYTimes Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", True),
]

TARGET_LANGUAGES = [
    ("gu", "ગુજરાતી", "Gujarati"),
    ("hi", "हिन्दी", "Hindi"),
    ("mr", "मराठी", "Marathi"),
    ("ta", "தமிழ்", "Tamil"),
    ("te", "తెలుగు", "Telugu"),
    ("kn", "ಕನ್ನಡ", "Kannada"),
    ("bn", "বাংলা", "Bengali"),
    ("ml", "മലയാളം", "Malayalam"),
    ("pa", "ਪੰਜਾਬੀ", "Punjabi"),
]

# કોઈપણ ભારતીય ભાષાના આંકડા (૦-૯, ०-९, વગેરે) ને ફરજિયાત અંગ્રેજી 0-9 માં ફેરવવા માટે
INDIC_DIGIT_BASES = [0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66]
INDIC_DIGIT_TRANS = str.maketrans({
    chr(base + d): str(d)
    for base in INDIC_DIGIT_BASES
    for d in range(10)
})

PAYWALL_HINTS = [
    "subscribe to continue", "et prime", "already a subscriber",
    "unlock this article", "sign in to read", "member-only story",
    "for unlimited access", "create a free account to continue"
]

AI_CLICHES = [
    "delve into", "tapestry", "in conclusion", "game-changer",
    "testament to", "navigating the", "ever-evolving", "rollercoaster",
    "pivotal moment", "underscores the importance", "double-edged sword",
    "landscape of", "in today's fast-paced", "it is worth noting",
    "in summary", "all eyes are on", "only time will tell"
]

STOPWORDS = {
    "india", "indian", "market", "markets", "stock", "stocks", "share", "shares",
    "price", "prices", "today", "news", "latest", "update", "report", "reports",
    "amid", "after", "over", "with", "from", "into", "that", "this", "will",
    "says", "said", "what", "next", "check", "here", "details", "know", "more"
}

THUMB_THEMES = [
    {
        "bg1": (7, 16, 36), "bg2": (18, 48, 92),
        "accent": (250, 204, 21), "accent_text": (15, 23, 42),
        "badge": (220, 38, 38), "glow": (56, 189, 248), "chart": (34, 197, 94)
    },
    {
        "bg1": (6, 28, 28), "bg2": (15, 76, 72),
        "accent": (253, 224, 71), "accent_text": (15, 23, 42),
        "badge": (234, 88, 12), "glow": (45, 212, 191), "chart": (74, 222, 128)
    },
    {
        "bg1": (24, 10, 38), "bg2": (68, 22, 105),
        "accent": (251, 191, 36), "accent_text": (15, 23, 42),
        "badge": (225, 29, 72), "glow": (192, 132, 252), "chart": (56, 189, 248)
    },
    {
        "bg1": (28, 10, 14), "bg2": (96, 20, 28),
        "accent": (250, 204, 21), "accent_text": (15, 23, 42),
        "badge": (14, 116, 144), "glow": (251, 146, 60), "chart": (250, 204, 21)
    },
    {
        "bg1": (12, 18, 30), "bg2": (30, 41, 75),
        "accent": (56, 189, 248), "accent_text": (15, 23, 42),
        "badge": (220, 38, 38), "glow": (250, 204, 21), "chart": (52, 211, 153)
    },
]

key_index = 0


# ==========================================
# 2. UTILITIES, DEDUP & COPYRIGHT SCANNER
# ==========================================
def get_ist_now():
    return datetime.now(IST_TZ)


def enforce_english_digits(obj):
    """તમામ અનુવાદિત ટેક્સ્ટમાંથી કોઈપણ ભારતીય આંકડાને અંગ્રેજી 0-9 માં ફેરવે છે."""
    if isinstance(obj, str):
        return obj.translate(INDIC_DIGIT_TRANS)
    if isinstance(obj, list):
        return [enforce_english_digits(item) for item in obj]
    if isinstance(obj, dict):
        return {k: enforce_english_digits(v) for k, v in obj.items()}
    return obj


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("posted_urls", [])
                data.setdefault("posted_titles", [])
                data.setdefault("daily_counts", {})
                return data
        except Exception:
            pass
    return {"posted_urls": [], "posted_titles": [], "daily_counts": {}}


def save_history(history):
    history["posted_urls"] = history["posted_urls"][-2000:]
    history["posted_titles"] = history["posted_titles"][-800:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def clean_html_text(raw):
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def extract_topic_keywords(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


def is_similar_topic(new_title, existing_titles):
    n1 = re.sub(r"[^a-z0-9\s]", "", new_title.lower()).strip()
    kw1 = extract_topic_keywords(new_title)
    for old_t in existing_titles:
        n2 = re.sub(r"[^a-z0-9\s]", "", str(old_t).lower()).strip()
        if not n2:
            continue
        if difflib.SequenceMatcher(None, n1, n2).ratio() >= 0.56:
            return True
        kw2 = extract_topic_keywords(old_t)
        if kw1 and kw2:
            common = kw1 & kw2
            if len(common) >= 3:
                return True
            overlap = len(common) / max(1, min(len(kw1), len(kw2)))
            if overlap >= 0.55 and len(common) >= 2:
                return True
    return False


def parse_fresh_pub_dt(pub_date_str, title, is_international=False):
    if re.search(r"('\s?2[0-5]\b|\b202[0-4]\b)", title):
        return None
    if not pub_date_str:
        return None

    s = pub_date_str.strip()
    s = re.sub(r"\bIST\b", "+0530", s)
    s = re.sub(r"\bGMT\b", "+0000", s)

    dt = None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except Exception:
                    continue
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST_TZ if not is_international else timezone.utc)

    dt_ist = dt.astimezone(IST_TZ)
    now_ist = get_ist_now()
    age_hours = (now_ist - dt_ist).total_seconds() / 3600.0

    max_hours = 24 if is_international else MAX_ARTICLE_AGE_HOURS
    if not is_international and dt_ist.date() != now_ist.date() and age_hours > 8:
        return None
    if -1.5 <= age_hours <= max_hours:
        return dt_ist
    return None


def find_copied_phrases(source_text, draft_text, ngram_size=7):
    src_words = re.findall(r"[a-z0-9]+", source_text.lower())
    drf_words = re.findall(r"[a-z0-9]+", draft_text.lower())
    if len(src_words) < ngram_size or len(drf_words) < ngram_size:
        return []
    src_ngrams = {
        " ".join(src_words[i:i + ngram_size])
        for i in range(len(src_words) - ngram_size + 1)
    }
    matches = []
    for i in range(len(drf_words) - ngram_size + 1):
        tokens = drf_words[i:i + ngram_size]
        gram = " ".join(tokens)
        if gram in src_ngrams and gram not in matches:
            num_tokens = sum(1 for t in tokens if re.match(r"^(fy\d+|q[1-4]|\d+|crore|lakh|rs|usd|inr|percent)$", t))
            if num_tokens >= 3:
                continue
            matches.append(gram)
            if len(matches) >= 10:
                break
    return matches


def find_ai_cliches(text):
    low = text.lower()
    return [c for c in AI_CLICHES if c in low]


def flatten_article_json(article_json):
    parts = [
        article_json.get("seo_title", ""),
        article_json.get("intro", ""),
        " ".join(article_json.get("key_highlights", [])),
        " ".join(s.get("heading", "") + " " + s.get("body", "") for s in article_json.get("sections", [])),
        " ".join(f.get("q", "") + " " + f.get("a", "") for f in article_json.get("faqs", [])),
        article_json.get("conclusion", "")
    ]
    return " ".join(parts)


# ==========================================
# 3. BLOGGER LIVE HISTORY & INTERNAL LINKS
# ==========================================
def get_blogger_access_token():
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise RuntimeError("Blogger OAuth credentials missing in GitHub Secrets.")
    res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token"
        },
        timeout=15
    )
    if res.status_code != 200:
        raise RuntimeError(f"OAuth Token Error: {res.text}")
    return res.json()["access_token"]


def fetch_recent_blogger_posts(access_token, limit=50):
    try:
        url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts?maxResults={limit}&status=live&fetchBodies=false"
        res = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
        if res.status_code == 200:
            items = res.json().get("items", [])
            return [
                {"title": it.get("title", ""), "url": it.get("url", ""), "labels": it.get("labels", [])}
                for it in items if it.get("title") and it.get("url")
            ]
    except Exception as e:
        print(f"[Recent Posts Fetch Info] {e}")
    return []


def pick_related_internal_links(recent_posts, current_tags, max_links=3):
    if not recent_posts:
        return []
    tag_set = {str(t).lower() for t in current_tags}
    scored = []
    for p in recent_posts[:25]:
        p_labels = {str(l).lower() for l in p.get("labels", [])}
        score = len(tag_set & p_labels)
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:max_links]]


# ==========================================
# 4. ALL-WEBSITE SCANNER (RSS + ATOM + JSON-LD)
# ==========================================
def extract_jsonld_article_body(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or script.get_text()
            if not raw:
                continue
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for obj in items:
                if isinstance(obj, dict):
                    if obj.get("articleBody") and len(str(obj["articleBody"])) > 350:
                        return clean_html_text(str(obj["articleBody"]))
                    if isinstance(obj.get("@graph"), list):
                        for g in obj["@graph"]:
                            if isinstance(g, dict) and g.get("articleBody") and len(str(g["articleBody"])) > 350:
                                return clean_html_text(str(g["articleBody"]))
        except Exception:
            continue
    return None


def extract_full_public_article(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=14)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.content, "html.parser")

        ld_body = extract_jsonld_article_body(soup)

        for tag in soup.find_all(["script", "style", "nav", "footer", "aside", "form", "noscript", "iframe"]):
            tag.decompose()

        body_text_low = soup.get_text(" ", strip=True).lower()
        container = (
            soup.find("article")
            or soup.find("div", class_=re.compile(r"(arttext|article-body|story-content|content_wrapper|main-content|story_details|article_content|story-element)", re.I))
            or soup
        )

        paragraphs = []
        for p in container.find_all("p"):
            txt = " ".join(p.get_text(" ", strip=True).split())
            low = txt.lower()
            if len(txt) < 40:
                continue
            if any(h in low for h in ["also read", "read more", "download app", "follow us on", "whatsapp channel", "copyright", "all rights reserved", "disclaimer:"]):
                continue
            if txt not in paragraphs:
                paragraphs.append(txt)

        html_text = "\n\n".join(paragraphs)
        full_text = ld_body if (ld_body and len(ld_body) > len(html_text)) else html_text

        if not full_text or len(full_text) < 350:
            return None
        if any(p_hint in body_text_low for p_hint in PAYWALL_HINTS) and len(full_text) < 850:
            return None
        return full_text[:6500]
    except Exception:
        return None


def parse_feed_entries(xml_bytes):
    entries = []
    root = ET.fromstring(xml_bytes)

    for item in root.findall(".//item")[:15]:
        title = clean_html_text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or "").strip()
        if title and link:
            entries.append((title, link, pub_date))

    if not entries:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns)[:15]:
            title = clean_html_text(entry.findtext("atom:title", "", ns))
            link_el = entry.find("atom:link", ns)
            link = (link_el.attrib.get("href", "") if link_el is not None else "").strip()
            pub_date = (entry.findtext("atom:updated", "", ns) or entry.findtext("atom:published", "", ns)).strip()
            if title and link:
                entries.append((title, link, pub_date))
    return entries


def fetch_fresh_diverse_articles(history, live_blog_titles):
    posted_urls = set(history["posted_urls"])
    known_titles = list(history["posted_titles"]) + list(live_blog_titles)
    source_buckets = {}

    for source_name, feed_url, is_intl in NEWS_FEEDS:
        source_buckets.setdefault(source_name, [])
        if len(source_buckets[source_name]) >= 2:
            continue
        try:
            res = requests.get(feed_url, headers=HEADERS, timeout=12)
            if res.status_code != 200:
                continue
            entries = parse_feed_entries(res.content)

            raw_fresh = []
            for title, link, pub_date in entries:
                if link in posted_urls or "/prime/" in link.lower():
                    continue
                pub_dt = parse_fresh_pub_dt(pub_date, title, is_international=is_intl)
                if pub_dt:
                    raw_fresh.append((pub_dt, title, link, pub_date))

            raw_fresh.sort(key=lambda x: x[0], reverse=True)

            for pub_dt, title, link, pub_date in raw_fresh:
                if len(source_buckets[source_name]) >= 2:
                    break
                if is_similar_topic(title, known_titles):
                    continue
                full_text = extract_full_public_article(link)
                if not full_text:
                    continue

                posted_urls.add(link)
                known_titles.append(title)
                source_buckets[source_name].append({
                    "source": source_name,
                    "title": title,
                    "url": link,
                    "pub_date": pub_date,
                    "pub_dt": pub_dt,
                    "full_text": full_text
                })
        except Exception:
            continue

    summary_parts = [f"{src}: {len(b)}" for src, b in source_buckets.items() if len(b) > 0]
    print(f"📡 All-Website Scan Found Fresh News -> {', '.join(summary_parts) if summary_parts else 'None'}")

    active_buckets = [b for b in source_buckets.values() if b]
    active_buckets.sort(key=lambda b: b[0]["pub_dt"], reverse=True)

    candidates = []
    max_depth = max((len(b) for b in active_buckets), default=0)
    for idx in range(max_depth):
        for bucket in active_buckets:
            if idx < len(bucket):
                candidates.append(bucket[idx])
    return candidates


# ==========================================
# 5. GEMINI API WITH KEY ROTATION & FALLBACK
# ==========================================
def call_gemini(prompt, expect_json=True, max_tokens=4096, temperature=0.45):
    global key_index
    if not GEMINI_KEYS:
        raise RuntimeError("GEMINI_API_KEYS અથવા GEMINI_API_KEY GitHub Secrets માં સેટ નથી!")

    last_err = None
    for model_name in MODEL_FALLBACKS:
        for _ in range(len(GEMINI_KEYS)):
            api_key = GEMINI_KEYS[key_index % len(GEMINI_KEYS)]
            key_index += 1

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": api_key
            }
            gen_cfg = {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
            if expect_json:
                gen_cfg["responseMimeType"] = "application/json"

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_cfg
            }

            try:
                res = requests.post(url, headers=headers, json=payload, timeout=95)
                if res.status_code == 200:
                    data = res.json()
                    parts = data["candidates"][0]["content"]["parts"]
                    raw_text = "".join(p.get("text", "") for p in parts).strip()
                    if expect_json:
                        raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text, flags=re.I).strip()
                        return json.loads(raw_text)
                    return raw_text
                elif res.status_code == 429:
                    print(f"[Rate Limit] {model_name}, rotating key...")
                    time.sleep(4)
                    last_err = f"429: {res.text[:180]}"
                    continue
                else:
                    last_err = f"{model_name} HTTP {res.status_code}: {res.text[:250]}"
                    print(f"[Gemini Warning] {last_err}")
                    break
            except Exception as e:
                last_err = str(e)
                time.sleep(2)

    raise RuntimeError(f"Gemini API નિષ્ફળ ગઈ: {last_err}")


# ==========================================
# 6. SHORT-STORY #1 SEO + AUTO-HEAL ENGINE
# ==========================================
def pass1_extract_pure_facts(article):
    prompt = f"""
Extract ONLY core verified facts, figures, dates, and exact English company/stock names from the news below into concise bullet notes.
DO NOT copy prose sentences. Keep every number, percentage, and rupee/dollar amount 100% accurate.

TITLE: {article['title']}
TEXT:
{article['full_text']}

Return ONLY valid JSON:
{{
  "primary_topic": "Main company, stock, or economic topic in English",
  "focus_keyword": "High-search Google keyword in English (3-5 words)",
  "stock_and_company_names": ["Exact English Company/Stock Name 1", "Name 2"],
  "atomic_facts": ["Fact 1 with exact numbers", "Fact 2...", "..."],
  "quick_table": [
    {{"label": "Metric/Detail", "value": "Exact Value"}},
    {{"label": "Metric/Detail", "value": "Exact Value"}},
    {{"label": "Metric/Detail", "value": "Exact Value"}},
    {{"label": "Metric/Detail", "value": "Exact Value"}}
  ]
}}
"""
    return call_gemini(prompt, expect_json=True, max_tokens=1800, temperature=0.2)


def pass2_write_natural_short_story(facts_json):
    today_str = get_ist_now().strftime("%B %d, %Y")
    prompt = f"""
You are a sharp Mumbai business journalist writing a crisp, engaging SHORT STORY for 'MarketMunafa' on {today_str}.
Using ONLY the verified facts below, write a COMPLETE, 100% ORIGINAL, fast-reading, completely natural human-sounding financial short story (480 to 650 words total) in English.

NATURAL SHORT STORY + GOOGLE #1 RANKING RULES:
1. CRISP & NATURAL VOICE: Short sentences (12-18 words), zero padding, zero filler, crystal clear. No robotic transitions or textbook tone.
2. STRICTLY BANNED AI CLICHES: Never use "delve", "tapestry", "landscape", "game-changer", "in conclusion", "testament", "ever-evolving", "rollercoaster", "underscores", "pivotal moment", "it is worth noting", "in today's fast-paced", "all eyes are on", "only time will tell".
3. ENGLISH-ONLY TITLE, THUMBNAIL & TAGS:
   - `seo_title`: Strictly English, primary keyword near start (55-72 chars), factual, high-CTR, zero clickbait.
   - `thumbnail_headline`: Strictly English, punchy 4-6 words.
   - `category_badge`: Strictly English, 2-3 words (e.g. MARKET SHORT / STOCK ALERT / IPO NEWS).
   - `tags`: 8-12 strictly English popular + specific stock/sector tags.
4. SHORT-STORY STRUCTURE:
   - `intro`: 2 short, punchy paragraphs (70-95 words total).
   - `quick_table`: 3-4 crisp rows showing exact key numbers at a glance.
   - `key_highlights`: 4 sharp, scannable one-line takeaways.
   - `sections`: Exactly 3 tight, high-value H2 sections (90-120 words each):
     1) The Core Story & Numbers
     2) Why It Matters Right Now
     3) What Investors Should Track Next
   - `faqs`: 3 direct, practical "People Also Ask" Google questions with short 2-sentence answers.
   - `conclusion`: 2 sharp, natural wrap-up sentences (no buy/sell tips).

VERIFIED FACTS:
{json.dumps(facts_json, ensure_ascii=False)}

Return ONLY valid JSON with this exact schema:
{{
  "seo_title": "Keyword-first crisp English Google News headline (55-72 chars)",
  "focus_keyword": "Primary English search keyword",
  "stock_and_company_names": ["Exact English Company/Stock Name 1", "..."],
  "thumbnail_headline": "Punchy 4-6 word English headline for thumbnail",
  "category_badge": "2-3 word English badge e.g. MARKET SHORT / STOCK ALERT",
  "meta_description": "Crisp 140-155 char English meta description with focus keyword",
  "tags": ["Tag1", "Tag2", "..."],
  "quick_table": [
    {{"label": "Key Parameter", "value": "Exact Value"}}
  ],
  "intro": "2 short punchy opening paragraphs.",
  "key_highlights": ["Point 1", "Point 2", "Point 3", "Point 4"],
  "sections": [
    {{"heading": "H2 Heading 1", "body": "1-2 tight natural paragraphs"}},
    {{"heading": "H2 Heading 2", "body": "1-2 tight natural paragraphs"}},
    {{"heading": "H2 Heading 3", "body": "1-2 tight natural paragraphs"}}
  ],
  "faqs": [
    {{"q": "User search question 1?", "a": "Direct 2-sentence answer."}},
    {{"q": "User search question 2?", "a": "Direct 2-sentence answer."}},
    {{"q": "User search question 3?", "a": "Direct 2-sentence answer."}}
  ],
  "conclusion": "2 sharp closing sentences."
}}
"""
    return call_gemini(prompt, expect_json=True, max_tokens=3072, temperature=0.55)


def pass3_copyright_humanizer_with_autoheal(draft_json, article):
    flat_draft = flatten_article_json(draft_json)
    overlaps = find_copied_phrases(article["full_text"], flat_draft, ngram_size=7)
    cliches = find_ai_cliches(flat_draft)

    audit_alerts = []
    if overlaps:
        audit_alerts.append(f"REWRITE REQUIRED: Rephrase sentences containing these source overlaps in completely fresh everyday words: {overlaps}")
    if cliches:
        audit_alerts.append(f"REMOVE AI CLICHES: Replace these stiff phrases with natural journalism: {cliches}")
    alert_text = "\n".join(audit_alerts) if audit_alerts else "Polish to sound 100% like an authentic, crisp human-written short story."

    prompt = f"""
You are the Chief Editor and Copyright Compliance Officer at MarketMunafa.
Audit and polish this SHORT STORY JSON against the source for 100% originality, natural human flow, factual accuracy, and Google #1 SEO.

{alert_text}

RULES:
- ZERO copied sentences (7+ words) from the source. Express figures and facts in fresh phrasing.
- Keep it tight, crisp, and natural (480-650 words).
- Keep all numbers and facts 100% accurate to the source. No buy/sell advice.
- Keep seo_title, thumbnail_headline, category_badge, and tags strictly in English.
- Keep all 3 sections, quick_table, 4 highlights, 3 FAQs, and conclusion complete.

ORIGINAL SOURCE (fact & anti-copy check only):
{article['full_text'][:3500]}

DRAFT JSON:
{json.dumps(draft_json, ensure_ascii=False)}

Return ONLY the final polished JSON in the exact same schema.
"""
    verified = call_gemini(prompt, expect_json=True, max_tokens=3072, temperature=0.35)

    if not verified.get("seo_title") or len(verified.get("sections", [])) < 3:
        raise ValueError("Short story incomplete after audit.")

    remaining_overlaps = find_copied_phrases(article["full_text"], flatten_article_json(verified), ngram_size=7)
    if remaining_overlaps:
        print(f"   🩹 Auto-healing {len(remaining_overlaps)} overlapping phrase(s)...")
        heal_prompt = f"""
The JSON article below still shares these exact phrases with the source: {remaining_overlaps}
Rewrite ONLY the sentences containing those phrases using completely different sentence structure and fresh natural wording while preserving the exact numbers/facts.
Return ONLY the updated complete JSON in the exact same schema.

JSON TO FIX:
{json.dumps(verified, ensure_ascii=False)}
"""
        verified = call_gemini(heal_prompt, expect_json=True, max_tokens=3072, temperature=0.4)
        remaining_overlaps = find_copied_phrases(article["full_text"], flatten_article_json(verified), ngram_size=8)
        if len(remaining_overlaps) > 2:
            raise ValueError(f"Copyright gate rejected after auto-heal: {remaining_overlaps[:3]}")

    print(f"   🛡️ Copyright & Natural Short-Story Audit Passed (overlaps: {len(remaining_overlaps)})")
    return verified


def translate_article_to_language(verified_en, lang_name):
    """બોડીનો અનુવાદ જે-તે ભારતીય ભાષામાં કરશે, પણ બધા આંકડા (0-9) અને શેર/કંપનીના નામ 100% અંગ્રેજીમાં જ રાખશે."""
    keep_names = verified_en.get("stock_and_company_names", [])
    payload_to_translate = {
        "quick_table": verified_en.get("quick_table", []),
        "intro": verified_en["intro"],
        "key_highlights": verified_en["key_highlights"],
        "sections": verified_en["sections"],
        "faqs": verified_en["faqs"],
        "conclusion": verified_en["conclusion"]
    }
    prompt = f"""
Translate this financial SHORT STORY JSON into natural, fluent, contemporary {lang_name} used by modern Indian stock market readers.

STRICT ENGLISH-PRESERVATION RULES (MUST FOLLOW 100%):
1. NUMBERS MUST STAY IN ENGLISH DIGITS (0-9): Write EVERY single number, price, percentage, date, year, and figure strictly using standard English Arabic numerals (0, 1, 2, 3, 4, 5, 6, 7, 8, 9) — e.g., ₹1,250, 15.4%, 2026, Q2, FY27, $280 million. NEVER use native script digits (no ૦-૯, ०-९, ০-৯, etc.).
2. COMPANY, STOCK & SHARE NAMES MUST STAY IN ENGLISH SCRIPT: Keep ALL company names, stock/share names, brokerage names, indices, tickers, and financial institutions strictly in English Latin letters (e.g., Tata Motors, HDFC Bank, Reliance Industries, Nifty 50, Sensex, BSE, NSE, RBI, SEBI, IPO, EBITDA, EPS, PAT, MCX, {keep_names}). NEVER transliterate company or share names into {lang_name} script.
3. NATURAL CONTEMPORARY {lang_name}: Translate all surrounding sentences, explanations, headings, table labels, highlights, FAQs, and conclusion into warm, crisp, natural journalistic {lang_name}. Never skip or shorten any paragraph.
4. Preserve the exact JSON keys and structure.

INPUT JSON:
{json.dumps(payload_to_translate, ensure_ascii=False)}

Return ONLY valid JSON.
"""
    translated = call_gemini(prompt, expect_json=True, max_tokens=3072, temperature=0.32)
    if not translated.get("sections") or len(translated["sections"]) < len(verified_en["sections"]):
        raise ValueError(f"{lang_name} translation incomplete.")

    # Python લેવલ પર પણ ખાતરી કરી લો કે એક પણ ભારતીય આંકડો ન રહી જાય
    return enforce_english_digits(translated)


# ==========================================
# 7. PRO TV-NEWS STUDIO THUMBNAIL ENGINE (ENGLISH)
# ==========================================
def load_author_photo():
    if AUTHOR_IMAGE_URL:
        try:
            r = requests.get(AUTHOR_IMAGE_URL, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGBA")
        except Exception as e:
            print(f"[Author URL Error] {e}")

    for fname in AUTHOR_IMAGE_CANDIDATES:
        if os.path.exists(fname):
            print(f"   ✅ Found author photo: {fname}")
            return Image.open(fname).convert("RGBA")

    for fname in sorted(os.listdir(".")):
        if fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and not fname.startswith("thumb_"):
            print(f"   ✅ Auto-detected author photo: {fname}")
            return Image.open(fname).convert("RGBA")

    print("   ⚠️ author.png ફોટો રેપોમાં મળ્યો નથી!")
    return None


def remove_white_bg_clean(img_rgba, threshold=236):
    datas = img_rgba.get_flattened_data() if hasattr(img_rgba, "get_flattened_data") else img_rgba.getdata()
    new_data = []
    for r, g, b, a in datas:
        if a < 10:
            new_data.append((255, 255, 255, 0))
        elif r >= threshold and g >= threshold and b >= threshold:
            new_data.append((255, 255, 255, 0))
        elif r >= threshold - 22 and g >= threshold - 22 and b >= threshold - 22:
            avg = (r + g + b) // 3
            alpha = max(0, min(255, int((threshold - avg) * 12)))
            new_data.append((r, g, b, alpha))
        else:
            new_data.append((r, g, b, a))
    img_rgba.putdata(new_data)
    return img_rgba


def get_font(size, bold=True):
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "arialbd.ttf",
        "arial.ttf"
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_stock_chart_bg(canvas, th, seed_str):
    W, H = canvas.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    for x in range(0, W, 60):
        odraw.line([(x, 0), (x, H)], fill=(255, 255, 255, 14), width=1)
    for y in range(0, H, 60):
        odraw.line([(0, y), (W, y)], fill=(255, 255, 255, 14), width=1)

    rng = random.Random(seed_str)
    points = []
    base_y = 540
    for i in range(18):
        cx = 40 + i * 44
        delta = rng.randint(-38, 22)
        base_y = max(220, min(565, base_y + delta))
        body_h = rng.randint(24, 72)
        is_green = rng.random() > 0.35
        color = (34, 197, 94, 48) if is_green else (239, 68, 68, 42)
        wick_top = max(160, base_y - body_h // 2 - rng.randint(15, 35))
        wick_bot = min(610, base_y + body_h // 2 + rng.randint(15, 35))
        odraw.line([(cx, wick_top), (cx, wick_bot)], fill=color, width=3)
        odraw.rounded_rectangle(
            [cx - 9, base_y - body_h // 2, cx + 9, base_y + body_h // 2],
            radius=3, fill=color
        )
        points.append((cx, base_y - 20))

    if len(points) >= 2:
        trend_glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        tg_draw = ImageDraw.Draw(trend_glow)
        tg_draw.line(points, fill=(*th["chart"], 130), width=7)
        trend_glow = trend_glow.filter(ImageFilter.GaussianBlur(radius=6))
        overlay = Image.alpha_composite(overlay, trend_glow)
        odraw = ImageDraw.Draw(overlay)
        odraw.line(points, fill=(*th["chart"], 190), width=3)

    return Image.alpha_composite(canvas, overlay)


def create_custom_thumbnail(headline, badge_text, slug_id, theme_index=0):
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    W, H = 1200, 675
    th = THUMB_THEMES[theme_index % len(THUMB_THEMES)]

    canvas = Image.new("RGBA", (W, H), (*th["bg1"], 255))
    draw = ImageDraw.Draw(canvas)
    for y in range(H):
        t = y / H
        r = int(th["bg1"][0] + (th["bg2"][0] - th["bg1"][0]) * t)
        g = int(th["bg1"][1] + (th["bg2"][1] - th["bg1"][1]) * t)
        b = int(th["bg1"][2] + (th["bg2"][2] - th["bg1"][2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    canvas = draw_stock_chart_bg(canvas, th, slug_id)

    light_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(light_layer)
    ldraw.ellipse([660, 20, 1260, 640], fill=(*th["glow"], 95))
    ldraw.ellipse([760, 110, 1180, 540], fill=(*th["accent"], 75))
    ldraw.ellipse([-120, -80, 740, 700], fill=(4, 10, 22, 130))
    light_layer = light_layer.filter(ImageFilter.GaussianBlur(radius=45))
    canvas = Image.alpha_composite(canvas, light_layer)

    author = load_author_photo()
    if author is not None:
        try:
            w_a, h_a = author.size
            corners = [
                author.getpixel((2, 2)),
                author.getpixel((w_a - 3, 2)),
                author.getpixel((2, h_a - 3)),
                author.getpixel((w_a - 3, h_a - 3)),
            ]
            white_corners = sum(1 for r, g, b, a in corners if a > 220 and r > 230 and g > 230 and b > 230)
            if white_corners >= 2:
                author = remove_white_bg_clean(author)

            bbox = author.getbbox()
            if bbox:
                author = author.crop(bbox)

            rgb_part = author.convert("RGB")
            rgb_part = ImageEnhance.Contrast(rgb_part).enhance(1.08)
            rgb_part = ImageEnhance.Color(rgb_part).enhance(1.08)
            rgb_part = ImageEnhance.Sharpness(rgb_part).enhance(1.18)
            author_enhanced = rgb_part.convert("RGBA")
            author_enhanced.putalpha(author.getchannel("A"))
            author = author_enhanced

            target_h = 585
            ratio = target_h / author.height
            target_w = int(author.width * ratio)
            if target_w > 530:
                scale = 530 / target_w
                target_w = 530
                target_h = int(target_h * scale)

            author = author.resize((target_w, target_h), Image.Resampling.LANCZOS)
            paste_x = W - target_w - 12
            paste_y = H - target_h - 40

            rim_canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rim_fill = Image.new("RGBA", author.size, (*th["accent"], 210))
            rim_fill.putalpha(author.getchannel("A"))
            rim_canvas.paste(rim_fill, (paste_x - 6, paste_y - 4))
            rim_canvas = rim_canvas.filter(ImageFilter.GaussianBlur(radius=18))
            canvas = Image.alpha_composite(canvas, rim_canvas)

            canvas.paste(author, (paste_x, paste_y), author)
        except Exception as e:
            print(f"[Thumbnail Photo Error] {e}")

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, W, 9], fill=(*th["accent"], 255))

    # થમ્બનેલ પર માત્ર અંગ્રેજી બેજ અને અંગ્રેજી હેડલાઈન જ આવશે
    badge = re.sub(r"[^A-Za-z0-9\s\-&]", "", str(badge_text or "MARKET SHORT")).strip().upper()[:24] or "MARKET SHORT"
    badge_font = get_font(25, bold=True)
    tb = draw.textbbox((0, 0), badge, font=badge_font)
    badge_w = (tb[2] - tb[0]) + 72
    draw.rounded_rectangle([52, 46, 52 + badge_w, 98], radius=10, fill=(*th["badge"], 255))
    draw.ellipse([72, 64, 88, 80], fill=(255, 255, 255, 255))
    draw.text((100, 56), badge, font=badge_font, fill=(255, 255, 255, 255))

    clean_hl = " ".join(re.sub(r"[^\x20-\x7E₹]", "", str(headline)).split()).upper()
    title_font = get_font(56, bold=True)
    wrapped = textwrap.wrap(clean_hl, width=18)[:4]
    if len(wrapped) <= 2:
        title_font = get_font(62, bold=True)
        wrapped = textwrap.wrap(clean_hl, width=16)[:3]

    y_pos = 132
    line_step = 86 if len(wrapped) <= 3 else 78

    for idx, line in enumerate(wrapped):
        bbox_l = draw.textbbox((0, 0), line, font=title_font)
        lw = bbox_l[2] - bbox_l[0]
        lh = bbox_l[3] - bbox_l[1]

        if idx == 0:
            draw.rounded_rectangle(
                [46, y_pos - 6, 46 + lw + 32, y_pos + lh + 18],
                radius=10, fill=(*th["accent"], 255)
            )
            draw.text((62, y_pos), line, font=title_font, fill=(*th["accent_text"], 255))
            y_pos += line_step + 12
        else:
            draw.text((57, y_pos + 4), line, font=title_font, fill=(0, 0, 0, 220))
            draw.text(
                (52, y_pos), line, font=title_font,
                fill=(255, 255, 255, 255),
                stroke_width=2, stroke_fill=(0, 0, 0, 230)
            )
            y_pos += line_step

    bar_y = H - 68
    draw.rectangle([0, bar_y, W, H], fill=(10, 15, 28, 245))
    draw.rectangle([0, bar_y, W, bar_y + 4], fill=(*th["accent"], 255))

    brand_font = get_font(26, bold=True)
    meta_font = get_font(23, bold=False)
    draw.rounded_rectangle([42, bar_y + 13, 305, H - 11], radius=7, fill=(*th["accent"], 255))
    draw.text((60, bar_y + 19), "MARKETMUNAFA", font=brand_font, fill=(15, 23, 42, 255))

    date_str = get_ist_now().strftime("%d %b %Y").upper()
    draw.text(
        (328, bar_y + 21),
        f"STOCK MARKET & BUSINESS SHORT   •   {date_str}",
        font=meta_font, fill=(226, 232, 240, 255)
    )

    filename = f"thumb_{slug_id}.jpg"
    filepath = os.path.join(THUMBNAIL_DIR, filename)
    canvas.convert("RGB").save(filepath, "JPEG", quality=92, optimize=True)
    print(f"   🎨 Thumbnail created: {filepath}")

    if GITHUB_REPO:
        try:
            subprocess.run(["git", "config", "user.name", "MarketMunafa Bot"], check=False)
            subprocess.run(["git", "config", "user.email", "bot@marketmunafa.local"], check=False)
            subprocess.run(["git", "add", filepath], check=True)
            subprocess.run(["git", "commit", "-m", f"Add thumbnail {filename}"], check=False)
            subprocess.run(["git", "push"], capture_output=True, text=True)
            time.sleep(2)
        except Exception as e:
            print(f"[Git Push Thumb Warning] {e}")
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{THUMBNAIL_DIR}/{filename}"
    return None


# ==========================================
# 8. GUJARATI-FIRST HTML + ENGLISH TITLE/NUMBERS/TAGS
# ==========================================
def build_language_block(lang_data, lang_code, is_default=False):
    display_style = "block" if is_default else "none"

    hl_heading = "⚡ મુખ્ય મુદ્દાઓ (Key Highlights)" if lang_code == "gu" else "⚡ Quick Story Highlights"
    faq_heading = "❓ વારંવાર પૂછાતા પ્રશ્નો (FAQ)" if lang_code == "gu" else "❓ Quick FAQs"
    bottom_lbl = "નિષ્કર્ષ:" if lang_code == "gu" else "Bottom Line:"

    intro_html = "".join(
        f"<p style='line-height:1.85; margin-bottom:14px;'>{p.strip()}</p>"
        for p in lang_data.get("intro", "").split("\n") if p.strip()
    )

    qtable_rows = ""
    for row in lang_data.get("quick_table", [])[:4]:
        if row.get("label") and row.get("value"):
            qtable_rows += f"""
            <tr>
              <td style="padding:10px 12px; border:1px solid #cbd5e1; font-weight:700; background:#f8fafc; width:38%; color:#0f172a;">{row['label']}</td>
              <td style="padding:10px 12px; border:1px solid #cbd5e1; color:#1e293b;">{row['value']}</td>
            </tr>
            """
    qtable_html = ""
    if qtable_rows:
        qtable_html = f"""
        <div style="overflow-x:auto; margin:16px 0 20px 0;">
          <table style="width:100%; border-collapse:collapse; font-size:15px;">
            <tbody>{qtable_rows}</tbody>
          </table>
        </div>
        """

    highlights_html = "".join(
        f"<li style='margin-bottom:7px;'>{item}</li>"
        for item in lang_data.get("key_highlights", [])
    )

    sections_html = ""
    for sec in lang_data.get("sections", []):
        body_ps = "".join(
            f"<p style='line-height:1.85; margin-bottom:13px;'>{p.strip()}</p>"
            for p in sec.get("body", "").split("\n") if p.strip()
        )
        sections_html += f"""
        <h2 style="color:#0f172a; font-size:20px; margin-top:24px; margin-bottom:10px; border-left:4px solid #1d4ed8; padding-left:10px;">
            {sec.get('heading', '')}
        </h2>
        {body_ps}
        """

    faqs_html = ""
    for faq in lang_data.get("faqs", []):
        faqs_html += f"""
        <div style="margin-bottom:11px; padding:12px 14px; background:#f8fafc; border-radius:8px; border:1px solid #e2e8f0;">
            <h3 style="margin:0 0 5px 0; font-size:15.5px; font-weight:700; color:#0f172a;">Q: {faq.get('q', '')}</h3>
            <p style="margin:0; color:#334155; line-height:1.68;">{faq.get('a', '')}</p>
        </div>
        """

    return f"""
    <div class="mm-lang-pane" data-lang="{lang_code}" style="display:{display_style};">
        <div style="font-size:16px; color:#1e293b;">{intro_html}</div>

        {qtable_html}

        <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:15px 17px; margin:18px 0;">
            <h2 style="margin-top:0; font-size:18px; color:#1e3a8a;">{hl_heading}</h2>
            <ul style="margin:0; padding-left:20px; line-height:1.72; color:#1e293b;">
                {highlights_html}
            </ul>
        </div>

        {sections_html}

        <div style="margin-top:24px;">
            <h2 style="color:#0f172a; font-size:19px; margin-bottom:11px;">{faq_heading}</h2>
            {faqs_html}
        </div>

        <div style="margin-top:20px; padding:14px 16px; background:#f0fdf4; border-left:4px solid #16a34a; border-radius:8px;">
            <p style="margin:0; line-height:1.72; color:#14532d;"><strong>{bottom_lbl}</strong> {lang_data.get('conclusion', '')}</p>
        </div>
    </div>
    """


def build_complete_post_html(verified_en, translations, thumb_url, related_posts):
    now_ist = get_ist_now()
    ist_str = now_ist.strftime("%B %d, %Y | %I:%M %p IST")
    iso_str = now_ist.isoformat()
    uid = hashlib.md5(verified_en["seo_title"].encode("utf-8")).hexdigest()[:8]

    news_schema = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": verified_en["seo_title"],
        "description": verified_en.get("meta_description", verified_en["seo_title"]),
        "image": [thumb_url] if thumb_url else [],
        "datePublished": iso_str,
        "dateModified": iso_str,
        "author": {"@type": "Organization", "name": "MarketMunafa Editorial Desk", "url": BLOG_BASE_URL},
        "publisher": {"@type": "Organization", "name": "MarketMunafa", "url": BLOG_BASE_URL}
    }
    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f.get("q", ""),
                "acceptedAnswer": {"@type": "Answer", "text": f.get("a", "")}
            }
            for f in verified_en.get("faqs", [])
        ]
    }

    # ડિફોલ્ટ પહેલા ગુજરાતી ખુલશે, પછી વાચક બટન દબાવી ભાષા બદલી શકશે
    default_lang = "gu" if "gu" in translations else "en"

    lang_buttons = []
    if "gu" in translations:
        gu_cls = "mm-lbtn active" if default_lang == "gu" else "mm-lbtn"
        lang_buttons.append(
            f"<button type='button' class='{gu_cls}' onclick=\"mmSwitchPostLang('{uid}','gu',this)\">ગુજરાતી</button>"
        )

    en_cls = "mm-lbtn active" if default_lang == "en" else "mm-lbtn"
    lang_buttons.append(
        f"<button type='button' class='{en_cls}' onclick=\"mmSwitchPostLang('{uid}','en',this)\">English</button>"
    )

    for code, native_label, _ in TARGET_LANGUAGES:
        if code == "gu":
            continue
        if code in translations:
            lang_buttons.append(
                f"<button type='button' class='mm-lbtn' onclick=\"mmSwitchPostLang('{uid}','{code}',this)\">{native_label}</button>"
            )

    panes_html = ""
    if "gu" in translations:
        panes_html += build_language_block(translations["gu"], "gu", is_default=(default_lang == "gu"))
    panes_html += build_language_block(verified_en, "en", is_default=(default_lang == "en"))
    for code, _, _ in TARGET_LANGUAGES:
        if code == "gu":
            continue
        if code in translations:
            panes_html += build_language_block(translations[code], code, is_default=False)

    thumb_block = ""
    if thumb_url:
        thumb_block = f"""
        <figure style="margin:0 0 18px 0; text-align:center;">
            <img src="{thumb_url}" alt="{verified_en['seo_title']}" title="{verified_en['seo_title']}" width="1200" height="675"
                 style="width:100%; height:auto; border-radius:12px; box-shadow:0 6px 18px rgba(0,0,0,0.14);" />
        </figure>
        """

    related_html = ""
    if related_posts:
        links_li = "".join(
            f"<li style='margin-bottom:7px;'><a href='{rp['url']}' style='color:#1d4ed8; font-weight:600; text-decoration:none;'>{rp['title']}</a></li>"
            for rp in related_posts
        )
        related_html = f"""
        <div style="margin-top:24px; padding:15px 17px; background:#f8fafc; border:1px solid #cbd5e1; border-radius:10px;">
            <h3 style="margin:0 0 9px 0; font-size:16.5px; color:#0f172a;">🔗 Also Read on MarketMunafa</h3>
            <ul style="margin:0; padding-left:20px; line-height:1.68;">{links_li}</ul>
        </div>
        """

    html = f"""
<article id="mm-post-{uid}" style="font-family:'Noto Sans Gujarati','Noto Sans Devanagari','Segoe UI',Roboto,Arial,sans-serif; max-width:860px; margin:0 auto;">
  <style>
    #mm-post-{uid} .mm-langbar {{
      display:flex; flex-wrap:wrap; gap:7px; align-items:center;
      background:#f1f5f9; padding:11px 12px; border-radius:10px; margin:14px 0 18px 0;
      border:1px solid #cbd5e1;
    }}
    #mm-post-{uid} .mm-lbtn {{
      border:1px solid #1d4ed8; background:#fff; color:#1d4ed8;
      padding:6px 13px; border-radius:20px; font-size:13.5px; font-weight:600;
      cursor:pointer; transition:0.2s;
    }}
    #mm-post-{uid} .mm-lbtn.active, #mm-post-{uid} .mm-lbtn:hover {{
      background:#1d4ed8; color:#fff;
    }}
  </style>

  {thumb_block}

  <p style="font-size:13.5px; color:#64748b; margin-bottom:12px;">
    📅 Published on: {ist_str} | By <strong>MarketMunafa Editorial Desk</strong> • 2 min read
  </p>

  <div class="mm-langbar">
    <span style="font-weight:700; font-size:14px; color:#0f172a; margin-right:4px;">🌐 ભાષા બદલો / Read in:</span>
    {"".join(lang_buttons)}
  </div>

  {panes_html}

  {related_html}

  <hr style="margin:24px 0 12px 0; border:none; border-top:1px solid #e2e8f0;" />
  <p style="font-size:12.5px; color:#64748b; line-height:1.6;">
    <strong>Disclaimer:</strong> This short report is prepared by the MarketMunafa Editorial Desk for informational and educational purposes only and does not constitute investment advice. Stock market investments are subject to market risks; please consult a SEBI-registered advisor before investing.
  </p>

  <script type="application/ld+json">{json.dumps(news_schema, ensure_ascii=False)}</script>
  <script type="application/ld+json">{json.dumps(faq_schema, ensure_ascii=False)}</script>
  <script>
    function mmSwitchPostLang(uid, code, btn) {{
      var root = document.getElementById('mm-post-' + uid);
      if (!root) return;
      root.querySelectorAll('.mm-lang-pane').forEach(function(p) {{
        p.style.display = (p.getAttribute('data-lang') === code) ? 'block' : 'none';
      }});
      root.querySelectorAll('.mm-lbtn').forEach(function(b) {{
        b.classList.remove('active');
      }});
      if (btn) btn.classList.add('active');
    }}
  </script>
</article>
"""
    return html.strip()


def publish_to_blogger(access_token, title, html_content, labels):
    # બ્લોગર ટાઇટલ અને ટૅગ્સ 100% અંગ્રેજીમાં જ રહેશે
    clean_title = " ".join(re.sub(r"[^\x20-\x7E₹]", "", str(title)).split())
    clean_labels = []
    total_chars = 0
    for lbl in labels:
        lbl = re.sub(r"[^A-Za-z0-9\s\-&]", "", str(lbl)).strip()[:28]
        if lbl and lbl not in clean_labels and (total_chars + len(lbl) + 1) <= 185:
            clean_labels.append(lbl)
            total_chars += len(lbl) + 1
    if "MARKET NEWS" not in clean_labels:
        clean_labels.insert(0, "MARKET NEWS")

    api_url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#post",
        "title": clean_title,
        "content": html_content,
        "labels": clean_labels[:12]
    }
    res = requests.post(api_url, headers=headers, json=payload, timeout=25)
    if res.status_code in (200, 201):
        post_url = res.json().get("url", "")
        print(f"   ✅ Published: {clean_title} -> {post_url}")
        return post_url
    else:
        print(f"   ❌ Blogger API Error ({res.status_code}): {res.text}")
        return None


# ==========================================
# 9. MAIN EXECUTION LOOP (1 WEBSITE = 1 SHORT STORY)
# ==========================================
def main():
    history = load_history()
    now_ist = get_ist_now()
    today_key = now_ist.strftime("%Y-%m-%d")
    posted_today = history["daily_counts"].get(today_key, 0)

    if posted_today >= DAILY_MAX_POSTS:
        print(f"ℹ️ આજે ({today_key}) પહેલેથી જ {posted_today} પોસ્ટ થઈ ગઈ છે (Daily limit: {DAILY_MAX_POSTS}).")
        return

    access_token = get_blogger_access_token()
    recent_blog_posts = fetch_recent_blogger_posts(access_token, limit=50)
    live_blog_titles = [p["title"] for p in recent_blog_posts]

    remaining_today = min(MAX_POSTS_PER_RUN, DAILY_MAX_POSTS - posted_today)
    candidates = fetch_fresh_diverse_articles(history, live_blog_titles)
    if not candidates:
        print(f"ℹ️ આજે ({today_key}) કોઈ નવા અલગ સમાચાર મળ્યા નથી.")
        return

    published_count = 0
    used_sources_this_run = set()

    for art in candidates:
        if published_count >= remaining_today:
            break

        if art["source"] in used_sources_this_run:
            continue

        pub_time_str = art["pub_dt"].strftime("%I:%M %p IST")
        print(f"\n🔍 Today's Live News [{art['source']} | {pub_time_str}]: {art['title']}")
        try:
            print("   📋 Pass 1: Extracting verified facts...")
            facts_json = pass1_extract_pure_facts(art)
            time.sleep(2)

            print("   ✍️ Pass 2: Writing natural SEO short story...")
            draft_en = pass2_write_natural_short_story(facts_json)
            time.sleep(2)

            print("   🔍 Pass 3: Copyright audit & auto-heal...")
            verified_en = pass3_copyright_humanizer_with_autoheal(draft_en, art)
            time.sleep(2)

            translations = {}
            all_langs_ready = True
            for code, native_name, eng_name in TARGET_LANGUAGES:
                print(f"   🌐 Translating to {eng_name} (English numbers & stock names preserved)...")
                try:
                    translations[code] = translate_article_to_language(verified_en, eng_name)
                    time.sleep(2)
                except Exception as te:
                    print(f"   ⚠️ Retry {eng_name}: {te}")
                    time.sleep(4)
                    try:
                        translations[code] = translate_article_to_language(verified_en, eng_name)
                    except Exception as te2:
                        print(f"   ❌ Failed {eng_name}: {te2}")
                        all_langs_ready = False
                        break

            if not all_langs_ready:
                print("   ⏭️ બધી ભાષા પૂરી ન બની હોવાથી આ પોસ્ટ સ્કિપ કરી.")
                continue

            slug_id = hashlib.md5(art["url"].encode("utf-8")).hexdigest()[:10]
            thumb_url = create_custom_thumbnail(
                verified_en.get("thumbnail_headline", verified_en["seo_title"][:50]),
                verified_en.get("category_badge", "MARKET SHORT"),
                slug_id,
                theme_index=posted_today
            )

            tags = verified_en.get("tags", ["Stock Market", "Business News"])
            related_posts = pick_related_internal_links(recent_blog_posts, tags, max_links=3)
            post_html = build_complete_post_html(verified_en, translations, thumb_url, related_posts)

            published_url = publish_to_blogger(
                access_token=access_token,
                title=verified_en["seo_title"],
                html_content=post_html,
                labels=tags
            )

            if published_url:
                published_count += 1
                posted_today += 1
                used_sources_this_run.add(art["source"])
                history["posted_urls"].append(art["url"])
                history["posted_titles"].append(art["title"])
                history["posted_titles"].append(verified_en["seo_title"])
                history["daily_counts"][today_key] = posted_today
                recent_blog_posts.insert(0, {"title": verified_en["seo_title"], "url": published_url, "labels": tags})
                save_history(history)
                time.sleep(5)

        except Exception as e:
            print(f"   ❌ Skipped article: {e}")
            continue

    print(f"\n🎉 Run finished! આજે ({today_key}) કુલ {published_count} અલગ-અલગ વેબસાઈટની નવી Short Story પબ્લિશ થઈ (આજની કુલ: {posted_today}).")


if __name__ == "__main__":
    main()
