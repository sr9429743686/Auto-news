import os
import re
import json
import time
import hashlib
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

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

# એક કરતા વધુ Gemini Key કોમા (,) થી અલગ કરીને GitHub Secret 'GEMINI_API_KEYS' માં મૂકવી
RAW_GEMINI_KEYS = os.environ.get("GEMINI_API_KEYS", os.environ.get("GEMINI_API_KEY", ""))
GEMINI_KEYS = [k.strip() for k in RAW_GEMINI_KEYS.split(",") if k.strip()]

# મોડેલ લિસ્ટ (પહેલા તમારું પસંદગીનું મોડેલ ટ્રાય થશે, ન ચાલે તો ઓટો-ફોલબેક)
PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
MODEL_FALLBACKS = list(dict.fromkeys([
    PRIMARY_MODEL,
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite"
]))

# દર વખતે કેટલી પોસ્ટ કરવી (દિવસમાં 7-8 વખત રન થાય તો ~20 પોસ્ટ/દિવસ)
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "3"))
DAILY_MAX_POSTS = int(os.environ.get("DAILY_MAX_POSTS", "20"))

# GitHub Repo info (થમ્બનેલને ફ્રી અને કાયમી CDN URL આપવા માટે)
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "").strip()  # e.g. username/repo
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()

AUTHOR_IMAGE_PATH = "author.jpg"
THUMBNAIL_DIR = "thumbnails"
HISTORY_FILE = "posted_urls.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9"
}

# 7 વેબસાઈટ્સના પબ્લિક RSS ફીડ્સ (પેવોલ વગરના તાજા સમાચાર માટે)
NEWS_FEEDS = [
    ("Zee Business", "https://www.zeebiz.com/india-markets.xml"),
    ("Zee Business", "https://www.zeebiz.com/latest.xml"),
    ("NDTV Business", "https://feeds.feedburner.com/ndtvprofit-latest"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("Moneycontrol Markets", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("LiveMint", "https://www.livemint.com/rss/markets"),
    ("LiveMint News", "https://www.livemint.com/rss/news"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
    ("NYTimes Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
]

# ભારતની મુખ્ય ભાષાઓ (દરેક પોસ્ટ આ બધી ભાષામાં બન્યા પછી જ પબ્લિશ થશે)
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
                return json.load(f)
        except Exception:
            pass
    return {"posted_urls": [], "daily_counts": {}}


def save_history(history):
    history["posted_urls"] = history["posted_urls"][-1000:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def clean_html_text(raw):
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


# ==========================================
# 3. PUBLIC NEWS FETCHING (FULL FREE ARTICLES)
# ==========================================
def extract_full_public_article(url):
    """પબ્લિક ફ્રી આર્ટિકલનો પૂરો ટેક્સ્ટ લાવે છે; પેવોલ/અધૂરો આર્ટિકલ સ્કિપ કરે છે."""
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return None

        soup = BeautifulSoup(res.content, "html.parser")

        # બિનજરૂરી ટેગ્સ હટાવો
        for tag in soup.find_all(["script", "style", "nav", "footer", "aside", "form", "noscript", "iframe"]):
            tag.decompose()

        body_text_low = soup.get_text(" ", strip=True).lower()
        # પેવોલ વાળા અધૂરા લેખ સ્કિપ કરો
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

        # જો ટેક્સ્ટ બહુ નાનો હોય અથવા પેવોલ નોટિસ હોય તો સ્કિપ કરો
        if len(full_text) < 450:
            return None
        if any(p_hint in body_text_low for p_hint in PAYWALL_HINTS) and len(full_text) < 900:
            return None

        return full_text[:6500]
    except Exception:
        return None


def fetch_candidate_articles(posted_urls):
    candidates = []
    seen = set(posted_urls)

    for source_name, feed_url in NEWS_FEEDS:
        try:
            res = requests.get(feed_url, headers=HEADERS, timeout=12)
            if res.status_code != 200:
                continue
            root = ET.fromstring(res.content)
            items = root.findall(".//item")[:6]

            for item in items:
                title = clean_html_text(item.findtext("title"))
                link = (item.findtext("link") or "").strip()
                desc = clean_html_text(item.findtext("description"))

                if not title or not link or link in seen:
                    continue
                if "/prime/" in link.lower():
                    continue  # પેઇડ સેક્શન સ્કિપ

                seen.add(link)
                full_text = extract_full_public_article(link)
                if not full_text:
                    continue

                candidates.append({
                    "source": source_name,
                    "title": title,
                    "url": link,
                    "summary": desc,
                    "full_text": full_text
                })

                if len(candidates) >= MAX_POSTS_PER_RUN * 3:
                    return candidates
        except Exception as e:
            print(f"[Feed Skip] {source_name}: {e}")
            continue

    return candidates


# ==========================================
# 4. GEMINI API WITH KEY ROTATION & FALLBACK
# ==========================================
def call_gemini(prompt, expect_json=True, max_tokens=4096):
    global key_index
    if not GEMINI_KEYS:
        raise RuntimeError("GEMINI_API_KEYS સેટ નથી! GitHub Secrets માં ઉમેરો.")

    last_err = None
    for model_name in MODEL_FALLBACKS:
        for attempt in range(len(GEMINI_KEYS)):
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
                    print(f"[Rate Limit] Model {model_name}, rotating key...")
                    time.sleep(3)
                    last_err = f"429: {res.text[:200]}"
                    continue
                else:
                    last_err = f"{model_name} HTTP {res.status_code}: {res.text[:250]}"
                    print(f"[Gemini Warning] {last_err}")
                    break  # Try next fallback model
            except Exception as e:
                last_err = str(e)
                time.sleep(2)

    raise RuntimeError(f"Gemini API નિષ્ફળ ગઈ: {last_err}")


# ==========================================
# 5. TWO-STEP ORIGINAL REWRITING & AUDIT
# ==========================================
def generate_original_english_article(article):
    """Pass 1: ૧૦૦% મૌલિક, પૂરો SEO & Google News ફ્રેન્ડલી આર્ટિકલ બનાવે છે."""
    today_str = get_ist_now().strftime("%B %d, %Y")
    prompt = f"""
You are a senior Indian financial journalist writing for 'MarketMunafa' on {today_str}.
Read the source facts below and write a COMPLETE, 100% ORIGINAL, human-sounding, deeply informative news analysis article from scratch.

STRICT RULES:
- DO NOT copy sentences or structure from the source. Completely rewrite in fresh journalistic prose.
- Keep all numbers, dates, company names, and facts 100% accurate to the source. Do NOT invent numbers.
- Write a COMPLETE article (at least 650-900 words), never half or truncated.
- Follow Google News, Google Discover, and AdSense E-E-A-T best practices: clear neutral headline (no clickbait), inverted-pyramid lead, structured H2 sections, investor takeaways, and FAQs.
- Generate 10 to 14 SEO tags/labels (mix of short popular tags like 'Stock Market', 'Business News' and specific company/topic tags).
- Provide a short 4-to-7 word punchy thumbnail headline for the image banner.

SOURCE: {article['source']}
SOURCE TITLE: {article['title']}
SOURCE CONTENT:
{article['full_text']}

Return ONLY valid JSON with this exact schema:
{{
  "seo_title": "Clear, high-CTR Google News headline (60-78 chars)",
  "thumbnail_headline": "Short 4-7 word bold headline for thumbnail",
  "category_badge": "2-3 word category e.g. MARKET UPDATE / BUSINESS NEWS",
  "meta_description": "Compelling 140-155 char SEO meta description",
  "tags": ["Tag1", "Tag2", "Tag3", "..."],
  "intro": "2 strong opening paragraphs explaining who, what, when, why it matters.",
  "key_highlights": ["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4", "Bullet 5"],
  "sections": [
    {{"heading": "H2 Section Title 1", "body": "Detailed 2-3 paragraphs"}},
    {{"heading": "H2 Section Title 2", "body": "Detailed 2-3 paragraphs"}},
    {{"heading": "Impact on Investors & Market Outlook", "body": "Balanced analytical view without buy/sell financial advice"}}
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
    """Pass 2 (ડબલ ચેક): કોપીરાઇટ ફ્રી, ફેક્ટ-ચેક અને Google News/AdSense ક્વોલિટી ઓડિટ."""
    prompt = f"""
You are the Chief Editor and Copyright/SEO Auditor for MarketMunafa.
Perform a STRICT 2nd-pass audit on the DRAFT ARTICLE against the ORIGINAL SOURCE.

CHECKLIST:
1. COPYRIGHT SAFETY: Ensure zero phrases (6+ words) are copied verbatim from the source. Rephrase anything similar into fresh original English.
2. FACT CHECK: Verify every number, percentage, company name, and date matches the source. Fix any exaggeration or hallucination.
3. COMPLETENESS: Confirm intro, 5 key_highlights, 3 detailed sections, 3 FAQs, and conclusion are all complete and rich (no placeholders, no cut-off sentences).
4. GOOGLE NEWS & ADSENSE SAFETY: Ensure neutral journalistic tone, no guaranteed profit claims, no policy-violating wording, high-ranking SEO title, and clean popular + specific tags (max 12 tags, each under 30 chars).

ORIGINAL SOURCE EXCERPT:
{article['full_text'][:3500]}

DRAFT JSON TO AUDIT & IMPROVE:
{json.dumps(draft_json, ensure_ascii=False)}

Return ONLY the final verified, polished JSON in the EXACT same schema as the draft, plus `"audit_passed": true`.
"""
    verified = call_gemini(prompt, expect_json=True, max_tokens=4096)
    # પૂરું કન્ટેન્ટ છે કે નહીં તેની ચકાસણી
    if not verified.get("seo_title") or not verified.get("sections") or len(verified.get("sections", [])) < 2:
        raise ValueError("Verified article incomplete.")
    return verified


def translate_article_to_language(verified_en, lang_name):
    """એક-એક ભાષામાં સંપૂર્ણ, કુદરતી અનુવાદ (અધૂરું કન્ટેન્ટ ટાળવા અલગ કોલ)."""
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
- Translate EVERY single field completely (seo_title, intro, all key_highlights, all sections, all faqs, conclusion). Do NOT summarize or shorten anything.
- Keep company names, ticker names, ₹ amounts, and well-known financial terms (IPO, RBI, SEBI, Nifty, Sensex, EBITDA, Q2) natural and easy to understand.
- Keep the exact same JSON keys and structure.

INPUT JSON:
{json.dumps(payload_to_translate, ensure_ascii=False)}

Return ONLY valid JSON.
"""
    translated = call_gemini(prompt, expect_json=True, max_tokens=4096)
    if not translated.get("sections") or len(translated["sections"]) < len(verified_en["sections"]):
        raise ValueError(f"{lang_name} translation incomplete.")
    return translated


# ==========================================
# 6. CUSTOM THUMBNAIL GENERATOR (author.jpg)
# ==========================================
def remove_white_bg_clean(img_rgba, threshold=232):
    """તમારા સફેદ બેકગ્રાઉન્ડ વાળા ફોટાને સાફ કરીને ટ્રાન્સપરન્ટ કટઆઉટ બનાવે છે."""
    datas = img_rgba.getdata()
    new_data = []
    for r, g, b, a in datas:
        if r >= threshold and g >= threshold and b >= threshold:
            new_data.append((255, 255, 255, 0))
        elif r >= threshold - 20 and g >= threshold - 20 and b >= threshold - 20:
            # કિનારી સોફ્ટ રાખવા માટે
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


def create_custom_thumbnail(headline, badge_text, slug_id):
    """1200x675 (16:9 Google Discover & Google News HD) થમ્બનેલ બનાવે છે."""
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)
    W, H = 1200, 675
    canvas = Image.new("RGBA", (W, H), (10, 25, 47, 255))
    draw = ImageDraw.Draw(canvas)

    # ડાર્ક ફાઇનાન્સ ગ્રેડિયન્ટ બેકગ્રાઉન્ડ
    for y in range(H):
        r = int(8 + (16 * y / H))
        g = int(22 + (36 * y / H))
        b = int(48 + (52 * y / H))
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # જમણી બાજુ સોફ્ટ ગોલ્ડ/બ્લુ ગ્લો સર્કલ
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse([700, 60, 1240, 620], fill=(25, 118, 210, 65))
    gdraw.ellipse([760, 120, 1180, 560], fill=(245, 158, 11, 45))
    canvas = Image.alpha_composite(canvas, glow)
    draw = ImageDraw.Draw(canvas)

    # ટોપ ગોલ્ડ સ્ટ્રીપ
    draw.rectangle([0, 0, W, 10], fill=(245, 158, 11, 255))

    # કેટેગરી બેજ
    badge = (badge_text or "MARKET UPDATE").upper()[:26]
    badge_font = get_font(28, bold=True)
    draw.rounded_rectangle([55, 55, 55 + max(280, len(badge) * 20), 108], radius=10, fill=(220, 38, 38, 255))
    draw.text((75, 65), badge, font=badge_font, fill=(255, 255, 255, 255))

    # મુખ્ય હેડલાઈન (ડાબી બાજુ)
    title_font = get_font(56, bold=True)
    wrapped = textwrap.wrap(headline, width=21)[:4]
    y_pos = 155
    for idx, line in enumerate(wrapped):
        color = (250, 204, 21, 255) if idx == 0 else (255, 255, 255, 255)
        draw.text((55, y_pos), line, font=title_font, fill=color)
        y_pos += 76

    # નીચે બ્રાન્ડિંગ બાર
    sub_font = get_font(26, bold=True)
    date_str = get_ist_now().strftime("%d %b %Y")
    draw.rounded_rectangle([55, 555, 560, 618], radius=12, fill=(15, 118, 110, 235))
    draw.text((78, 571), f"MARKETMUNAFA  |  {date_str}", font=sub_font, fill=(255, 255, 255, 255))

    # તમારા ફોટા (author.jpg) ને જમણી બાજુ ગોઠવવો
    if os.path.exists(AUTHOR_IMAGE_PATH):
        try:
            author = Image.open(AUTHOR_IMAGE_PATH).convert("RGBA")
            author = remove_white_bg_clean(author)
            target_h = 590
            ratio = target_h / author.height
            target_w = int(author.width * ratio)
            author = author.resize((target_w, target_h), Image.Resampling.LANCZOS)

            paste_x = W - target_w - 15
            paste_y = H - target_h
            canvas.paste(author, (paste_x, paste_y), author)
        except Exception as e:
            print(f"[Thumbnail Photo Error] {e}")

    filename = f"thumb_{slug_id}.jpg"
    filepath = os.path.join(THUMBNAIL_DIR, filename)
    canvas.convert("RGB").save(filepath, "JPEG", quality=90, optimize=True)

    if GITHUB_REPO:
        # jsDelivr CDN ફાસ્ટ અને Google News/Blogger માટે 100% ફ્રેન્ડલી છે
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


def build_complete_post_html(verified_en, translations, thumb_url, source_name):
    ist_str = get_ist_now().strftime("%B %d, %Y | %I:%M %p IST")
    uid = hashlib.md5(verified_en["seo_title"].encode("utf-8")).hexdigest()[:8]

    # Google News & SEO FAQ Schema (JSON-LD)
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

    # ભાષા બદલવાના બટન્સ
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
    <strong>Disclaimer & Editorial Note:</strong> This report is prepared by the MarketMunafa Editorial Desk based on publicly available market developments and news reports ({source_name}) for informational and educational purposes only. It does not constitute investment advice. Please consult a SEBI-registered financial advisor before making investment decisions.
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
        raise RuntimeError("Blogger OAuth credentials missing in environment variables.")
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
    # Blogger labels નિયમ: કુલ કેરેક્ટર <= 200 અને ખાસ ચિહ્નો વગર
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
        post_url = res.json().get("url", "")
        print(f"✅ Published successfully: {title} -> {post_url}")
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
    candidates = fetch_candidate_articles(history["posted_urls"])
    if not candidates:
        print("ℹ️ હાલમાં કોઈ નવા પબ્લિક ફુલ આર્ટિકલ મળ્યા નથી.")
        return

    access_token = get_blogger_access_token()
    published_count = 0

    for art in candidates:
        if published_count >= remaining_today:
            break

        print(f"\n🔍 Processing [{art['source']}]: {art['title']}")
        try:
            # Step 1: મૌલિક અંગ્રેજી લેખ
            draft_en = generate_original_english_article(art)
            time.sleep(2)

            # Step 2: Gemini ડબલ ચેક (કોપીરાઇટ ફ્રી + ફેક્ટ ચેક + SEO)
            verified_en = verify_and_polish_article(draft_en, art)
            time.sleep(2)

            # Step 3: ભારતની તમામ મુખ્ય ભાષામાં સંપૂર્ણ અનુવાદ (પહેલા બધી ભાષા તૈયાર થશે)
            translations = {}
            all_langs_ready = True
            for code, native_name, eng_name in TARGET_LANGUAGES:
                print(f"   🌐 Translating to {eng_name}...")
                try:
                    translations[code] = translate_article_to_language(verified_en, eng_name)
                    time.sleep(2)
                except Exception as te:
                    print(f"   ⚠️ Retry {eng_name} once: {te}")
                    time.sleep(4)
                    try:
                        translations[code] = translate_article_to_language(verified_en, eng_name)
                    except Exception as te2:
                        print(f"   ❌ Failed {eng_name}: {te2}")
                        all_langs_ready = False
                        break

            # અડધું કન્ટેન્ટ ક્યારેય પબ્લિશ નહીં થાય
            if not all_langs_ready:
                print("⏭️ બધી ભાષા પૂરી ન બની હોવાથી આ પોસ્ટ સ્કિપ કરી.")
                continue

            # Step 4: તમારા ફોટા સાથે 1200x675 HD થમ્બનેલ
            slug_id = hashlib.md5(art["url"].encode("utf-8")).hexdigest()[:10]
            thumb_url = create_custom_thumbnail(
                verified_en.get("thumbnail_headline", verified_en["seo_title"][:55]),
                verified_en.get("category_badge", "MARKET NEWS"),
                slug_id
            )

            # Step 5: ફાઇનલ HTML અને બ્લોગર પબ્લિશ
            post_html = build_complete_post_html(verified_en, translations, thumb_url, art["source"])
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
                history["daily_counts"][today_key] = posted_today
                save_history(history)
                time.sleep(5)

        except Exception as e:
            print(f"❌ Error processing article: {e}")
            continue

    print(f"\n🎉 Run finished! આ રનમાં કુલ {published_count} પોસ્ટ પબ્લિશ થઈ (આજની કુલ: {posted_today}).")


if __name__ == "__main__":
    main()
