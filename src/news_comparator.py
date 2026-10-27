import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from google.genai import errors, types

from fetch_dataset import API_URL, clean_text, source_from_url
from relevance import POS, generate, judge_relevance

OUT_DIR = Path("results/comparator")
COMPARE_CHARS = 3000
JUDGE_CHARS = 3000
JUDGE_DELAY = 2
DAYS_BACK = 30
SENTIMENT_THRESHOLD = 0.1
WORLD_NEWS_ERRORS = {
    401: "World News API key is invalid. Please check WORLD_NEWS_API_KEY.",
    402: "World News API daily quota is used up. Please try again tomorrow.",
    429: "Too many requests to World News API. Please wait a minute and try again.",
}

def sentiment_label(score) -> str:
    if not isinstance(score, (int, float)):
        return "neutral"
    if score > SENTIMENT_THRESHOLD:
        return "positive"
    if score < -SENTIMENT_THRESHOLD:
        return "negative"
    return "neutral"


def clean_keyword(user_input: str) -> str:
    prompt = f"""Convert this text into a short news search query (maximum 5 words, English only).
- Copy words exactly as written in the text. Never merge or split words
  (keep "rock star" as two words, never "rockstar"; keep "co-pilot" as is).
- Keep the words that make this story specific (names, places, the unusual detail, e.g. "biological"),
  and drop only filler words such as "man", "learns", "is", "over".
Return ONLY the search query, nothing else.

Text: {user_input}"""
    return generate(prompt).strip()


def fetch_news(keyword: str, max_results: int = 10, days_back: int = DAYS_BACK) -> list[dict]:
    api_key = os.environ.get("WORLD_NEWS_API_KEY")
    if not api_key:
        raise RuntimeError("WORLD_NEWS_API_KEY not found in secrets.toml or environment")

    params = {
        "text": keyword,
        "language": "en",
        "number": max_results,
        "earliest-publish-date": (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d"),
    }
    try:
        res = requests.get(API_URL, params=params, headers={"x-api-key": api_key}, timeout=30)
    except requests.Timeout:
        raise RuntimeError("World News API did not respond in 30 seconds. Please try again.") from None
    except requests.RequestException:
        raise RuntimeError("Cannot connect to World News API. Please check your internet connection.") from None
    if res.status_code != 200:
        raise RuntimeError(WORLD_NEWS_ERRORS.get(
            res.status_code, f"World News API error {res.status_code}: {res.text[:200]}"))

    query_words = set(re.findall(r"[a-z0-9]+", keyword.lower()))
    best: dict[str, tuple[int, dict]] = {}
    for item in res.json().get("news", []):
        url = item.get("url", "")
        source = source_from_url(url)
        article = {
            "source": source,
            "country": item.get("source_country", ""),
            "title": html.unescape(item.get("title", "")),
            "text": clean_text(item.get("text") or ""),
            "url": url,
            "sentiment_score": item.get("sentiment"),
            "sentiment": sentiment_label(item.get("sentiment")),
        }
        score = len(query_words & set(re.findall(r"[a-z0-9]+", article["title"].lower())))
        if source not in best or score > best[source][0]:
            best[source] = (score, article)
    articles = [a for _, a in best.values()]
    print(f"ดึงข่าวได้ {len(articles)} สำนัก")
    return articles


def search_variants(keyword: str, user_input: str) -> list[str]:
    words = keyword.split()
    variants = [keyword]
    while len(words) > 2:
        words = words[:-1]
        variants.append(" ".join(words))
    variants.append(user_input.strip())
    seen = set()
    return [v for v in variants if v and not (v.lower() in seen or seen.add(v.lower()))]


def find_articles(keyword: str, user_input: str, max_results: int = 10, days_back: int = DAYS_BACK) -> tuple[list[dict], str]:
    for query in search_variants(keyword, user_input):
        articles = fetch_news(query, max_results, days_back)
        if articles:
            return articles, query
        print(f"  ไม่พบข่าวด้วย '{query}' ลอง query ถัดไป...")
    return [], keyword


def filter_relevant(topic: str, articles: list[dict], on_progress=None) -> list[dict]:
    kept = []
    for i, a in enumerate(articles):
        if i:
            time.sleep(JUDGE_DELAY)
        label, reason = judge_relevance(topic, a["title"], a["text"][:JUDGE_CHARS])
        mark = "✓" if label == POS else "✗"
        print(f"  {mark} [{a['source']}] {a['title'][:70]}  ({reason})")
        if on_progress:
            on_progress(i + 1, len(articles), a, label, reason)
        if label == POS:
            kept.append(a)
    print(f"เกี่ยวข้อง {len(kept)} จาก {len(articles)} ข่าว")
    return kept


COMPARE_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema={
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "article": {"type": "integer"},
                "summary": {"type": "string"},
                "angle": {"type": "string"},
                "unique_point": {"type": "string"},
            },
            "required": ["article", "summary", "angle", "unique_point"],
        },
    },
)


def compare_perspectives(keyword: str, articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += f"""
--- Article {i} ---
Source: {a['source']}
Title: {a['title']}
Content: {a['text'][:COMPARE_CHARS]}
"""

    prompt = f"""You are analyzing news articles about the same topic from different sources.
Topic: "{keyword}"
{articles_text}
For each article, return an object with:
- "article": the article number
- "summary": 2-sentence summary in English
- "angle": what angle or framing this source emphasizes (1 sentence)
- "unique_point": one thing this source mentions that others don't"""

    try:
        results = json.loads(generate(prompt, COMPARE_CONFIG))
    except json.JSONDecodeError:
        raise RuntimeError("Gemini returned the summaries in an unexpected format. Please click Analyze again.") from None
    merged = []
    for r in results:
        n = r.get("article")
        if isinstance(n, int) and 1 <= n <= len(articles):
            merged.append({**articles[n - 1], **r})
    return merged


SENTIMENT_COLORS = {"positive": "#d4edda", "negative": "#f8d7da", "neutral": "#fff3cd"}


def save_html(results: list[dict], keyword: str) -> Path:
    e = html.escape
    parts = [f"<meta charset='utf-8'><h2>Topic: {e(keyword)}</h2>",
             f"<p>Found perspectives from {len(results)} sources</p>"]
    for r in results:
        color = SENTIMENT_COLORS.get(r.get("sentiment", ""), "#f0f0f0")
        parts.append(f"""
        <div style="border:1px solid #ddd; border-radius:8px; padding:16px; margin:12px 0; background:{color}; color:#000;">
          <h3 style="margin:0 0 4px;">{e(r['source'])} ({e(r['country'])})</h3>
          <p style="margin:0 0 8px"><a href="{e(r['url'])}" target="_blank" style="color:#000;">{e(r['title'])}</a></p>
          <p><b>Summary:</b> {e(r.get('summary', ''))}</p>
          <p><b>Angle:</b> {e(r.get('angle', ''))}</p>
          <p><b>Unique point:</b> {e(r.get('unique_point', ''))}</p>
          <span style="background:#fff; border-radius:4px; padding:2px 8px; font-size:12px">Sentiment: {e(r.get('sentiment', '?'))}</span>
        </div>""")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_") or "result"
    out_path = OUT_DIR / f"{slug}.html"
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def run_pipeline(input_text: str, max_results: int = 10, days_back: int = DAYS_BACK) -> None:
    print("Step 1: หา keyword...")
    keyword = clean_keyword(input_text)
    print(f"keyword: {keyword}")
    topic = input_text

    print("\nStep 2: ดึงข่าว...")
    articles, keyword = find_articles(keyword, input_text, max_results, days_back)
    if not articles:
        print("ไม่พบข่าว ลองเปลี่ยน keyword")
        return

    print("\nStep 3: คัดกรองข่าวที่เกี่ยวข้อง...")
    articles = filter_relevant(topic, articles)
    if not articles:
        print("ไม่มีข่าวที่เกี่ยวข้อง ลองเปลี่ยน keyword")
        return

    print("\nStep 4: เปรียบเทียบมุมมอง...")
    results = compare_perspectives(keyword, articles)
    for r in results:
        print(f"  [{r['source']}] {r['sentiment']}: {r['angle']}")

    out_path = save_html(results, keyword)
    print(f"\nบันทึกผลที่ {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="News Perspective Comparator")
    ap.add_argument("text", help="keyword หรือข้อความอธิบายหัวข้อข่าว")
    ap.add_argument("--max", type=int, default=10, help="จำนวนข่าวสูงสุดที่ดึง (default 10)")
    ap.add_argument("--days", type=int, default=DAYS_BACK, help=f"ย้อนหลังกี่วัน (default {DAYS_BACK})")
    args = ap.parse_args()

    try:
        run_pipeline(args.text, max_results=args.max, days_back=args.days)
    except RuntimeError as err:
        sys.exit(str(err))


if __name__ == "__main__":
    main()
