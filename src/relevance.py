"""
relevance.py
ส่วนที่ใช้ร่วมกันระหว่าง news_comparator.py (ระบบจริง) และ evaluate.py (วัดผล)
- สร้าง Gemini client จาก environment variable
- เรียก Gemini โดยสุ่มสลับ key และลองใหม่อัตโนมัติเมื่อโดน rate limit
- judge_relevance: ให้ Gemini ตัดสินว่าบทความเกี่ยวข้องกับหัวข้อหรือไม่

ตั้ง key ในไฟล์ project/.streamlit/secrets.toml:
  GEMINI_API_KEY = "..."       (ใช้ key ตัวเดียว)
  WORLD_NEWS_API_KEY = "..."
  MODEL = "gemini-..."         (ไม่ใส่ = ใช้ DEFAULT_MODEL)
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

from google import genai
from google.genai import errors, types

# อ่านค่าจาก .streamlit/secrets.toml (ค่าใน environment จริงมาก่อน) ไม่ใช้ .env แล้ว
_HERE = Path(__file__).resolve().parent
_SECRETS = _HERE / ".streamlit" / "secrets.toml"
if _SECRETS.exists():
    import tomllib
    for _k, _v in tomllib.loads(_SECRETS.read_text(encoding="utf-8")).items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v.strip())

DEFAULT_MODEL = "gemini-3.1-flash-lite"
MODEL = (os.environ.get("MODEL") or DEFAULT_MODEL).strip()
# โมเดลสำรองเมื่อ MODEL หลักขึ้น 5xx ซ้ำ (ตั้งใน secrets.toml ด้วย FALLBACK_MODEL=... ว่าง = ไม่สลับ)
FALLBACK_MODEL = (os.environ.get("FALLBACK_MODEL") or "gemini-3.5-flash-lite").strip()
POS, NEG = "relevant", "not relevant"

RETRY_CODES = {429, 500, 502, 503, 504}
COOLDOWN = 60          # วินาทีที่พัก key ที่โดน 429 ก่อนสุ่มกลับมาใช้
MAX_ATTEMPTS = 10      # จำนวนครั้งสูงสุดที่ลองต่อ 1 request (รวมทุก key)

_clients: list[genai.Client] = []
_cooldown_until: list[float] = []


def get_clients() -> list[genai.Client]:
    if not _clients:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            sys.exit("GEMINI_API_KEY not found in .streamlit/secrets.toml or environment")
        _clients.append(genai.Client(api_key=key))
        _cooldown_until.append(0.0)
    return _clients


def _pick_key() -> int:
    """สุ่ม key จากตัวที่ไม่ได้พักอยู่ ถ้าพักหมดทุกตัว รอจนตัวแรกพักเสร็จ"""
    now = time.time()
    ready = [i for i, t in enumerate(_cooldown_until) if t <= now]
    if ready:
        return random.choice(ready)
    i = min(range(len(_cooldown_until)), key=_cooldown_until.__getitem__)
    wait = _cooldown_until[i] - now
    print(f"   ทุก key โดน rate limit รอ {wait:.0f} วินาที...")
    time.sleep(wait)
    return i


def generate(prompt: str, config: types.GenerateContentConfig | None = None) -> str:
    """เรียก Gemini ด้วย key ที่สุ่มมา ถ้า key นั้นโดน 429 จะพักมันไว้แล้วสลับไปใช้ key อื่นทันที"""
    clients = get_clients()
    model = MODEL
    overloaded = 0
    for attempt in range(MAX_ATTEMPTS):
        i = _pick_key()
        try:
            response = clients[i].models.generate_content(model=model, contents=prompt, config=config)
            return response.text or ""
        except errors.APIError as e:
            if e.code not in RETRY_CODES or attempt == MAX_ATTEMPTS - 1:
                raise
            if e.code == 429:
                _cooldown_until[i] = time.time() + COOLDOWN
                print(f"   key #{i + 1} โดน rate limit (429) พักไว้ {COOLDOWN} วินาที สลับไปใช้ key อื่น")
            else:
                # 5xx เป็นปัญหาฝั่งโมเดล (สลับ key ไม่ช่วย) ถ้าเจอซ้ำ 3 ครั้งให้ลองโมเดลสำรอง
                overloaded += 1
                if overloaded == 3 and FALLBACK_MODEL and model != FALLBACK_MODEL:
                    model = FALLBACK_MODEL
                    print(f"   {MODEL} ล่ม/โหลดเต็ม สลับไปใช้ {model}")
                wait = 5 * 2 ** min(attempt, 3)
                print(f"   Gemini error {e.code} รอ {wait} วินาทีแล้วลองใหม่...")
                time.sleep(wait)
    raise RuntimeError("unreachable")


JUDGE_CONFIG = types.GenerateContentConfig(
    temperature=0,
    response_mime_type="application/json",
    response_schema={
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": [POS, NEG]},
            "reason": {"type": "string"},
        },
        "required": ["label", "reason"],
    },
)


def judge_relevance(topic: str, title: str, text: str) -> tuple[str, str]:
    """คืน (label, reason) โดย label เป็น "relevant" หรือ "not relevant" """
    prompt = f"""You are judging whether a news article is relevant to a specific news topic.

Topic: {topic}

Rules:
- "{POS}" only if the article is mainly about this specific event, or covers it in substance.
- "{NEG}" if the article only mentions the event in passing (one or two sentences, a link,
  or a headline list), is about a different event, or only shares a keyword with the topic.
- Follow any exclusions stated in the topic.

Article title: {title}

Article text:
{text}

Answer with the label and a one-sentence reason."""

    raw = generate(prompt, JUDGE_CONFIG)
    data = json.loads(raw)
    label = data.get("label", "").strip().lower()
    if label not in (POS, NEG):
        raise ValueError(f"Gemini ตอบ label ไม่ถูกต้อง: {raw[:200]}")
    return label, data.get("reason", "").strip()
