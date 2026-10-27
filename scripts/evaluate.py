"""
evaluate.py
วัดผล Gemini เทียบกับ ground truth (final_label) และเทียบกับ baseline (keyword matching)

ขั้นตอน:
1. อ่านไฟล์ label + เนื้อข่าวเต็มจาก data/raw/Txx.json (จับคู่ด้วย api_news_id)
2. ให้ Gemini ตัดสินทีละข่าวด้วย topic_description + title + ข่าวเต็ม
   ผลบันทึกที่ results/filtering/gemini_pred_<MODEL>.csv ทุกข่าว ถ้ารันใหม่ด้วยโมเดลเดิมจะข้ามข่าวที่มีผลแล้ว (ไม่เสีย token ซ้ำ)
   เปลี่ยน MODEL ใน secrets.toml = ได้ไฟล์ผลชุดใหม่แยกกัน ใช้เทียบหลายโมเดลได้
3. รวมกับ results/filtering/baseline_pred.csv (จาก baseline.py) แล้วคิด Precision / Recall / F1
   รวมทั้งหมด แยกตามหัวข้อ และแยกตาม difficulty

วิธีใช้ (รันจากโฟลเดอร์ project):
  python scripts/baseline.py --labels data/ground_truth.csv
  python scripts/evaluate.py --labels data/ground_truth.csv
  python scripts/evaluate.py --labels data/ground_truth.csv --topics T16-T20 --no-gemini   # คิดคะแนนอย่างเดียว ไม่เรียก API

ผลลัพธ์:
  results/filtering/gemini_pred_<MODEL>.csv   คำตอบของ Gemini รายข่าว
  results/filtering/comparison_<MODEL>.csv    เฉลย / baseline / gemini รายข่าว (ไว้ดูข่าวที่ตอบผิด)
  results/filtering/metrics_<MODEL>.csv       คะแนนทุกกลุ่ม
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

# fetch_dataset.py และ relevance.py ย้ายไปอยู่ใน src/ (ตัวหลักของเว็บ)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fetch_dataset import clean_text
from baseline import outcome, parse_topic_ids, read_table
from relevance import MODEL, NEG, POS, judge_relevance

RAW_DIR = Path("data/raw")
OUT_DIR = Path("results/filtering")
_MODEL_TAG = re.sub(r"[^A-Za-z0-9._-]+", "_", MODEL)
GEMINI_CSV = OUT_DIR / f"gemini_pred_{_MODEL_TAG}.csv"
BASELINE_CSV = OUT_DIR / "baseline_pred.csv"
COMPARISON_CSV = OUT_DIR / f"comparison_{_MODEL_TAG}.csv"
METRICS_CSV = OUT_DIR / f"metrics_{_MODEL_TAG}.csv"


def norm_label(value: str) -> str:
    """รวมรูปแบบที่พิมพ์ต่างกัน เช่น "Relevant", "not_relevant", " not relevant " """
    v = (value or "").strip().lower().replace("_", " ")
    return v if v in (POS, NEG) else ""


def load_full_texts() -> dict[str, str]:
    texts = {}
    for path in RAW_DIR.glob("T*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        for item in record["response"].get("news", []):
            texts[str(item.get("id"))] = clean_text(item.get("text") or "")
    return texts


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def run_gemini(rows: list[dict], full_texts: dict[str, str], delay: float) -> dict[str, dict]:
    """คืน dict article_no -> ผลของ Gemini และบันทึกลงไฟล์หลังได้ผลทุกข่าว"""
    cache = {}
    if GEMINI_CSV.exists():
        for r in read_table(GEMINI_CSV):
            cache[r["article_no"]] = r

    todo = [r for r in rows if not (r["article_no"] in cache
                                    and cache[r["article_no"]]["api_news_id"] == r["api_news_id"])]
    print(f"Gemini: มีผลแล้ว {len(rows) - len(todo)} ข่าว ต้องรันอีก {len(todo)} ข่าว")

    for i, r in enumerate(todo, 1):
        news_id = r["api_news_id"].strip()
        text = full_texts.get(news_id)
        if text is None:
            print(f"  [{r['article_no']}] ไม่พบเนื้อข่าวใน data/raw (api_news_id {news_id}) ข้าม")
            continue
        if i > 1:
            time.sleep(delay)
        try:
            label, reason = judge_relevance(r["topic_description"], r["title"], text)
        except Exception as e:
            print(f"  [{r['article_no']}] error: {e} ข้ามไปก่อน รันใหม่ภายหลังได้")
            continue
        print(f"  [{i}/{len(todo)}] {r['article_no']} -> {label}")
        cache[r["article_no"]] = {"article_no": r["article_no"], "api_news_id": news_id,
                                  "gemini_pred": label, "gemini_reason": reason, "model": MODEL}
        write_csv(GEMINI_CSV, sorted(cache.values(), key=lambda x: x["article_no"]))
    return cache


def score(pairs: list[tuple[str, str]]) -> dict:
    """pairs = [(final_label, pred), ...]"""
    counts = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    for label, pred in pairs:
        counts[outcome(label, pred)] += 1
    tp, fp, fn = counts["TP"], counts["FP"], counts["FN"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else (
        0.0 if precision is not None and recall is not None else None)
    return {**counts, "n": len(pairs), "precision": precision, "recall": recall, "f1": f1}


def fmt(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, help="ไฟล์ label (.csv) ที่มี final_label")
    ap.add_argument("--topics", help="เช่น T16-T20 (ไม่ใส่ = ทุกหัวข้อ)")
    ap.add_argument("--delay", type=float, default=4, help="วินาทีระหว่างการเรียก Gemini (default 4)")
    ap.add_argument("--no-gemini", action="store_true", help="ไม่เรียก Gemini ใช้ผลที่บันทึกไว้แล้ว")
    args = ap.parse_args()

    rows = read_table(Path(args.labels))
    if args.topics:
        wanted = parse_topic_ids(args.topics)
        rows = [r for r in rows if r["topic_id"].strip().upper() in wanted]
    rows = [r for r in rows if norm_label(r.get("final_label", ""))]
    if not rows:
        raise SystemExit("ไม่มีแถวที่มี final_label ในหัวข้อที่เลือก")

    if args.no_gemini:
        gemini = {r["article_no"]: r for r in read_table(GEMINI_CSV)} if GEMINI_CSV.exists() else {}
    else:
        gemini = run_gemini(rows, load_full_texts(), args.delay)

    baseline = {r["article_no"]: r for r in read_table(BASELINE_CSV)} if BASELINE_CSV.exists() else {}
    if not baseline:
        print(f"ไม่พบ {BASELINE_CSV} รัน baseline.py ก่อนถ้าต้องการเทียบ")

    comparison = []
    for r in rows:
        a = r["article_no"]
        comparison.append({
            "topic_id": r["topic_id"], "difficulty": r["difficulty"], "article_no": a, "title": r["title"],
            "final_label": norm_label(r["final_label"]),
            "baseline_pred": norm_label(baseline.get(a, {}).get("baseline_pred", "")),
            "gemini_pred": norm_label(gemini.get(a, {}).get("gemini_pred", "")),
            "gemini_reason": gemini.get(a, {}).get("gemini_reason", ""),
        })
    write_csv(COMPARISON_CSV, comparison)

    # คิดคะแนนเฉพาะข่าวที่ทั้งสองระบบมีคำตอบ เพื่อให้เทียบกันบนข่าวชุดเดียวกัน
    both = [c for c in comparison if c["baseline_pred"] and c["gemini_pred"]]
    skipped = len(comparison) - len(both)
    if skipped:
        print(f"\n⚠ ข้าม {skipped} ข่าวที่ยังไม่มีคำตอบจากระบบใดระบบหนึ่ง (ดู {COMPARISON_CSV})")
    if not both:
        raise SystemExit("ยังไม่มีข่าวที่ทั้งสองระบบตอบครบ")

    groups = [("ALL", both)]
    groups += [(f"topic {t}", [c for c in both if c["topic_id"] == t])
               for t in sorted({c["topic_id"] for c in both})]
    groups += [(f"difficulty {d}", [c for c in both if c["difficulty"] == d])
               for d in ("easy", "medium", "hard") if any(c["difficulty"] == d for c in both)]

    metrics = []
    print(f"\n{'group':<18}{'system':<10}{'n':>4}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}{'P':>7}{'R':>7}{'F1':>7}")
    for name, items in groups:
        for system in ("baseline", "gemini"):
            s = score([(c["final_label"], c[f"{system}_pred"]) for c in items])
            metrics.append({"group": name, "system": system, **s})
            print(f"{name:<18}{system:<10}{s['n']:>4}{s['TP']:>4}{s['FP']:>4}{s['FN']:>4}{s['TN']:>4}"
                  f"{fmt(s['precision']):>7}{fmt(s['recall']):>7}{fmt(s['f1']):>7}")
    write_csv(METRICS_CSV, metrics)
    print(f"\nบันทึก {METRICS_CSV} และ {COMPARISON_CSV}")


if __name__ == "__main__":
    main()
