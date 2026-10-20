"""
app.py
หน้าเว็บ Streamlit สำหรับ News Perspective Comparator
รัน: streamlit run app.py
"""

import html
import io
import os
import re

import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:
    pass

import news_comparator as nc  # noqa: E402

MAX_RESULTS = 10
DAYS_BACK = 30
SENTIMENT_COLORS = {"positive": "#4ade80", "negative": "#f87171", "neutral": "#facc15"}

# (หัวคอลัมน์, key ใน result, ความกว้าง)
EXCEL_COLUMNS = [
    ("Source", "source", 22),
    ("Country", "country", 10),
    ("Title", "title", 50),
    ("URL", "url", 40),
    ("Sentiment", "sentiment", 12),
    ("Summary", "summary", 60),
    ("Angle", "angle", 50),
    ("Unique point", "unique_point", 50),
]

st.set_page_config(page_title="News Perspective Comparator", layout="wide")
st.title("News Perspective Comparator")


def build_excel(keyword: str, results: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Comparison"
    ws.append([f"Topic: {keyword}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([name for name, _, _ in EXCEL_COLUMNS])
    for cell in ws[2]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9D9D9")
    for r in results:
        ws.append([r.get(key, "") for _, key, _ in EXCEL_COLUMNS])
        fill = SENTIMENT_COLORS.get(r.get("sentiment", ""))
        if fill:
            sentiment_cell = ws.cell(row=ws.max_row, column=5)
            sentiment_cell.fill = PatternFill("solid", fgColor=fill.lstrip("#"))
    for i, (_, _, width) in enumerate(EXCEL_COLUMNS, 1):
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = width
    for row in ws.iter_rows(min_row=3):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A3"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@st.cache_data(show_spinner=False, ttl=3600)
def get_keyword(text: str) -> str:
    return nc.clean_keyword(text)


@st.cache_data(show_spinner=False, ttl=3600)
def get_articles(keyword: str) -> list[dict]:
    return nc.fetch_news(keyword, MAX_RESULTS, DAYS_BACK)


text = st.text_input("Enter a news topic you are interested in", placeholder="e.g. Brydon Carse charged over nightclub incident")

if st.button("Analyze", type="primary") and text.strip():
    text = text.strip()
    st.session_state.pop("analysis", None)
    bar = st.progress(0.0, text="Processing, please wait")
    try:
        keyword = get_keyword(text)
        bar.progress(0.1, text="Processing, please wait")

        articles = get_articles(keyword)
        bar.progress(0.2, text="Processing, please wait")
        if not articles:
            bar.empty()
            st.warning("No news found. Try a different keyword.")
            st.stop()

        def on_progress(i, total, a, label, reason):
            bar.progress(0.2 + 0.65 * i / total, text="Processing, please wait")

        relevant = nc.filter_relevant(text, articles, on_progress)
        if not relevant:
            bar.empty()
            st.warning("No relevant news found. Try a different keyword.")
            st.stop()

        results = nc.compare_perspectives(keyword, relevant)
        bar.progress(1.0, text="Done")
        bar.empty()
    except nc.errors.APIError as err:
        bar.empty()
        st.error(f"Gemini returned error {err.code} (the server is busy or temporarily down). Please try again in a moment.")
        st.stop()
    except (RuntimeError, SystemExit) as err:
        bar.empty()
        st.error(f"Something went wrong: {err}")
        st.stop()

    # เก็บผลไว้ใน session_state เพราะการกดปุ่ม download ทำให้สคริปต์รันใหม่ ปุ่ม Analyze จะกลับเป็น False
    st.session_state["analysis"] = {"keyword": keyword, "results": results}

analysis = st.session_state.get("analysis")
if analysis:
    keyword, results = analysis["keyword"], analysis["results"]
    slug = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_") or "result"

    st.subheader(f"Topic: {keyword}")
    head_left, head_right = st.columns([3, 1], vertical_alignment="center")
    head_left.write(f"Found perspectives from {len(results)} sources")
    head_right.download_button(
        "Export to Excel",
        data=build_excel(keyword, results),
        file_name=f"{slug}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    cols = st.columns(2)
    for i, r in enumerate(results):
        with cols[i % 2].container(border=True):
            sentiment = r.get("sentiment", "neutral")
            bg = SENTIMENT_COLORS.get(sentiment, SENTIMENT_COLORS["neutral"])
            st.markdown(
                f"<div style='display:flex; justify-content:space-between; align-items:center; gap:12px;'>"
                f"<h3 style='margin:0; padding:0;'>{html.escape(r['source'])}</h3>"
                f"<span style='background:{bg}; color:#000; border-radius:6px; padding:2px 10px; "
                f"font-size:13px; font-weight:600; white-space:nowrap;'>{html.escape(sentiment)}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(f"[{r['title']}]({r['url']})")
            for label, key in (("Summary", "summary"), ("Angle", "angle"), ("Unique point", "unique_point")):
                st.markdown(
                    f"<div style='background:rgba(128,128,128,0.12); border-radius:8px; margin-bottom:10px;"
                    f"padding:10px 14px; margin-top:8px;'>"
                    f"<div style='font-size:12px; font-weight:700; letter-spacing:0.06em; "
                    f"text-transform:uppercase; opacity:0.65; margin-bottom:4px;'>{label}</div>"
                    f"<div>{html.escape(r.get(key, ''))}</div></div>",
                    unsafe_allow_html=True,
                )
