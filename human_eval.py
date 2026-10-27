"""
human_eval.py
Human Evaluation ของผลสรุปจาก Gemini (Summary / Angle / Unique point) ให้คะแนน 1-5 ใน 3 ด้าน
completeness = ความครบถ้วน, accuracy = ความถูกต้อง, readability = ความเข้าใจง่าย

ขั้นตอน:
1. --build  เลือกข่าว relevant จาก ground truth หัวข้อละ 2 ข่าว (เติมให้ครบ 40 จากหัวข้อที่มีข่าวเยอะ)
            ให้ Gemini สรุปทีละหัวข้อด้วย compare_perspectives ตัวเดียวกับเว็บ แล้วแบ่งเป็น 2 ชุด ชุดละ 20 ข่าว
            ผู้ประเมิน 2 คนแรกได้ชุด A อีก 2 คนได้ชุด B (แต่ละข่าวมีคนให้คะแนน 2 คน)
2. ผู้ประเมินกรอกคะแนนในไฟล์ rater_<ชื่อ>.csv ของตัวเอง โดยเปิดข่าวต้นฉบับจาก url อ่านคู่กัน
   ความหมายของคะแนน 1-5 แต่ละด้านดูที่ data/human_eval/rubric.csv
3. --summarize  สรุปคะแนนเฉลี่ย ส่วนเบี่ยงเบน และความเห็นตรงกันระหว่างผู้ประเมิน 2 คน

วิธีใช้ (รันจากโฟลเดอร์ project):
  python human_eval.py --build --raters Man,Pete,Noeysod,Care
  python human_eval.py --summarize

ผลลัพธ์ (data/human_eval/):
  outputs.csv          ผลจาก Gemini ทุกข่าว (รัน --build ซ้ำจะใช้ไฟล์นี้ ไม่เรียก Gemini ใหม่)
  rater_<ชื่อ>.csv     ชีตให้คะแนนของแต่ละคน
  rubric.csv           เกณฑ์ให้คะแนน 1-5 ของแต่ละด้าน (เขียนเอง ไม่ได้สร้างจากสคริปต์)
  summary.csv          คะแนนสรุปรายด้าน
"""

import argparse
import csv
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from baseline import read_table
from fetch_dataset import clean_text
from news_comparator import compare_perspectives

GROUND_TRUTH = Path("data/ground_truth.csv")
TOPICS_CSV = Path("data/topics.csv")
RAW_DIR = Path("data/raw")
OUT_DIR = Path("data/human_eval")
OUTPUTS_CSV = OUT_DIR / "outputs.csv"
SUMMARY_CSV = OUT_DIR / "summary.csv"

TOTAL = 40
PER_TOPIC = 2
SEED = 42
DELAY = 4
CRITERIA = ["completeness", "accuracy", "readability"]
SHEET_FIELDS = ["eval_id", "group", "topic_id", "difficulty", "topic", "source", "title", "url",
                "summary", "angle", "unique_point", *CRITERIA, "comment"]


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_raw() -> dict[str, dict]:
    items = {}
    for path in RAW_DIR.glob("T*.json"):
        for item in json.loads(path.read_text(encoding="utf-8"))["response"].get("news", []):
            items[str(item.get("id"))] = item
    return items


def select_articles() -> list[dict]:
    """หัวข้อละ PER_TOPIC ข่าว ถ้ายังไม่ครบ TOTAL เติมจากหัวข้อที่เหลือข่าวมากที่สุดก่อน"""
    rng = random.Random(SEED)
    by_topic: dict[str, list[dict]] = {}
    for r in read_table(GROUND_TRUTH):
        if r["final_label"] == "relevant":
            by_topic.setdefault(r["topic_id"], []).append(r)
    for rows in by_topic.values():
        rng.shuffle(rows)

    picked = {t: rows[:PER_TOPIC] for t, rows in by_topic.items()}
    leftovers = sorted(by_topic, key=lambda t: len(by_topic[t]) - len(picked[t]), reverse=True)
    while sum(len(v) for v in picked.values()) < TOTAL and leftovers:
        t = leftovers.pop(0)
        if len(by_topic[t]) > len(picked[t]):
            picked[t].append(by_topic[t][len(picked[t])])
    return [r for t in sorted(picked) for r in picked[t]]


def generate_outputs(selected: list[dict]) -> list[dict]:
    done = {r["article_no"]: r for r in read_table(OUTPUTS_CSV)} if OUTPUTS_CSV.exists() else {}
    topics = {r["id"]: r for r in read_table(TOPICS_CSV)}
    raw = load_raw()

    todo: dict[str, list[dict]] = {}
    for r in selected:
        if r["article_no"] not in done:
            todo.setdefault(r["topic_id"], []).append(r)
    print(f"Gemini: มีผลแล้ว {len(selected) - sum(map(len, todo.values()))} ข่าว ต้องสรุปอีก {len(todo)} หัวข้อ")

    for i, (topic_id, rows) in enumerate(todo.items(), 1):
        if i > 1:
            time.sleep(DELAY)
        articles = [{**r, "text": clean_text(raw[r["api_news_id"]].get("text") or "")} for r in rows]
        results = compare_perspectives(topics[topic_id]["query"], articles)
        for res in results:
            done[res["article_no"]] = {k: res.get(k, "") for k in
                                       ("article_no", "summary", "angle", "unique_point")}
        missing = {r["article_no"] for r in rows} - {res["article_no"] for res in results}
        print(f"  [{i}/{len(todo)}] {topic_id}: สรุปได้ {len(results)}/{len(rows)} ข่าว"
              + (f" ขาด {sorted(missing)} รันใหม่ภายหลังได้" if missing else ""))
        write_csv(OUTPUTS_CSV, sorted(done.values(), key=lambda x: x["article_no"]),
                  ["article_no", "summary", "angle", "unique_point"])
    return [{**r, **done[r["article_no"]]} for r in selected if r["article_no"] in done]


def build(raters: list[str]) -> None:
    if len(raters) != 4:
        raise SystemExit("ต้องระบุผู้ประเมิน 4 คน เช่น --raters Man,Pete,Noeysod,Care")
    selected = select_articles()
    rows = generate_outputs(selected)
    if len(rows) < len(selected):
        raise SystemExit(f"ยังได้ผลจาก Gemini ไม่ครบ ({len(rows)}/{len(selected)}) รัน --build อีกครั้ง")

    # rows เรียงตามหัวข้อ สลับ A/B ทีละข่าวจึงได้กลุ่มละ 20 และหัวข้อที่มี 2 ข่าวขึ้นไปจะอยู่ทั้งสองกลุ่ม
    for i, r in enumerate(rows):
        r["group"] = "AB"[i % 2]
    rows.sort(key=lambda r: (r["group"], r["article_no"]))
    for i, r in enumerate(rows, 1):
        r["eval_id"] = f"E{i:02d}"
        r["topic"] = r["topic_description"]

    for rater, group in zip(raters, "AABB"):
        sheet = [r for r in rows if r["group"] == group]
        write_csv(OUT_DIR / f"rater_{rater}.csv", sheet, SHEET_FIELDS)
        print(f"สร้าง rater_{rater}.csv (กลุ่ม {group}, {len(sheet)} ข่าว)")


def mean_sd(values: list[int]) -> tuple[float, float]:
    return statistics.mean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def summarize() -> None:
    sheets = sorted(OUT_DIR.glob("*rater_*.csv"))
    if not sheets:
        raise SystemExit(f"ไม่พบไฟล์ rater_*.csv ใน {OUT_DIR} รัน --build ก่อน")

    scores: list[dict] = []
    for path in sheets:
        rater = path.stem.split("rater_")[-1]
        for r in read_table(path):
            for c in CRITERIA:
                value = r.get(c, "").strip()
                if not value:
                    continue
                if value not in {"1", "2", "3", "4", "5"}:
                    raise SystemExit(f"{path.name} {r['eval_id']} {c} = {value!r} ต้องเป็นเลข 1-5")
                scores.append({"rater": rater, "eval_id": r["eval_id"], "difficulty": r["difficulty"],
                               "title": r["title"], "criterion": c, "score": int(value)})
    if not scores:
        raise SystemExit("ยังไม่มีใครกรอกคะแนน")

    summary = []
    print(f"\n{'criterion':<14}{'n':>4}{'mean':>7}{'sd':>6}{'>=4':>7}   1/2/3/4/5")
    for c in CRITERIA:
        values = [s["score"] for s in scores if s["criterion"] == c]
        if not values:
            continue
        m, sd = mean_sd(values)
        high = sum(v >= 4 for v in values) / len(values)
        dist = "/".join(str(values.count(k)) for k in range(1, 6))
        summary.append({"criterion": c, "n": len(values), "mean": round(m, 2), "sd": round(sd, 2),
                        "pct_4_or_5": round(high, 2), "dist_1_to_5": dist})
        print(f"{c:<14}{len(values):>4}{m:>7.2f}{sd:>6.2f}{high:>7.0%}   {dist}")

    print("\nแยกตาม difficulty (เฉลี่ยทุกด้าน)")
    for d in ("easy", "medium", "hard"):
        values = [s["score"] for s in scores if s["difficulty"] == d]
        if values:
            print(f"  {d:<8}{statistics.mean(values):.2f}  (n={len(values)})")

    pairs: dict[tuple[str, str], list[int]] = {}
    for s in scores:
        pairs.setdefault((s["eval_id"], s["criterion"]), []).append(s["score"])
    diffs = [abs(v[0] - v[1]) for v in pairs.values() if len(v) == 2]
    if diffs:
        exact = sum(d == 0 for d in diffs) / len(diffs)
        within1 = sum(d <= 1 for d in diffs) / len(diffs)
        print(f"\nผู้ประเมิน 2 คนให้คะแนนตรงกัน {exact:.0%}, ต่างกันไม่เกิน 1 คะแนน {within1:.0%} (จาก {len(diffs)} คู่)")
        summary.append({"criterion": "agreement", "n": len(diffs), "mean": round(exact, 2),
                        "sd": "", "pct_4_or_5": round(within1, 2), "dist_1_to_5": "exact / within 1"})

    item_mean: dict[str, list[int]] = {}
    titles = {}
    for s in scores:
        item_mean.setdefault(s["eval_id"], []).append(s["score"])
        titles[s["eval_id"]] = s["title"]
    print("\nข่าวที่ได้คะแนนเฉลี่ยต่ำสุด 5 อันดับ (ไว้ดูเป็นกรณีล้มเหลว)")
    for eid, values in sorted(item_mean.items(), key=lambda kv: statistics.mean(kv[1]))[:5]:
        print(f"  {eid}  {statistics.mean(values):.2f}  {titles[eid][:70]}")

    write_csv(SUMMARY_CSV, summary, ["criterion", "n", "mean", "sd", "pct_4_or_5", "dist_1_to_5"])
    print(f"\nบันทึก {SUMMARY_CSV}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="สร้างผลจาก Gemini และชีตให้คะแนน")
    ap.add_argument("--raters", default="R1,R2,R3,R4", help="ชื่อผู้ประเมิน 4 คน คั่นด้วย ,")
    ap.add_argument("--summarize", action="store_true", help="สรุปคะแนนจากชีตที่กรอกแล้ว")
    args = ap.parse_args()

    if args.build:
        build([r.strip() for r in args.raters.split(",") if r.strip()])
    elif args.summarize:
        summarize()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
