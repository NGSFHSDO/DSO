from __future__ import annotations

from datetime import datetime
from math import ceil
from pathlib import Path

import pandas as pd
import streamlit as st

from data_access import (
    STATUS_BUY,
    STATUS_ERROR,
    STATUS_MISSING,
    STATUS_SELL,
    get_repository,
)


APP_DIR = Path(__file__).resolve().parent
STATUS_OPTIONS = [STATUS_BUY, STATUS_SELL, STATUS_ERROR, STATUS_MISSING]
STATUS_UI = {
    STATUS_BUY: {
        "icon": ":material/trending_up:",
        "key": "buy",
        "badge": "buy",
    },
    STATUS_SELL: {
        "icon": ":material/trending_down:",
        "key": "sell",
        "badge": "sell",
    },
    STATUS_ERROR: {
        "icon": ":material/error:",
        "key": "error",
        "badge": "error",
    },
    STATUS_MISSING: {
        "icon": ":material/remove:",
        "key": "missing",
        "badge": "missing",
    },
}


st.set_page_config(
    page_title="KRX300 종목 의견",
    page_icon=":material/monitoring:",
    layout="wide",
)


def load_css() -> None:
    css_path = APP_DIR / "styles.css"
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def repository():
    return get_repository()


@st.cache_data(show_spinner=False, ttl=60)
def available_dates() -> list[str]:
    return repository().available_dates()


@st.cache_data(show_spinner=False, ttl=60)
def load_stocks(signal_date: str) -> pd.DataFrame:
    return repository().load_stocks(signal_date)


def select_stock(stock_code: str) -> None:
    st.session_state["selected_stock_code"] = stock_code


def format_date(date_text: str) -> str:
    if len(date_text) != 8:
        return date_text
    return f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}"


def parse_signal_date(date_text: str):
    return datetime.strptime(date_text, "%Y%m%d").date()


def render_detail(stock_df: pd.DataFrame) -> None:
    selected_code = st.session_state.get("selected_stock_code")
    if not selected_code:
        st.info("종목 아이콘을 선택하면 매수·매도 의견과 종합 요약을 확인할 수 있습니다.")
        return

    selected = stock_df[stock_df["stock_code"].eq(selected_code)]
    if selected.empty:
        st.session_state.pop("selected_stock_code", None)
        return

    row = selected.iloc[0]
    status = row["status"]
    ui = STATUS_UI[status]
    market_industry = " · ".join(
        value
        for value in [str(row.get("market_name") or "").strip(), str(row.get("industry_name") or "").strip()]
        if value
    )

    st.markdown(
        f"""
        <div class="opinion-detail">
            <div><strong>{int(row['display_order'])}. {row['stock_name']}</strong>
            <span class="opinion-badge {ui['badge']}">{status}</span></div>
            <div style="color:#666b73;font-size:0.88rem;margin-top:0.25rem;">
                {row['stock_code']}{' · ' + market_industry if market_industry else ''}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if status in {STATUS_BUY, STATUS_SELL}:
        st.markdown(str(row.get("summary") or "요약이 없습니다."))
        c1, c2 = st.columns(2)
        c1.caption(f"모델: {row.get('model_name') or '-'}")
        c2.caption(f"생성 시각: {row.get('created_at') or '-'}")
    elif status == STATUS_ERROR:
        st.error(str(row.get("parse_error") or "의견 생성 과정에서 오류가 발생했습니다."))
        raw_response = str(row.get("raw_response") or "").strip()
        if raw_response:
            with st.expander("미완성 모델 응답 확인"):
                st.text(raw_response)
    else:
        st.warning("선택한 기준일에 생성된 종목 의견이 없습니다.")


def filter_stocks(
    df: pd.DataFrame,
    query: str,
    statuses: list[str],
    market_names: list[str],
) -> pd.DataFrame:
    result = df[df["status"].isin(statuses)].copy()
    if query.strip():
        keyword = query.strip().lower()
        result = result[
            result["stock_name"].astype(str).str.lower().str.contains(keyword, regex=False)
            | result["stock_code"].astype(str).str.contains(keyword, regex=False)
        ]
    if market_names:
        result = result[result["market_name"].isin(market_names)]
    return result.sort_values("display_order").reset_index(drop=True)


def render_grid(page_df: pd.DataFrame, signal_date: str, columns_per_row: int = 6) -> None:
    for start in range(0, len(page_df), columns_per_row):
        row_df = page_df.iloc[start : start + columns_per_row]
        columns = st.columns(columns_per_row)
        for column, (_, stock) in zip(columns, row_df.iterrows()):
            status = stock["status"]
            ui = STATUS_UI[status]
            code = stock["stock_code"]
            label = f"{int(stock['display_order'])}. {stock['stock_name']} · {status}"
            with column:
                st.button(
                    label,
                    key=f"tile_{ui['key']}_{signal_date}_{code}",
                    icon=ui["icon"],
                    width="stretch",
                    help=f"{stock['stock_name']} ({code}) 상세 보기",
                    on_click=select_stock,
                    args=(code,),
                )


load_css()

st.title("KRX300 종목별 매수·매도 의견")
st.caption("KRX300 원래 편입 순서를 유지하며, 종목 아이콘을 선택하면 생성된 의견과 근거를 확인할 수 있습니다.")

try:
    repo = repository()
    dates = available_dates()
    if not dates:
        st.warning("조회 가능한 종목 의견 데이터가 없습니다.")
        st.stop()

    available_date_map = {
        parse_signal_date(date_text): date_text
        for date_text in dates
    }
    available_date_values = sorted(available_date_map)

    c1, c2 = st.columns([1, 3])
    with c1:
        selected_calendar_date = st.date_input(
            "기준일",
            value=available_date_values[-1],
            min_value=available_date_values[0],
            max_value=available_date_values[-1],
        )
    with c2:
        st.caption(f"데이터 저장소: {repo.backend_label}")
        st.caption(
            "조회 가능한 날짜: "
            + ", ".join(format_date(date_text) for date_text in sorted(dates, reverse=True))
        )

    signal_date = available_date_map.get(selected_calendar_date)
    if signal_date is None:
        st.warning(
            f"{selected_calendar_date:%Y-%m-%d}에 해당하는 의견 DB가 없습니다. "
            "조회 가능한 날짜를 선택해 주세요."
        )
        st.stop()

    stock_df = load_stocks(signal_date)
    counts = stock_df["status"].value_counts()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("전체", f"{len(stock_df):,}")
    m2.metric("매수", f"{int(counts.get(STATUS_BUY, 0)):,}")
    m3.metric("매도", f"{int(counts.get(STATUS_SELL, 0)):,}")
    m4.metric("미분석", f"{int(counts.get(STATUS_MISSING, 0)):,}")
    m5.metric("생성 실패", f"{int(counts.get(STATUS_ERROR, 0)):,}")

    st.subheader("종목 상세")
    render_detail(stock_df)

    st.subheader("종목 목록")
    f1, f2, f3, f4 = st.columns([1.4, 2.2, 1.7, 1.2])
    query = f1.text_input(
        "검색",
        placeholder="종목명 또는 종목코드",
        icon=":material/search:",
    )
    statuses = f2.multiselect(
        "의견 상태",
        options=STATUS_OPTIONS,
        default=STATUS_OPTIONS,
    )
    market_options = sorted(value for value in stock_df["market_name"].dropna().unique() if str(value).strip())
    markets = f3.multiselect("시장", options=market_options, default=market_options)
    page_size = f4.selectbox("페이지당 종목", options=[60, 120, 297], index=0)

    filtered_df = filter_stocks(stock_df, query, statuses, markets)
    total_pages = max(1, ceil(len(filtered_df) / page_size))
    page_number = st.number_input(
        "페이지",
        min_value=1,
        max_value=total_pages,
        value=1,
        step=1,
    )
    start = (int(page_number) - 1) * page_size
    page_df = filtered_df.iloc[start : start + page_size]

    st.caption(f"검색 결과 {len(filtered_df):,}개 · {int(page_number)}/{total_pages} 페이지")
    if page_df.empty:
        st.info("조건에 맞는 종목이 없습니다.")
    else:
        render_grid(page_df, signal_date)

except Exception as exc:
    st.error(str(exc))
    st.info("SQLite 경로 또는 DATABASE_URL 설정을 확인해 주세요.")
