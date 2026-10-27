"""
กฎของ baseline
1. ถ้า query มี keyword ทุกคำ และมีอย่างน้อย 1 คำอยู่ใน title => relevant
2. ถ้า query มี keyword ทุกคำ แต่ไม่มีคำไหนอยู่ใน title => not relevant\
3. ถ้า query มี keyword ไม่ครบทุกคำ => not relevant
วิธีใช้:
    python scripts/baseline.py --labels data/ground_truth.csv --topics T01-T05  # ไม่ใส่ --topics = ทุกหัวข้อ

    เนื้อหาเต็มอ่านจาก data/raw/Txx.json
"""

import argparse
import csv
import json
import re
from pathlib import Path

RAW_DIR = Path("data/raw")
OUT_DIR = Path("results/filtering")
POS, NEG = "relevant", "not relevant"

STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "at", "by", "with"}


# ตัวอักษร/ตัวเลข และให้มีขีดกลางคั่นได้
WORD_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*"


def read_table(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


def keywords_from_query(query: str) -> list[str]:
    query = query.lower()
    words = re.findall(WORD_PATTERN, query)

    keywords = []
    for word in words:
        if word in STOPWORDS:
            continue
        keywords.append(word)

    return keywords

# หา Raw Text จากไฟล์ JSON ในโฟลเดอร์ data/raw
def parse_topic_ids(spec: str) -> set[str]:
    ids = set()
    for part in spec.upper().split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            ids.update(f"T{n:02d}" for n in range(int(a[1:]), int(b[1:]) + 1))
        elif part:
            ids.add(part)
    return ids

def load_full_texts() -> dict[str, str]:
    """api_news_id -> เนื้อหาเต็มจากไฟล์ raw JSON"""
    texts = {}
    for path in RAW_DIR.glob("T*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        for item in record["response"].get("news", []):
            texts[str(item.get("id"))] = item.get("text") or ""
    return texts

def contains(text: str, keyword: str) -> bool:
    # ให้ขีดกลางใน keyword ตรงกับ "-", ช่องว่าง หรือไม่มีอะไรเลย
    # เช่น co-pilot ตรงกับ co-pilot / co pilot / copilot
    escaped = re.escape(keyword)
    flexible = escaped.replace(r"\-", r"[-\s]?")

    # \b = จับเป็นคำทั้งคำ ไม่ให้ไปเจอเป็นส่วนหนึ่งของคำอื่น
    pattern = r"\b" + flexible + r"\b"
    match = re.search(pattern, text)

    return match is not None


def keyword_baseline(query: str, title: str, body: str) -> tuple[str, str]:
    keywords = keywords_from_query(query)
    title_lower = title.lower()
    all_lower = (title + " " + body).lower()

    missing = []    # keyword ที่ไม่เจอ
    in_title = []   # keyword ที่เจอ

    for keyword in keywords:
        if not contains(all_lower, keyword):
            missing.append(keyword)
        if contains(title_lower, keyword):
            in_title.append(keyword)

    if missing:
        return NEG, f"ไม่พบคำ: {missing}"

    if not in_title:
        return NEG, "ไม่มี keyword ในบทความ"

    return POS, f"มี keyword ครบทุกคำ: {in_title}"

def outcome(label: str, pred: str) -> str:
    if pred == POS:
        return "TP" if label == POS else "FP"
    return "FN" if label == POS else "TN"

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, help="ไฟล์ label (.csv หรือ .tsv)")
    ap.add_argument("--topics", help="เช่น T01-T05 (ไม่ใส่ = ทุกหัวข้อที่มี final_label)")
    args = ap.parse_args()

    rows = read_table(Path(args.labels))
    if args.topics:
        wanted = parse_topic_ids(args.topics)
        rows = [r for r in rows if r["topic_id"].strip().upper() in wanted]

    full_texts = load_full_texts()

    results = []

    for r in rows:
        label = r.get("final_label", "")
        body = full_texts.get(str(r.get("api_news_id", "")).strip())
        pred, why = keyword_baseline(r["query"], r["title"], body)
        results.append({
                    "topic_id": r["topic_id"], "difficulty": r["difficulty"], "article_no": r["article_no"],
                    "source": r["source"], "title": r["title"], "final_label": label,
                    "baseline_pred": pred, "outcome": outcome(label, pred), "baseline_reason": why,
                })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "baseline_pred.csv"
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

if __name__ == "__main__":
    main()