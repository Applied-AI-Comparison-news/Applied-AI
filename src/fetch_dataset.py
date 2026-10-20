"""
fetch_dataset.py
ดึงบทความจาก World News API ตามหัวข้อที่กำหนด เก็บ raw JSON ไว้ และสร้าง CSV สำหรับ label

วิธีใช้:
  pip install requests

  # ตั้ง API key (แต่ละคนใช้ key ของตัวเอง)
  macOS/Linux:  export WORLD_NEWS_API_KEY="your_key"
  Windows:      set WORLD_NEWS_API_KEY=your_key

  # ดึงเฉพาะหัวข้อที่ตัวเองรับผิดชอบ
  python fetch_dataset.py --topics T01-T05
  python fetch_dataset.py --topics T02,T07

  # หลังทุกคนดึงเสร็จและรวมโฟลเดอร์ data/raw แล้ว ให้สร้างไฟล์สำหรับ label
  python fetch_dataset.py --build-csv

หมายเหตุ:
  - ถ้ามีไฟล์ data/raw/Txx.json อยู่แล้ว สคริปต์จะข้าม (ไม่เปลือง API points)
    ใช้ --force ถ้าต้องการดึงใหม่
  - Free tier ค้นย้อนหลังได้ประมาณ 1 เดือน จึงควรดึงข้อมูลให้เสร็จเร็วที่สุด
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

API_URL = "https://api.worldnewsapi.com/search-news"
RAW_DIR = Path("data/raw")
LABEL_CSV = Path("data/labeling_sheet.csv")
TOPICS_CSV = Path("data/topics.csv")

NUM_RESULTS = 10      # ขอสูงสุด 10 บทความต่อหัวข้อ
MIN_RESULTS = 5       # ถ้าได้น้อยกว่านี้ จะเตือนให้ปรับ query
LOOKBACK_DAYS = 30    # free tier ย้อนหลังได้ประมาณ 1 เดือน

# difficulty:
#   easy   = query เฉพาะเจาะจง คาดว่าส่วนใหญ่จะ relevant
#   medium = มีโอกาสได้ข่าวเรื่องใกล้เคียงปนมา
#   hard   = query กว้าง/กำกวม/เหตุการณ์เกิดซ้ำ คาดว่าจะมี not relevant เยอะ
# seed_url: เติม URL ข่าวต้นทางเองสำหรับบางหัวข้อ เพื่อใช้ทดสอบ input แบบ URL ภายหลัง
TOPICS = [
    # ---------- World / Politics ----------
    {"id": "T01", "category": "World", "difficulty": "easy",
     "query": "Flydubai co-pilot",
     "description": "Flydubai flight FZ1073 (late Sep/early Oct 2026): co-pilot accused of attacking "
                    "the cockpit and trying to crash an Israel-bound plane; emergency landing in Saudi Arabia.",
     "seed_url": ""},
    {"id": "T02", "category": "World", "difficulty": "easy",
     "query": "North Korea missile",
     "description": "North Korea fires a ballistic missile into the East Sea (early Oct 2026). "
                    "Articles about other/earlier launches are NOT relevant.",
     "seed_url": ""},
    {"id": "T03", "category": "Health", "difficulty": "hard",
     "query": "Ebola",
     "description": "New Ebola cases recorded in rebel-held eastern Congo (early Oct 2026). "
                    "Other outbreaks/countries or general Ebola research are NOT relevant.",
     "seed_url": ""},
    {"id": "T04", "category": "Politics", "difficulty": "easy",
     "query": "Brazil presidential election",
     "description": "Brazil's 2026 presidential election campaign and what it means for policy "
                    "(e.g., the Amazon). Elections in other countries are NOT relevant.",
     "seed_url": ""},
    {"id": "T05", "category": "World", "difficulty": "easy",
     "query": "Dutch royal house colonial",
     "description": "Study finding the Dutch royal house was deeply involved in colonial exploitation (Oct 2026).",
     "seed_url": ""},

    # ---------- Energy / Economy ----------
    {"id": "T06", "category": "Energy", "difficulty": "medium",
     "query": "diesel reserves",
     "description": "G7/Europe agree to release about 100 million barrels of oil and diesel reserves "
                    "after US pressure (Oct 2026). Local fuel shortage stories (e.g., Texas) are NOT relevant.",
     "seed_url": ""},
    {"id": "T07", "category": "Energy", "difficulty": "medium",
     "query": "Texas fuel shortage",
     "description": "Texas declares a state of emergency over a fuel/diesel shortage (Oct 2026).",
     "seed_url": ""},
    {"id": "T08", "category": "World", "difficulty": "medium",
     "query": "Cuba fuel ship",
     "description": "US Coast Guard intercepts/seizes ships carrying fuel to Cuba (Oct 2026).",
     "seed_url": ""},
    {"id": "T09", "category": "Economy", "difficulty": "hard",
     "query": "September jobs report payrolls",
     "description": "US September 2026 jobs report: about 29,000 jobs added, unemployment 4.2%. "
                    "Other months or other countries' jobs data are NOT relevant.",
     "seed_url": ""},
    {"id": "T10", "category": "Business", "difficulty": "medium",
     "query": "Tesla deliveries",
     "description": "Tesla Q3 2026 vehicle deliveries beat expectations and the stock jumps (Oct 2026). "
                    "Earlier quarters are NOT relevant.",
     "seed_url": ""},

    # ---------- Technology ----------
    {"id": "T11", "category": "Technology", "difficulty": "easy",
     "query": "Anthropic IPO Thanksgiving",
     "description": "Anthropic targets an IPO as soon as mid-November, before Thanksgiving (Oct 2026). "
                    "Other Anthropic news (e.g., talent investment) is NOT relevant.",
     "seed_url": ""},
    {"id": "T12", "category": "Technology", "difficulty": "hard",
     "query": "Anthropic $100 million",
     "description": "Anthropic to invest $100 million to train AI engineering talent (Oct 2026). "
                    "Other Anthropic news (e.g., IPO plans) is NOT relevant.",
     "seed_url": ""},
    {"id": "T13", "category": "Technology", "difficulty": "medium",
     "query": "OpenAI hack",
     "description": "OpenAI reveals its AI agents were involved in hacking Australian (and US) "
                    "government agency websites (Oct 2026).",
     "seed_url": ""},
    {"id": "T14", "category": "Technology", "difficulty": "hard",
     "query": "Nvidia stock record high",
     "description": "Nvidia stock hits a record high (around Oct 2, 2026). "
                    "Other Nvidia product/partnership news is NOT relevant.",
     "seed_url": ""},

    # ---------- Sports ----------
    {"id": "T15", "category": "Sports", "difficulty": "hard",
     "query": "Obiena",
     "description": "EJ Obiena wins his second straight Asian Games pole vault gold (Sep 2026). "
                    "Other Asian Games events/medals are NOT relevant.",
     "seed_url": ""},
    {"id": "T16", "category": "Sports", "difficulty": "hard",
     "query": "Lynx",
     "description": "New York Liberty sweep the top-seeded Minnesota Lynx in the WNBA playoffs (late Sep 2026). "
                    "Anything else named 'Lynx' is NOT relevant.",
     "seed_url": ""},
    {"id": "T17", "category": "Sports", "difficulty": "easy",
     "query": "Protect College Sports Act",
     "description": "US Senate passes the Protect College Sports Act, 77-22 (Sep 2026).",
     "seed_url": ""},
    {"id": "T18", "category": "Sports", "difficulty": "easy",
     "query": "Tony Romo CBS",
     "description": "CBS Sports parts ways with broadcaster Tony Romo (Oct 2026).",
     "seed_url": ""},

    # ---------- Science / Environment ----------
    {"id": "T19", "category": "Science", "difficulty": "medium",
     "query": "penguin species",
     "description": "Genetic study identifies a new gentoo penguin species on the Kerguelen Islands (Sep 2026).",
     "seed_url": ""},
    {"id": "T20", "category": "Environment", "difficulty": "easy",
     "query": "Lake Powell water level",
     "description": "Lake Powell falls to its lowest level on record after a dry, warm winter (Sep 2026).",
     "seed_url": ""},
]

TOPIC_BY_ID = {t["id"]: t for t in TOPICS}


def parse_topic_ids(spec: str) -> list[str]:
    """รับ 'T01-T05' หรือ 'T01,T03,T07' แล้วคืนรายการ id"""
    ids = []
    for part in spec.split(","):
        part = part.strip().upper()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-")
            for n in range(int(start[1:]), int(end[1:]) + 1):
                ids.append(f"T{n:02d}")
        else:
            ids.append(part)
    unknown = [i for i in ids if i not in TOPIC_BY_ID]
    if unknown:
        sys.exit(f"ไม่รู้จักหัวข้อ: {unknown}")
    return ids


def fetch_topic(topic: dict, api_key: str, force: bool = False) -> None:
    out_path = RAW_DIR / f"{topic['id']}.json"
    if out_path.exists() and not force:
        print(f"[{topic['id']}] มีไฟล์อยู่แล้ว ข้าม (ใช้ --force เพื่อดึงใหม่)")
        return

    earliest = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "text": topic["query"],
        "language": "en",
        "earliest-publish-date": earliest,
        "number": NUM_RESULTS,
    }

    try:
        resp = requests.get(API_URL, params=params, headers={"x-api-key": api_key}, timeout=30)
    except requests.RequestException as e:
        print(f"[{topic['id']}] เชื่อมต่อไม่ได้: {e}")
        return

    # แสดงโควต้าที่เหลือ (ถ้า API ส่ง header มา)
    quota = {h: resp.headers.get(h) for h in ("X-API-Quota-Request", "X-API-Quota-Used", "X-API-Quota-Left")}
    quota = {k: v for k, v in quota.items() if v is not None}

    if resp.status_code == 401:
        sys.exit("API key ไม่ถูกต้อง (401)")
    if resp.status_code in (402, 429):
        sys.exit(f"โควต้าหมดหรือเรียกถี่เกินไป ({resp.status_code}) ลองใหม่พรุ่งนี้หรือใช้ key ของเพื่อน")
    if resp.status_code != 200:
        print(f"[{topic['id']}] error {resp.status_code}: {resp.text[:200]}")
        return

    data = resp.json()
    news = data.get("news", [])

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "topic": topic,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "response": data,
    }
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    msg = f"[{topic['id']}] ได้ {len(news)} บทความ -> {out_path}"
    if quota:
        msg += f"  quota: {quota}"
    print(msg)
    if len(news) < MIN_RESULTS:
        print(f"   ⚠ ได้น้อยกว่า {MIN_RESULTS} บทความ ลองปรับ query ให้กว้างขึ้นแล้วรันใหม่ด้วย --force")


def source_from_url(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host

#แอบเพิ่ม
def rot47(s: str) -> str:
    return "".join(chr(33 + (ord(c) - 33 + 47) % 94) if 33 <= ord(c) <= 126 else c for c in s)


def clean_text(text: str) -> str:
    # บางเว็บ (เช่น stltoday.com) ซ่อนเนื้อข่าวด้วย ROT47 โดย <p> กลายเป็น kAm และ </p> กลายเป็น k^Am
    if text.lstrip().startswith("kAm") or "k^Am" in text:
        text = html.unescape(re.sub(r"<[^>]+>", "", rot47(text)))
    return text.replace("\n", " ").strip()


def build_csv() -> None:
    rows = []
    for topic in TOPICS:
        path = RAW_DIR / f"{topic['id']}.json"
        if not path.exists():
            print(f"[{topic['id']}] ยังไม่มีไฟล์ raw ข้าม")
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        seen_urls = set()
        for i, item in enumerate(record["response"].get("news", []), start=1):
            url = item.get("url", "")
            # ตัด query string ออกก่อนเทียบ เพราะบางเว็บส่งบทความเดียวกันมาสองครั้ง เช่น ...?cmt=1
            url_key = url.split("?")[0].split("#")[0]
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            authors = item.get("authors") or ([item["author"]] if item.get("author") else [])
            text = clean_text(item.get("text") or "")
            rows.append({
                "topic_id": topic["id"],
                "category": topic["category"],
                "difficulty": topic["difficulty"],
                "topic_description": topic["description"],
                "query": topic["query"],
                "article_no": f"{topic['id']}-{i:02d}",
                "api_news_id": item.get("id", ""),
                "source": source_from_url(url),
                "title": html.unescape(item.get("title", "")),
                "url": url,
                "publish_date": item.get("publish_date", ""),
                "authors": "; ".join(a for a in authors if a),
                "sentiment": item.get("sentiment", ""),
                "text_preview": text[:400],
                "label_A": "",
                "label_B": "",
                "final_label": "",
                "note": "",
            })

    if not rows:
        sys.exit("ไม่มีข้อมูลให้สร้าง CSV ลองดึงข้อมูลก่อน")

    LABEL_CSV.parent.mkdir(parents=True, exist_ok=True)
    with LABEL_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with TOPICS_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "category", "difficulty", "query", "description", "seed_url"])
        writer.writeheader()
        writer.writerows(TOPICS)

    per_topic = {}
    for r in rows:
        per_topic[r["topic_id"]] = per_topic.get(r["topic_id"], 0) + 1
    print(f"สร้าง {LABEL_CSV} แล้ว: {len(rows)} บทความ จาก {len(per_topic)} หัวข้อ")
    for tid, n in per_topic.items():
        flag = "  ⚠ น้อยเกินไป" if n < MIN_RESULTS else ""
        print(f"  {tid}: {n}{flag}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ground-truth dataset from World News API")
    parser.add_argument("--topics", help="เช่น T01-T05 หรือ T01,T03 (ไม่ใส่ = ทุกหัวข้อ)")
    parser.add_argument("--force", action="store_true", help="ดึงใหม่แม้มีไฟล์อยู่แล้ว")
    parser.add_argument("--build-csv", action="store_true", help="รวม raw JSON เป็น labeling CSV")
    args = parser.parse_args()

    if args.build_csv:
        build_csv()
        return

    api_key = os.environ.get("WORLD_NEWS_API_KEY")
    if not api_key:
        sys.exit("กรุณาตั้งค่า WORLD_NEWS_API_KEY ก่อน")

    ids = parse_topic_ids(args.topics) if args.topics else [t["id"] for t in TOPICS]
    for tid in ids:
        fetch_topic(TOPIC_BY_ID[tid], api_key, force=args.force)
        time.sleep(1.2)  # เว้นระยะเล็กน้อยกัน rate limit


if __name__ == "__main__":
    main()
