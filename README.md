# News Perspective Comparator

ระบบเปรียบเทียบมุมมองข่าวจากหลายสำนัก — โปรเจกต์วิชา Introduction to Applied AI for IT

ผู้ใช้พิมพ์หัวข้อข่าวภาษาอังกฤษ ระบบจะค้นข่าวจากหลายสำนัก คัดข่าวที่ไม่เกี่ยวข้องออกด้วย Gemini
แล้วแสดงบทสรุป มุมมองการนำเสนอ ประเด็นเฉพาะ และ Tone ของแต่ละสำนักเรียงกัน ให้ผู้ใช้เปรียบเทียบได้เอง

- **Deployed app:** _(ใส่ URL ของ Streamlit Community Cloud)_
- **Repository:** https://github.com/RealTiraqmisu/Applied-AI

## ฟังก์ชันหลัก

1. รับหัวข้อข่าวภาษาอังกฤษจากผู้ใช้ (ตรวจ input: ว่าง / สั้นเกินไป / ไม่ใช่ภาษาอังกฤษ / ยาวเกิน 200 ตัวอักษร)
2. ค้นข่าวจากหลายสำนักผ่าน World News API และคัดข่าวที่ไม่เกี่ยวข้องออกด้วย Gemini
3. สรุปข่าว (Summary), มุมมองการนำเสนอ (Angle) และประเด็นเฉพาะ (Unique point) แยกรายบทความ
4. แสดง Tone เป็น Positive / Neutral / Negative จาก sentiment score ของ World News API
   (score > 0.1 = Positive, −0.1 ถึง 0.1 = Neutral, < −0.1 = Negative)
5. แสดงผลแต่ละสำนักเรียงกัน และ export เป็น Excel ได้

## การทำงานของระบบ

```
หัวข้อจากผู้ใช้ (Streamlit)
  → ตรวจ input
  → Gemini สกัดคำค้น (ไม่เกิน 5 คำ)
  → World News API ค้นข่าวภาษาอังกฤษย้อนหลัง 30 วัน สำนักละ 1 ข่าว สูงสุด 8 สำนัก
      (ถ้าไม่เจอข่าว ตัดคำท้ายของคำค้นทีละคำแล้วค้นใหม่)
  → Gemini ตัดสินทีละข่าวว่า relevant / not relevant (temperature 0, ตอบเป็น JSON)
  → Gemini สรุปข่าวที่เกี่ยวข้องทั้งหมดใน prompt เดียว (Summary / Angle / Unique point)
  → แปลง sentiment score เป็น Tone
  → แสดงการ์ดของแต่ละสำนัก + ปุ่ม Export to Excel
```

โมเดลที่ใช้: `gemini-3.1-flash-lite` (เปลี่ยนได้ด้วยค่า `MODEL` ใน secrets)
แผนภาพสถาปัตยกรรม: [docs/system-architecture.drawio](docs/system-architecture.drawio)

## โครงสร้างโปรเจกต์

```
Applied-AI/
├── requirements.txt
├── src/                          แอปที่ deploy
│   ├── app.py                    หน้าเว็บ Streamlit
│   ├── news_comparator.py        ค้นข่าว คัดกรอง สรุปมุมมอง แปลง sentiment เป็น Tone
│   ├── relevance.py              เรียก Gemini (ลองใหม่เมื่อ error) และ prompt คัดกรองข่าว
│   └── fetch_dataset.py          ดึงข่าวจาก World News API มาสร้าง dataset + ฟังก์ชันทำความสะอาดข้อความ
├── scripts/                      สคริปต์ประเมินผล
│   ├── baseline.py               baseline แบบ keyword matching
│   ├── evaluate.py               วัด Precision / Recall / F1 ของ Gemini เทียบ baseline
│   └── human_eval.py             สร้างชีตและสรุปผล Human Evaluation ของบทสรุป
├── notebooks/demov1.ipynb        ต้นแบบแรกของระบบ
├── data/                         ข้อมูล input
│   ├── topics.csv                20 หัวข้อข่าวที่ใช้สร้าง dataset
│   ├── raw/T01.json–T20.json     ข่าวดิบจาก World News API
│   ├── labels/                   ป้ายที่สมาชิกแต่ละคนติด (คนละ 5 หัวข้อ)
│   ├── ground_truth.csv          ป้ายรวม 190 บทความ
│   └── human_eval/
│       ├── rubric.csv            เกณฑ์ให้คะแนน 1–5
│       └── ratings/              คะแนนจากผู้ประเมินแต่ละคน
├── results/                      ผลที่สคริปต์สร้าง (ลบแล้วรันสร้างใหม่ได้)
│   ├── filtering/                ผลเทียบ baseline กับ Gemini
│   └── human_eval/               ผลสรุปของ Gemini ที่ใช้ประเมิน + คะแนนสรุป
└── docs/                         แผนภาพสถาปัตยกรรม และเนื้อหาสไลด์
```

## การติดตั้งและรัน

ต้องใช้ Python 3.11 ขึ้นไป

```bash
pip install -r requirements.txt
```

สร้างไฟล์ `src/.streamlit/secrets.toml`:

```toml
GEMINI_API_KEY = "your-gemini-api-key"
WORLD_NEWS_API_KEY = "your-world-news-api-key"
MODEL = "gemini-3.1-flash-lite"   # ไม่ใส่ก็ได้
```

รันแอปจากโฟลเดอร์ root ของโปรเจกต์:

```bash
streamlit run src/app.py
```

แล้วเปิด http://localhost:8501

## การประเมินผล

### 1. การคัดกรองข่าว (Gemini เทียบ baseline)

Ground truth: 190 บทความจาก 20 หัวข้อ (relevant 95 / not relevant 95) แบ่งความยาก easy 52 / medium 69 / hard 69
Baseline: ข่าวเป็น relevant เมื่อมีคำค้นครบทุกคำ และมีอย่างน้อย 1 คำในหัวข่าว

```bash
python scripts/baseline.py --labels data/ground_truth.csv
python scripts/evaluate.py --labels data/ground_truth.csv              # เรียก Gemini เฉพาะข่าวที่ยังไม่มีผล
python scripts/evaluate.py --labels data/ground_truth.csv --no-gemini  # คิดคะแนนจากผลที่บันทึกไว้
```

| ระบบ | Precision | Recall | F1 (micro) | F1 (macro รายหัวข้อ) |
|---|---|---|---|---|
| Baseline | 0.81 | 0.84 | 0.82 | 0.76 |
| Gemini 3.1 Flash-Lite | 0.91 | 0.74 | 0.81 | 0.73 |

ผลรายหัวข้อและรายข่าวอยู่ใน `results/filtering/`

### 2. Human Evaluation ของบทสรุป

40 บทความจากครบ 20 หัวข้อ สมาชิก 4 คนแบ่งเป็น 2 คู่ แต่ละบทความมีคนให้คะแนน 2 คนแยกกัน
ให้คะแนน 1–5 ตาม [data/human_eval/rubric.csv](data/human_eval/rubric.csv)

```bash
python scripts/human_eval.py --summarize
```

| เกณฑ์ | ค่าเฉลี่ย ± SD | ได้ 4–5 คะแนน |
|---|---|---|
| ความครบถ้วน | 3.74 ± 0.82 | 70% |
| ความถูกต้อง | 3.66 ± 0.79 | 69% |
| ความเข้าใจง่าย | 4.30 ± 0.70 | 92% |

> `python scripts/human_eval.py --build` จะสร้างชีตคะแนนใหม่ทับไฟล์ใน `data/human_eval/ratings/`
> ห้ามรันหลังจากผู้ประเมินกรอกคะแนนแล้ว

### สร้าง dataset ใหม่ (ไม่จำเป็นสำหรับการรันแอป)

```bash
python src/fetch_dataset.py --topics T01-T05   # ดึงข่าวตาม data/topics.csv เก็บที่ data/raw/
```

World News API แบบฟรีค้นข่าวย้อนหลังได้ประมาณ 30 วัน

## ข้อจำกัด

- เวลาตอบ 1.5–6 นาทีต่อการค้นหนึ่งครั้ง ขึ้นกับภาระของเซิร์ฟเวอร์ Gemini
- โมเดลไม่รู้วันที่ปัจจุบัน จึงตัดข่าวล่าสุดบางข่าวทิ้งเพราะคิดว่าเป็นเหตุการณ์ในอดีต (Recall 0.74)
- ค้นข่าวได้ย้อนหลังประมาณ 30 วัน และโควตา API แบบฟรีจำกัด
- ใช้เนื้อข่าว 3,000 ตัวอักษรแรกในการคัดกรองและสรุป ข่าวที่ยาวกว่านั้นจะอ่านไม่ครบ
- หลายสำนักลงข่าวจากต้นทางเดียวกัน (เช่น AP) ทำให้บทสรุปบางสำนักซ้ำกัน
- Ground truth แต่ละหัวข้อติดป้ายโดยสมาชิกคนเดียว และผู้ประเมินบทสรุปคือสมาชิกกลุ่มเอง
- Tone มาจาก sentiment score ของ World News API ซึ่งกลุ่มไม่ได้ตรวจสอบความถูกต้องแยก

## สมาชิก

| ชื่อ | รหัสนักศึกษา |
|---|---|
| นายกิตติพิชญ์ หิรัญวงศ์ | 66070018 |
| นายปณวัฒน์ ธรรมเจริญ | 66070113 |
| นางสาวอินทิรา ธนัพประภัศร์ | 66070228 |
| นางสาวปนัสยา บุญประกอบ | 66070286 |
