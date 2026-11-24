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
CLIENT_ID = os.environ.get("CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "").strip()
BLOG_ID = os.environ.get("BLOG_ID", "6996150531679127159").strip()

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
MAX_ARTICLE_AGE_HOURS = int(os.environ.get("MAX_ARTICLE_AGE_HOURS", "36"))

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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9"
}

NEWS_FEEDS = [
    ("Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("LiveMint", "https://www.livemint.com/rss/markets"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("NDTV Business", "https://feeds.feedburner.com/ndtvprofit-latest"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Zee Business", "https://www.zeebiz.com/india-markets.xml"),
    ("NYTimes Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Moneycontrol Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
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

PAYWALL_HINTS = [
    "subscribe to continue", "et prime", "already a subscriber",
    "unlock this article", "sign in to read", "member-only story",
    "for unlimited access", "create a free account to continue"
]

# દરેક ન્યૂઝ માટે અલગ-અલગ પ્રીમિયમ TV-ન્યૂઝ સ્ટુડિયો થીમ
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
# 2. HELPER UTILITIES & HISTORY
# ==========================================
def get_ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


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
    history["posted_urls"] = history["posted_urls"][-1500:]
    history["posted_titles"] = history["posted_titles"][-500:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def clean_html_text(raw):
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def normalize_title(title):
    return re.sub(r"[^a-z0-9\s]", "", title.lower()).strip()


def is_similar_topic(new_title, existing_titles):
    n1 = normalize_title(new_title)
    w1 = {w for w in n1.split() if len(w) > 3}
    for old_t in existing_titles:
        n2 = normalize_title(old_t)
        if not n2:
            continue
        if difflib.SequenceMatcher(None, n1, n2).ratio() >= 0.62:
            return True
        w2 = {w for w in n2.split() if len(w) > 3}
        if w1 and w2:
            overlap = len(w1 & w2) / max(1, min(len(w1), len(w2)))
            if overlap >= 0.65:
                return True
    return False


def is_fresh_article(pub_date_str, title):
    if re.search(r"('\s?2[0-5]\b|\b202[0-4]\b)", title):
        return False
    if not pub_date_str:
        return False
    try:
        dt = parsedate_to_datetime(pub_date_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        age_hours = (now_utc - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
        return -2.0 <= age_hours <= MAX_ARTICLE_AGE_HOURS
    except Exception:
        return False


def find_copied_phrases(source_text, draft_text, ngram_size=6):
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
        gram = " ".join(drf_words[i:i + ngram_size])
        if gram in src_ngrams and gram not in matches:
            matches.append(gram)
            if len(matches) >= 8:
                break
    return matches


# ==========================================
# 3. FRESH & DIVERSE NEWS FETCHING
# ==========================================
def extract_full_public_article(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.content, "html.parser")
        for tag in soup.find_all(["script", "style", "nav", "footer", "aside", "form", "noscript", "iframe"]):
            tag.decompose()

        body_text_low = soup.get_text(" ", strip=True).lower()
        container = (
            soup.find("article")
            or soup.find("div", class_=re.compile(r"(arttext|article-body|story-content|content_wrapper|main-content|story_details)", re.I))
            or soup
        )

        paragraphs = []
        for p in container.find_all("p"):
            txt = " ".join(p.get_text(" ", strip=True).split())
            low = txt.lower()
            if len(txt) < 45:
                continue
            if any(h in low for h in ["also read", "read more", "download app", "follow us on", "whatsapp channel", "copyright", "all rights reserved"]):
                continue
            if txt not in paragraphs:
                paragraphs.append(txt)

        full_text = "\n\n".join(paragraphs)
        if len(full_text) < 450:
            return None
        if any(p_hint in body_text_low for p_hint in PAYWALL_HINTS) and len(full_text) < 900:
            return None
        return full_text[:6500]
    except Exception:
        return None


def fetch_fresh_diverse_articles(history):
    posted_urls = set(history["posted_urls"])
    known_titles = list(history["posted_titles"])
    per_source_buckets = []

    for source_name, feed_url in NEWS_FEEDS:
        bucket = []
        try:
            res = requests.get(feed_url, headers=HEADERS, timeout=12)
            if res.status_code != 200:
                continue
            root = ET.fromstring(res.content)
            items = root.findall(".//item")[:10]

            for item in items:
                title = clean_html_text(item.findtext("title"))
                link = (item.findtext("link") or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()

                if not title or not link or link in posted_urls:
                    continue
                if "/prime/" in link.lower():
                    continue
                if not is_fresh_article(pub_date, title):
                    continue
                if is_similar_topic(title, known_titles):
                    continue

                full_text = extract_full_public_article(link)
                if not full_text:
                    continue

                posted_urls.add(link)
                known_titles.append(title)
                bucket.append({
                    "source": source_name,
                    "title": title,
                    "url": link,
                    "pub_date": pub_date,
                    "full_text": full_text
                })
                if len(bucket) >= 2:
                    break
        except Exception as e:
            print(f"[Feed Skip] {source_name}: {e}")
            continue

        if bucket:
            per_source_buckets.append(bucket)

    candidates = []
    max_depth = max((len(b) for b in per_source_buckets), default=0)
    for idx in range(max_depth):
        for bucket in per_source_buckets:
            if idx < len(bucket):
                candidates.append(bucket[idx])
    return candidates[: MAX_POSTS_PER_RUN * 3]


# ==========================================
# 4. GEMINI API WITH KEY ROTATION & FALLBACK
# ==========================================
def call_gemini(prompt, expect_json=True, max_tokens=4096):
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
                "temperature": 0.45,
                "maxOutputTokens": max_tokens
            }
            if expect_json:
                gen_cfg["responseMimeType"] = "application/json"

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_cfg
            }

            try:
                res = requests.post(url, headers=headers, json=payload, timeout=90)
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
                    time.sleep(3)
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
# 5. TWO-STEP ORIGINAL REWRITING & AUDIT
# ==========================================
def generate_original_english_article(article):
    today_str = get_ist_now().strftime("%B %d, %Y")
    prompt = f"""
You are a senior Indian financial journalist writing for 'MarketMunafa' on {today_str}.
Read the source facts below and write a COMPLETE, 100% ORIGINAL, human-sounding news analysis article from scratch.

STRICT RULES:
- ZERO copy-paste: Do NOT reuse sentences, phrasing, or paragraph structure from the source.
- Keep all numbers, dates, company names, and facts 100% accurate. Never invent numbers.
- Write a COMPLETE, detailed article (700-950 words), never half or cut-off.
- Optimize for Google News, Google Discover, AdSense safety, and top Google Search ranking.
- Generate 10 to 12 SEO tags (mix of broad popular tags like 'Stock Market', 'Business News' and specific company/sector tags).
- Provide a punchy 4-to-6 word thumbnail headline (for visual impact) and a 2-3 word top kicker (like 'BIG ALERT', 'IPO NEWS', 'MARKET BREAKING').

SOURCE TITLE: {article['title']}
SOURCE FACTS:
{article['full_text']}

Return ONLY valid JSON with this exact schema:
{{
  "seo_title": "Clear, high-CTR Google News headline (60-78 chars)",
  "thumbnail_headline": "Short 4-6 word punchy bold headline for thumbnail",
  "category_badge": "2-3 word category e.g. MARKET UPDATE / STOCK ALERT",
  "meta_description": "Compelling 140-155 char SEO meta description",
  "tags": ["Tag1", "Tag2", "Tag3"],
  "intro": "2 strong opening paragraphs explaining who, what, when, and why it matters.",
  "key_highlights": ["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4", "Bullet 5"],
  "sections": [
    {{"heading": "H2 Section Title 1", "body": "Detailed 2-3 paragraphs"}},
    {{"heading": "H2 Section Title 2", "body": "Detailed 2-3 paragraphs"}},
    {{"heading": "What It Means for Investors & Market Outlook", "body": "Balanced analytical view without buy/sell financial advice"}}
  ],
  "faqs": [
    {{"q": "Relevant user search question 1?", "a": "Clear concise factual answer."}},
    {{"q": "Relevant user search question 2?", "a": "Clear concise factual answer."}},
    {{"q": "Relevant user search question 3?", "a": "Clear concise factual answer."}}
  ],
  "conclusion": "Strong closing summary paragraph."
}}
"""
    return call_gemini(prompt, expect_json=True, max_tokens=4096)


def verify_and_polish_article(draft_json, article):
    draft_flat = " ".join([
        draft_json.get("seo_title", ""),
        draft_json.get("intro", ""),
        " ".join(s.get("body", "") for s in draft_json.get("sections", [])),
        draft_json.get("conclusion", "")
    ])
    overlaps = find_copied_phrases(article["full_text"], draft_flat, ngram_size=6)
    overlap_note = (
        f"IMPORTANT: These exact 6-word phrases overlapped with the source and MUST be completely rewritten: {overlaps}"
        if overlaps else "No 6-word verbatim overlaps detected; double-check sentence originality."
    )

    prompt = f"""
You are the Chief Editor and Copyright/SEO Auditor for MarketMunafa.
Perform a STRICT 2nd-pass verification on the DRAFT ARTICLE against the ORIGINAL SOURCE.

{overlap_note}

CHECKLIST:
1. 100% COPYRIGHT FREE: Ensure ZERO phrases (6+ words) match the source verbatim.
2. FACT ACCURACY: Confirm every figure, date, and company detail matches the source without exaggeration.
3. COMPLETENESS: Confirm intro, 5 key_highlights, 3 full sections, 3 FAQs, and conclusion are all complete.
4. GOOGLE NEWS & ADSENSE POLICY: Neutral authoritative tone, no guaranteed return claims, strong SEO headline, and clean tags (max 12, each under 28 chars).

ORIGINAL SOURCE EXCERPT:
{article['full_text'][:3500]}

DRAFT JSON TO AUDIT & FINALIZE:
{json.dumps(draft_json, ensure_ascii=False)}

Return ONLY the final verified JSON in the EXACT same schema as the draft, plus `"audit_passed": true`.
"""
    verified = call_gemini(prompt, expect_json=True, max_tokens=4096)
    if not verified.get("seo_title") or not verified.get("sections") or len(verified.get("sections", [])) < 2:
        raise ValueError("Verified article incomplete.")
    return verified


def translate_article_to_language(verified_en, lang_name):
    payload_to_translate = {
        "seo_title": verified_en["seo_title"],
        "intro": verified_en["intro"],
        "key_highlights": verified_en["key_highlights"],
        "sections": verified_en["sections"],
        "faqs": verified_en["faqs"],
        "conclusion": verified_en["conclusion"]
    }
    prompt = f"""
Translate the following COMPLETE financial news article JSON into natural, fluent, contemporary {lang_name} used by modern Indian news readers.

RULES:
- Translate EVERY field completely (seo_title, intro, all key_highlights, all sections, all faqs, conclusion). Never shorten or skip any paragraph.
- Keep company names, ₹ amounts, and common market terms (IPO, RBI, SEBI, Nifty, Sensex, EBITDA, Q2) natural and easy to read.
- Preserve the exact JSON keys and structure.

INPUT JSON:
{json.dumps(payload_to_translate, ensure_ascii=False)}

Return ONLY valid JSON.
"""
    translated = call_gemini(prompt, expect_json=True, max_tokens=4096)
    if not translated.get("sections") or len(translated["sections"]) < len(verified_en["sections"]):
        raise ValueError(f"{lang_name} translation incomplete.")
    return translated


# ==========================================
# 6. PRO TV-NEWS STUDIO THUMBNAIL ENGINE
# ==========================================
def load_author_photo():
    """રેપોમાં રહેલો તમારો કોઈપણ ફોટો (PNG/JPG) અથવા AUTHOR_IMAGE_URL આપમેળે લાવે છે."""
    if AUTHOR_IMAGE_URL:
        try:
            r = requests.get(AUTHOR_IMAGE_URL, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                print("✅ Loaded author photo from AUTHOR_IMAGE_URL")
                return Image.open(io.BytesIO(r.content)).convert("RGBA")
        except Exception as e:
            print(f"[Author URL Error] {e}")

    # 1) પહેલા જાણીતા નામો ચેક કરો
    for fname in AUTHOR_IMAGE_CANDIDATES:
        if os.path.exists(fname):
            print(f"✅ Found author photo: {fname}")
            return Image.open(fname).convert("RGBA")

    # 2) ન મળે તો મેઈન ફોલ્ડરમાં રહેલી કોઈપણ PNG/JPG ફાઈલ આપમેળે પસંદ કરો
    for fname in sorted(os.listdir(".")):
        if fname.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and not fname.startswith("thumb_"):
            print(f"✅ Auto-detected author photo in repo: {fname}")
            return Image.open(fname).convert("RGBA")

    print("⚠️ ચેતવણી: GitHub રેપોમાં તમારો ફોટો (જેમ કે author.png કે 1001185905_2.png) મળ્યો નથી!")
    return None


def remove_white_bg_clean(img_rgba, threshold=236):
    datas = img_rgba.getdata()
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
    """બેકગ્રાઉન્ડમાં પ્રોફેશનલ સ્ટોક માર્કેટ ગ્રિડ, કેન્ડલસ્ટિક્સ અને ગ્લોઈંગ ટ્રેન્ડ લાઈન બનાવે છે."""
    W, H = canvas.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)

    # 1) સૂક્ષ્મ ફાઇનાન્શિયલ ગ્રિડ લાઈન્સ
    for x in range(0, W, 60):
        odraw.line([(x, 0), (x, H)], fill=(255, 255, 255, 14), width=1)
    for y in range(0, H, 60):
        odraw.line([(0, y), (W, y)], fill=(255, 255, 255, 14), width=1)

    # 2) કેન્ડલસ્ટિક બાર્સ (ડાબેથી જમણે ચઢતો ટ્રેન્ડ)
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

    # 3) ગ્લોઈંગ ટ્રેન્ડ લાઈન
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
    """1200x675 પ્રોફેશનલ સ્ટુડિયો-ક્વોલિટી થમ્બનેલ બનાવી GitHub પર તરત લાઈવ કરે છે."""
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    W, H = 1200, 675
    th = THUMB_THEMES[theme_index % len(THUMB_THEMES)]

    # 1) ડાયગોનલ રિચ ગ્રેડિયન્ટ બેકગ્રાઉન્ડ
    canvas = Image.new("RGBA", (W, H), (*th["bg1"], 255))
    draw = ImageDraw.Draw(canvas)
    for y in range(H):
        t = y / H
        r = int(th["bg1"][0] + (th["bg2"][0] - th["bg1"][0]) * t)
        g = int(th["bg1"][1] + (th["bg2"][1] - th["bg1"][1]) * t)
        b = int(th["bg1"][2] + (th["bg2"][2] - th["bg1"][2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # 2) સ્ટોક માર્કેટ કેન્ડલસ્ટિક અને ગ્રિડ ઓવરલે
    canvas = draw_stock_chart_bg(canvas, th, slug_id)

    # 3) ડાબી બાજુ ડાર્ક રીડેબિલિટી વિગ્નેટ + જમણી બાજુ સ્ટુડિયો સ્પોટલાઈટ ગ્લો
    light_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(light_layer)
    ldraw.ellipse([660, 20, 1260, 640], fill=(*th["glow"], 95))
    ldraw.ellipse([760, 110, 1180, 540], fill=(*th["accent"], 75))
    ldraw.ellipse([-120, -80, 740, 700], fill=(4, 10, 22, 130))
    light_layer = light_layer.filter(ImageFilter.GaussianBlur(radius=45))
    canvas = Image.alpha_composite(canvas, light_layer)

    # 4) તમારો ફોટો + સ્ટુડિયો રિમ-લાઈટ ગ્લો (જમણી બાજુ)
    author = load_author_photo()
    if author is not None:
        try:
            # જો બેકગ્રાઉન્ડ સફેદ હોય તો સાફ કરો (ટ્રાન્સપરન્ટ PNG હોય તો સીધો રહેશે)
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

            # ચહેરો વધુ સ્પષ્ટ અને સ્ટુડિયો-લિટ દેખાય તે માટે હળવું એન્હાન્સમેન્ટ
            rgb_part = author.convert("RGB")
            rgb_part = ImageEnhance.Contrast(rgb_part).enhance(1.08)
            rgb_part = ImageEnhance.Color(rgb_part).enhance(1.08)
            rgb_part = ImageEnhance.Sharpness(rgb_part).enhance(1.18)
            author_enhanced = rgb_part.convert("RGBA")
            author_enhanced.putalpha(author.getchannel("A"))
            author = author_enhanced

            # સાઈઝ સેટ કરો (નીચેના ટિકર બાર પાછળ સ્મૂધ રીતે બેસે તે રીતે)
            target_h = 585
            ratio = target_h / author.height
            target_w = int(author.width * ratio)
            if target_w > 530:
                scale = 530 / target_w
                target_w = 530
                target_h = int(target_h * scale)

            author = author.resize((target_w, target_h), Image.Resampling.LANCZOS)
            paste_x = W - target_w - 12
            paste_y = H - target_h - 40  # નીચેનો બાર શર્ટની ધારને ઢાંકીને ક્લીન લુક આપશે

            # ફોટા પાછળ ગોલ્ડન/વ્હાઈટ સ્ટુડિયો રિમ-લાઈટ ઓરા
            rim_canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rim_fill = Image.new("RGBA", author.size, (*th["accent"], 210))
            rim_fill.putalpha(author.getchannel("A"))
            rim_canvas.paste(rim_fill, (paste_x - 6, paste_y - 4))
            rim_canvas = rim_canvas.filter(ImageFilter.GaussianBlur(radius=18))
            canvas = Image.alpha_composite(canvas, rim_canvas)

            # મૂળ ફોટો પેસ્ટ કરો
            canvas.paste(author, (paste_x, paste_y), author)
        except Exception as e:
            print(f"[Thumbnail Photo Error] {e}")

    draw = ImageDraw.Draw(canvas)

    # 5) ટોપ એક્સેન્ટ બોર્ડર + LIVE બેજ
    draw.rectangle([0, 0, W, 9], fill=(*th["accent"], 255))

    badge = (badge_text or "MARKET BREAKING").upper()[:24]
    badge_font = get_font(25, bold=True)
    tb = draw.textbbox((0, 0), badge, font=badge_font)
    badge_w = (tb[2] - tb[0]) + 72
    draw.rounded_rectangle([52, 46, 52 + badge_w, 98], radius=10, fill=(*th["badge"], 255))
    # લાઈવ વ્હાઈટ ડોટ
    draw.ellipse([72, 64, 88, 80], fill=(255, 255, 255, 255))
    draw.text((100, 56), badge, font=badge_font, fill=(255, 255, 255, 255))

    # 6) મોટી 3D હેડલાઈન (પહેલી લાઈન હાઈલાઈટ બેનરમાં + બાકી લાઈન વ્હાઈટ બોલ્ડ)
    clean_hl = " ".join(str(headline).split()).upper()
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
            # પહેલી લાઈન પર જોરદાર ગોલ્ડન હાઈલાઈટ બોક્સ
            draw.rounded_rectangle(
                [46, y_pos - 6, 46 + lw + 32, y_pos + lh + 18],
                radius=10, fill=(*th["accent"], 255)
            )
            draw.text((62, y_pos), line, font=title_font, fill=(*th["accent_text"], 255))
            y_pos += line_step + 12
        else:
            # ડાર્ક 3D શેડો + ક્રિસ્પ વ્હાઈટ ટેક્સ્ટ
            draw.text((57, y_pos + 4), line, font=title_font, fill=(0, 0, 0, 220))
            draw.text(
                (52, y_pos), line, font=title_font,
                fill=(255, 255, 255, 255),
                stroke_width=2, stroke_fill=(0, 0, 0, 230)
            )
            y_pos += line_step

    # 7) નીચે ફુલ-વિડ્થ TV-ન્યૂઝ ટિકર બાર
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
        f"STOCK MARKET & BUSINESS ANALYSIS   •   {date_str}",
        font=meta_font, fill=(226, 232, 240, 255)
    )

    filename = f"thumb_{slug_id}.jpg"
    filepath = os.path.join(THUMBNAIL_DIR, filename)
    canvas.convert("RGB").save(filepath, "JPEG", quality=92, optimize=True)
    print(f"🎨 Thumbnail generated locally: {filepath}")

    if GITHUB_REPO:
        try:
            subprocess.run(["git", "config", "user.name", "MarketMunafa Bot"], check=False)
            subprocess.run(["git", "config", "user.email", "bot@marketmunafa.local"], check=False)
            subprocess.run(["git", "add", filepath], check=True)
            subprocess.run(["git", "commit", "-m", f"Add thumbnail {filename}"], check=False)
            push_res = subprocess.run(["git", "push"], capture_output=True, text=True)
            if push_res.returncode == 0:
                print(f"✅ Thumbnail pushed to GitHub: {filename}")
            else:
                print(f"⚠️ Git push warning: {push_res.stderr}")
            time.sleep(2)
        except Exception as e:
            print(f"[Git Push Thumb Error] {e}")

        # raw.githubusercontent.com 0 સેકન્ડમાં તરત લાઈવ લોડ થાય છે (Repo Public હોવી જરૂરી)
        return f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{THUMBNAIL_DIR}/{filename}"
    return None


# ==========================================
# 7. SEO & MULTILINGUAL HTML BUILDER
# ==========================================
def build_language_block(lang_data, lang_code, is_default=False):
    display_style = "block" if is_default else "none"
    intro_html = "".join(
        f"<p style='line-height:1.8; margin-bottom:14px;'>{p.strip()}</p>"
        for p in lang_data.get("intro", "").split("\n") if p.strip()
    )
    highlights_html = "".join(
        f"<li style='margin-bottom:8px;'>{item}</li>"
        for item in lang_data.get("key_highlights", [])
    )
    sections_html = ""
    for sec in lang_data.get("sections", []):
        body_ps = "".join(
            f"<p style='line-height:1.8; margin-bottom:13px;'>{p.strip()}</p>"
            for p in sec.get("body", "").split("\n") if p.strip()
        )
        sections_html += f"""
        <h2 style="color:#0f172a; font-size:21px; margin-top:24px; margin-bottom:10px; border-left:4px solid #1d4ed8; padding-left:10px;">
            {sec.get('heading', '')}
        </h2>
        {body_ps}
        """

    faqs_html = ""
    for faq in lang_data.get("faqs", []):
        faqs_html += f"""
        <div style="margin-bottom:12px; padding:12px 14px; background:#f8fafc; border-radius:8px; border:1px solid #e2e8f0;">
            <p style="margin:0 0 6px 0; font-weight:700; color:#1e293b;">Q: {faq.get('q', '')}</p>
            <p style="margin:0; color:#334155; line-height:1.65;">A: {faq.get('a', '')}</p>
        </div>
        """

    return f"""
    <div class="mm-lang-pane" data-lang="{lang_code}" style="display:{display_style};">
        <div style="font-size:16px; color:#1e293b;">{intro_html}</div>

        <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:16px 18px; margin:20px 0;">
            <h2 style="margin-top:0; font-size:19px; color:#1e3a8a;">📌 Key Highlights</h2>
            <ul style="margin:0; padding-left:20px; line-height:1.75; color:#1e293b;">
                {highlights_html}
            </ul>
        </div>

        {sections_html}

        <div style="margin-top:24px;">
            <h2 style="color:#0f172a; font-size:20px; margin-bottom:12px;">❓ Frequently Asked Questions (FAQ)</h2>
            {faqs_html}
        </div>

        <div style="margin-top:22px; padding:15px 18px; background:#f0fdf4; border-left:4px solid #16a34a; border-radius:8px;">
            <p style="margin:0; line-height:1.75; color:#14532d;"><strong>Conclusion:</strong> {lang_data.get('conclusion', '')}</p>
        </div>
    </div>
    """


def build_complete_post_html(verified_en, translations, thumb_url):
    ist_str = get_ist_now().strftime("%B %d, %Y | %I:%M %p IST")
    uid = hashlib.md5(verified_en["seo_title"].encode("utf-8")).hexdigest()[:8]

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

    lang_buttons = [f"<button type='button' class='mm-lbtn active' onclick=\"mmSwitchPostLang('{uid}','en',this)\">English</button>"]
    for code, native_label, _ in TARGET_LANGUAGES:
        if code in translations:
            lang_buttons.append(
                f"<button type='button' class='mm-lbtn' onclick=\"mmSwitchPostLang('{uid}','{code}',this)\">{native_label}</button>"
            )

    panes_html = build_language_block(verified_en, "en", is_default=True)
    for code, _, _ in TARGET_LANGUAGES:
        if code in translations:
            panes_html += build_language_block(translations[code], code, is_default=False)

    thumb_block = ""
    if thumb_url:
        thumb_block = f"""
        <figure style="margin:0 0 18px 0; text-align:center;">
            <img src="{thumb_url}" alt="{verified_en['seo_title']}" width="1200" height="675"
                 style="width:100%; height:auto; border-radius:12px; box-shadow:0 6px 18px rgba(0,0,0,0.14);" />
        </figure>
        """

    html = f"""
<article id="mm-post-{uid}" style="font-family:'Noto Sans Gujarati','Noto Sans Devanagari','Segoe UI',Roboto,Arial,sans-serif; max-width:860px; margin:0 auto;">
  <style>
    #mm-post-{uid} .mm-langbar {{
      display:flex; flex-wrap:wrap; gap:7px; align-items:center;
      background:#f1f5f9; padding:12px; border-radius:10px; margin:16px 0 20px 0;
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
    📅 Published on: {ist_str} | By <strong>MarketMunafa Editorial Desk</strong>
  </p>

  <div class="mm-langbar">
    <span style="font-weight:700; font-size:14px; color:#0f172a; margin-right:4px;">🌐 ભાષા બદલો / Read in:</span>
    {"".join(lang_buttons)}
  </div>

  {panes_html}

  <hr style="margin:26px 0 14px 0; border:none; border-top:1px solid #e2e8f0;" />
  <p style="font-size:12.5px; color:#64748b; line-height:1.6;">
    <strong>Disclaimer:</strong> This report is prepared by the MarketMunafa Editorial Desk for informational and educational purposes only and does not constitute investment advice. Please consult a SEBI-registered financial advisor before making any investment decisions.
  </p>

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


# ==========================================
# 8. BLOGGER API PUBLISHING
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


def publish_to_blogger(access_token, title, html_content, labels):
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
        "title": title,
        "content": html_content,
        "labels": clean_labels[:12]
    }
    res = requests.post(api_url, headers=headers, json=payload, timeout=25)
    if res.status_code in (200, 201):
        print(f"✅ Published: {title} -> {res.json().get('url', '')}")
        return True
    else:
        print(f"❌ Blogger API Error ({res.status_code}): {res.text}")
        return False


# ==========================================
# 9. MAIN EXECUTION LOOP
# ==========================================
def main():
    history = load_history()
    today_key = get_ist_now().strftime("%Y-%m-%d")
    posted_today = history["daily_counts"].get(today_key, 0)

    if posted_today >= DAILY_MAX_POSTS:
        print(f"ℹ️ આજે પહેલેથી જ {posted_today} પોસ્ટ થઈ ગઈ છે (Daily limit: {DAILY_MAX_POSTS}).")
        return

    remaining_today = min(MAX_POSTS_PER_RUN, DAILY_MAX_POSTS - posted_today)
    candidates = fetch_fresh_diverse_articles(history)
    if not candidates:
        print("ℹ️ હાલમાં છેલ્લા 36 કલાકના કોઈ નવા અલગ આર્ટિકલ મળ્યા નથી.")
        return

    access_token = get_blogger_access_token()
    published_count = 0

    for art in candidates:
        if published_count >= remaining_today:
            break

        print(f"\n🔍 Fresh News [{art['source']}]: {art['title']}")
        try:
            draft_en = generate_original_english_article(art)
            time.sleep(2)

            verified_en = verify_and_polish_article(draft_en, art)
            time.sleep(2)

            translations = {}
            all_langs_ready = True
            for code, native_name, eng_name in TARGET_LANGUAGES:
                print(f"   🌐 Translating to {eng_name}...")
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
                print("⏭️ બધી ભાષા પૂરી ન બની હોવાથી આ પોસ્ટ સ્કિપ કરી.")
                continue

            slug_id = hashlib.md5(art["url"].encode("utf-8")).hexdigest()[:10]
            thumb_url = create_custom_thumbnail(
                verified_en.get("thumbnail_headline", verified_en["seo_title"][:50]),
                verified_en.get("category_badge", "MARKET BREAKING"),
                slug_id,
                theme_index=posted_today
            )

            post_html = build_complete_post_html(verified_en, translations, thumb_url)
            ok = publish_to_blogger(
                access_token=access_token,
                title=verified_en["seo_title"],
                html_content=post_html,
                labels=verified_en.get("tags", ["Stock Market", "Business News"])
            )

            if ok:
                published_count += 1
                posted_today += 1
                history["posted_urls"].append(art["url"])
                history["posted_titles"].append(art["title"])
                history["posted_titles"].append(verified_en["seo_title"])
                history["daily_counts"][today_key] = posted_today
                save_history(history)
                time.sleep(5)

        except Exception as e:
            print(f"❌ Error processing article: {e}")
            continue

    print(f"\n🎉 Run finished! આ રનમાં કુલ {published_count} અલગ-અલગ નવી પોસ્ટ પબ્લિશ થઈ (આજની કુલ: {posted_today}).")


if __name__ == "__main__":
    main()
