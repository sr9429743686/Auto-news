import os
import re
import json
import time
import hashlib
import textwrap
import subprocess
import difflib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. CONFIGURATION (GitHub Secrets માંથી આવશે)
# ==========================================
CLIENT_ID = os.environ.get("CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "").strip()
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "").strip()
BLOG_ID = os.environ.get("BLOG_ID", "6996150531679127159").strip()

# GEMINI_API_KEYS અથવા GEMINI_API_KEY બંનેમાંથી Key લઈ લેશે (કોમા કે નવી લાઈન બંને ચાલશે)
RAW_GEMINI_KEYS = (
    os.environ.get("GEMINI_API_KEYS", "") + "," + os.environ.get("GEMINI_API_KEY", "")
)
GEMINI_KEYS = [
    k.strip() for k in re.split(r"[\s,;]+", RAW_GEMINI_KEYS) if len(k.strip()) > 10
]

PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
MODEL_FALLBACKS = list(dict.fromkeys([
    PRIMARY_MODEL,
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite"
]))

MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "3"))
DAILY_MAX_POSTS = int(os.environ.get("DAILY_MAX_POSTS", "20"))
MAX_ARTICLE_AGE_HOURS = int(os.environ.get("MAX_ARTICLE_AGE_HOURS", "36"))

GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "").strip()
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()

# તમે મોકલેલો ફોટો આમાંથી કોઈ પણ નામે અપલોડ હશે તો ઓટોમેટિક મળી જશે
AUTHOR_IMAGE_CANDIDATES = [
    "author.png",
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

# બધી 7 વેબસાઈટ્સના તાજા પબ્લિક RSS ફીડ્સ
NEWS_FEEDS = [
    ("Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("LiveMint", "https://www.livemint.com/rss/markets"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("NDTV Business", "https://feeds.feedburner.com/ndtvprofit-latest"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("Zee Business", "https://www.zeebiz.com/india-markets.xml"),
    ("NYTimes Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Moneycontrol Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("LiveMint News", "https://www.livemint.com/rss/news"),
]

# ભારતની મુખ્ય ભાષાઓ (બધી ભાષા તૈયાર થયા પછી જ પોસ્ટ પબ્લિશ થશે)
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

# દરેક પોસ્ટના થમ્બનેલ માટે અલગ-અલગ પ્રીમિયમ કલર થીમ
THUMB_THEMES = [
    {"bg1": (8, 22, 48), "bg2": (24, 58, 100), "accent": (245, 158, 11), "badge": (220, 38, 38), "bar": (15, 118, 110)},
    {"bg1": (15, 23, 42), "bg2": (49, 46, 129), "accent": (56, 189, 248), "badge": (234, 88, 12), "bar": (30, 64, 175)},
    {"bg1": (6, 40, 38), "bg2": (17, 94, 89), "accent": (250, 204, 21), "badge": (185, 28, 28), "bar": (15, 23, 42)},
    {"bg1": (30, 14, 38), "bg2": (88, 28, 135), "accent": (251, 191, 36), "badge": (219, 39, 119), "bar": (17, 24, 39)},
    {"bg1": (36, 16, 16), "bg2": (127, 29, 29), "accent": (253, 224, 71), "badge": (15, 118, 110), "bar": (15, 23, 42)},
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
    """બે અલગ વેબસાઈટ પર એક જ ન્યૂઝ હોય તો તેને ડુપ્લીકેટ ગણીને સ્કિપ કરે છે."""
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
    """માત્ર છેલ્લા 36 કલાકના તાજા સમાચાર જ પસંદ કરે છે (જૂના વર્ષના આર્ટિકલ બ્લોક)."""
    # ટાઈટલમાં જૂનું વર્ષ (2021-2025 કે '22/'23/'24) હોય તો તરત સ્કિપ
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
    """મૂળ આર્ટિકલની કોઈ 6+ શબ્દોની લાઈન કોપી થઈ હોય તો પકડી પાડે છે."""
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
# 3. FRESH & DIVERSE NEWS FETCHING (ROUND-ROBIN)
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
    """બધી 7 સાઇટ્સમાંથી વારાફરતી માત્ર આજના તાજા અને અલગ-અલગ ન્યૂઝ લાવે છે."""
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
                # દરેક સાઇટમાંથી મહત્તમ 2 તાજા ન્યૂઝ જેથી બધી સાઇટનું મિશ્રણ મળે
                if len(bucket) >= 2:
                    break
        except Exception as e:
            print(f"[Feed Skip] {source_name}: {e}")
            continue

        if bucket:
            per_source_buckets.append(bucket)

    # Round-Robin: 1-1 ન્યૂઝ દરેક સાઇટમાંથી વારાફરતી ગોઠવો
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
# 5. TWO-STEP ORIGINAL REWRITING & COPYRIGHT AUDIT
# ==========================================
def generate_original_english_article(article):
    today_str = get_ist_now().strftime("%B %d, %Y")
    prompt = f"""
You are a senior Indian financial journalist writing for 'MarketMunafa' on {today_str}.
Read the source facts below and write a COMPLETE, 100% ORIGINAL, human-sounding news analysis article from scratch as if you researched and reported it yourself.

STRICT RULES:
- ZERO copy-paste: Do NOT reuse sentences, phrasing, or paragraph structure from the source.
- Keep all numbers, dates, company names, and facts 100% accurate. Never invent numbers.
- Write a COMPLETE, detailed article (700-950 words), never half or cut-off.
- Optimize for Google News, Google Discover, AdSense safety, and top Google Search ranking: crisp factual SEO title (no clickbait), clear lead, 3 rich H2 analysis sections, 5 bullet highlights, 3 reader FAQs, and conclusion.
- Generate 10 to 12 SEO tags (mix of broad popular tags like 'Stock Market', 'Business News', 'Share Market' and specific company/sector tags).
- Provide a punchy 4-to-7 word thumbnail headline for the image banner.

SOURCE TITLE: {article['title']}
SOURCE FACTS:
{article['full_text']}

Return ONLY valid JSON with this exact schema:
{{
  "seo_title": "Clear, high-CTR Google News headline (60-78 chars)",
  "thumbnail_headline": "Short 4-7 word bold headline for thumbnail",
  "category_badge": "2-3 word category e.g. MARKET UPDATE / STOCK NEWS",
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
    """Pass 2: Python n-gram ચેક + Gemini ડબલ કોપીરાઇટ/ફેક્ટ/SEO ઓડિટ."""
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
1. 100% COPYRIGHT FREE: Ensure ZERO phrases (6+ words) match the source verbatim. Rewrite any similar wording into natural, fresh journalism.
2. FACT ACCURACY: Confirm every figure, date, and company detail matches the source without exaggeration.
3. COMPLETENESS: Confirm intro, 5 key_highlights, 3 full sections, 3 FAQs, and conclusion are all complete (no truncated text).
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
# 6. CUSTOM THUMBNAIL GENERATOR (PNG / JPG)
# ==========================================
def find_author_photo():
    for fname in AUTHOR_IMAGE_CANDIDATES:
        if os.path.exists(fname):
            return fname
    return None


def remove_white_bg_clean(img_rgba, threshold=236):
    datas = img_rgba.getdata()
    new_data = []
    for r, g, b, a in datas:
        if r >= threshold and g >= threshold and b >= threshold:
            new_data.append((255, 255, 255, 0))
        elif r >= threshold - 18 and g >= threshold - 18 and b >= threshold - 18:
            avg = (r + g + b) // 3
            alpha = max(0, min(255, int((threshold - avg) * 14)))
            new_data.append((r, g, b, alpha))
        else:
            new_data.append((r, g, b, a))
    img_rgba.putdata(new_data)
    return img_rgba


def get_font(size, bold=True):
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
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


def create_custom_thumbnail(headline, badge_text, slug_id, theme_index=0):
    """તમારા PNG/JPG ફોટા સાથે 1200x675 HD થમ્બનેલ બનાવી તરત GitHub પર લાઈવ કરે છે."""
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    W, H = 1200, 675
    th = THUMB_THEMES[theme_index % len(THUMB_THEMES)]

    canvas = Image.new("RGBA", (W, H), (*th["bg1"], 255))
    draw = ImageDraw.Draw(canvas)

    # સ્મૂથ ગ્રેડિયન્ટ બેકગ્રાઉન્ડ
    for y in range(H):
        r = int(th["bg1"][0] + (th["bg2"][0] - th["bg1"][0]) * y / H)
        g = int(th["bg1"][1] + (th["bg2"][1] - th["bg1"][1]) * y / H)
        b = int(th["bg1"][2] + (th["bg2"][2] - th["bg1"][2]) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # જમણી બાજુ ફોટા પાછળ સોફ્ટ ગ્લો
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse([690, 50, 1240, 610], fill=(*th["accent"], 55))
    gdraw.ellipse([760, 120, 1180, 550], fill=(255, 255, 255, 35))
    canvas = Image.alpha_composite(canvas, glow)
    draw = ImageDraw.Draw(canvas)

    # ટોપ એક્સેન્ટ સ્ટ્રીપ
    draw.rectangle([0, 0, W, 10], fill=(*th["accent"], 255))

    # કેટેગરી બેજ
    badge = (badge_text or "MARKET NEWS").upper()[:26]
    badge_font = get_font(28, bold=True)
    badge_w = max(280, len(badge) * 20 + 40)
    draw.rounded_rectangle([55, 55, 55 + badge_w, 108], radius=10, fill=(*th["badge"], 255))
    draw.text((75, 65), badge, font=badge_font, fill=(255, 255, 255, 255))

    # મુખ્ય હેડલાઈન (ડાબી બાજુ)
    title_font = get_font(54, bold=True)
    wrapped = textwrap.wrap(headline, width=21)[:4]
    y_pos = 152
    for idx, line in enumerate(wrapped):
        color = (*th["accent"], 255) if idx == 0 else (255, 255, 255, 255)
        draw.text((55, y_pos), line, font=title_font, fill=color)
        y_pos += 76

    # નીચે બ્રાન્ડિંગ બાર
    sub_font = get_font(26, bold=True)
    date_str = get_ist_now().strftime("%d %b %Y")
    draw.rounded_rectangle([55, 555, 575, 618], radius=12, fill=(*th["bar"], 240))
    draw.text((78, 571), f"MARKETMUNAFA  |  {date_str}", font=sub_font, fill=(255, 255, 255, 255))

    # તમારો PNG/JPG ફોટો જમણી બાજુ ગોઠવવો
    author_path = find_author_photo()
    if author_path:
        try:
            author = Image.open(author_path).convert("RGBA")
            # જો PNG પહેલેથી ટ્રાન્સપરન્ટ હોય તો સીધો જ વાપરવો; નહીંતર સફેદ બેકગ્રાઉન્ડ હટાવવું
            alpha_min, _ = author.getchannel("A").getextrema()
            if alpha_min >= 250:
                author = remove_white_bg_clean(author)

            # ખાલી ટ્રાન્સપરન્ટ બોર્ડર ક્રોપ કરો જેથી ફોટો મોટો અને ચોખ્ખો દેખાય
            bbox = author.getbbox()
            if bbox:
                author = author.crop(bbox)

            target_h = 595
            ratio = target_h / author.height
            target_w = int(author.width * ratio)
            if target_w > 540:
                scale = 540 / target_w
                target_w = 540
                target_h = int(target_h * scale)

            author = author.resize((target_w, target_h), Image.Resampling.LANCZOS)
            paste_x = W - target_w - 10
            paste_y = H - target_h
            canvas.paste(author, (paste_x, paste_y), author)
        except Exception as e:
            print(f"[Thumbnail Photo Error] {e}")
    else:
        print("⚠️ author.png / 1001185905.png ફોટો રેપોમાં મળ્યો નથી!")

    filename = f"thumb_{slug_id}.jpg"
    filepath = os.path.join(THUMBNAIL_DIR, filename)
    canvas.convert("RGB").save(filepath, "JPEG", quality=90, optimize=True)

    if GITHUB_REPO:
        # Blogger પર પોસ્ટ પબ્લિશ થાય તે પહેલા જ થમ્બનેલ GitHub પર પુશ કરી દો
        try:
            subprocess.run(["git", "add", filepath], check=True)
            subprocess.run([
                "git", "-c", "user.name=MarketMunafa Bot",
                "-c", "user.email=bot@marketmunafa.local",
                "commit", "-m", f"Add thumbnail {filename}"
            ], check=False)
            subprocess.run(["git", "push"], check=True)
            time.sleep(3)
        except Exception as e:
            print(f"[Git Push Thumb Warning] {e}")
        return f"https://cdn.jsdelivr.net/gh/{GITHUB_REPO}@{GITHUB_BRANCH}/{THUMBNAIL_DIR}/{filename}"
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
                 style="width:100%; height:auto; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.08);" />
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
# 9. MAIN EXECUTION LOOP (દરેક ન્યૂઝની અલગ પોસ્ટ)
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
            # Step 1: 100% મૌલિક અંગ્રેજી લેખ
            draft_en = generate_original_english_article(art)
            time.sleep(2)

            # Step 2: Python n-gram ચેક + Gemini ડબલ કોપીરાઇટ/SEO ઓડિટ
            verified_en = verify_and_polish_article(draft_en, art)
            time.sleep(2)

            # Step 3: ભારતની તમામ મુખ્ય ભાષામાં સંપૂર્ણ અનુવાદ
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

            # Step 4: તમારા PNG ફોટા સાથે અલગ કલર થીમનું 1200x675 HD થમ્બનેલ
            slug_id = hashlib.md5(art["url"].encode("utf-8")).hexdigest()[:10]
            thumb_url = create_custom_thumbnail(
                verified_en.get("thumbnail_headline", verified_en["seo_title"][:55]),
                verified_en.get("category_badge", "MARKET NEWS"),
                slug_id,
                theme_index=posted_today
            )

            # Step 5: અલગ પોસ્ટ તરીકે Blogger પર પબ્લિશ
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
