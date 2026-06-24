from __future__ import annotations

from datetime import datetime
from html import escape
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
    page_title="KRX300 매수/매도 시그널",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="collapsed",
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


def parse_signal_date(date_text: str):
    return datetime.strptime(date_text, "%Y%m%d").date()


def clean_display_text(value: object, fallback: str = "-") -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    return text or fallback


def filter_stocks(
    stock_df: pd.DataFrame,
    query: str,
    market_name: str,
    industry_name: str,
) -> pd.DataFrame:
    filtered_df = stock_df.copy()
    keyword = query.strip().casefold()

    if keyword:
        name_matches = (
            filtered_df["stock_name"]
            .fillna("")
            .astype(str)
            .str.casefold()
            .str.contains(keyword, regex=False)
        )
        code_matches = (
            filtered_df["stock_code"]
            .fillna("")
            .astype(str)
            .str.contains(keyword, regex=False)
        )
        filtered_df = filtered_df[name_matches | code_matches]

    if market_name != "전체":
        filtered_df = filtered_df[filtered_df["market_name"].eq(market_name)]

    if industry_name != "전체":
        filtered_df = filtered_df[filtered_df["industry_name"].eq(industry_name)]

    return filtered_df.sort_values("display_order").reset_index(drop=True)


def render_stock_filters(stock_df: pd.DataFrame) -> tuple[str, str, str]:
    with st.container(key="stock_filters"):
        search_column, market_column, industry_column = st.columns(
            [2.6, 1.15, 1.45],
            vertical_alignment="bottom",
        )
        with search_column:
            query = st.text_input(
                "종목 검색",
                placeholder="종목명 또는 6자리 종목코드",
                icon=":material/search:",
                key="stock_search",
            )
        with market_column:
            market_name = st.selectbox(
                "시장",
                ["전체", "KOSPI", "KOSDAQ"],
                key="market_filter",
            )

        market_df = stock_df
        if market_name != "전체":
            market_df = stock_df[stock_df["market_name"].eq(market_name)]

        industry_options = ["전체"] + sorted(
            value
            for value in market_df["industry_name"].dropna().astype(str).str.strip().unique()
            if value
        )
        if st.session_state.get("industry_filter") not in industry_options:
            st.session_state["industry_filter"] = "전체"

        with industry_column:
            industry_name = st.selectbox(
                "업종",
                industry_options,
                key="industry_filter",
            )

    return query, market_name, industry_name


def render_stock_detail(stock: dict | None) -> None:
    if stock is None:
        st.markdown(
            """
            <div class="detail-empty-state">
                <span class="material-symbols-rounded" aria-hidden="true">touch_app</span>
                <strong>종목을 선택해 주세요.</strong>
                <p>왼쪽 목록에서 종목을 선택하면 상세 의견이 표시됩니다.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    status = stock["status"]
    ui = STATUS_UI[status]
    market_industry = " · ".join(
        value
        for value in [
            clean_display_text(stock.get("market_name"), ""),
            clean_display_text(stock.get("industry_name"), ""),
        ]
        if value.strip()
    )
    stock_name = escape(clean_display_text(stock.get("stock_name")))
    stock_code = escape(clean_display_text(stock.get("stock_code")))
    status_text = escape(status)

    st.markdown(
        f"""
        <div class="opinion-detail detail-panel-content">
            <div class="detail-panel-kicker">STOCK OPINION</div>
            <div class="opinion-detail-heading">
                <strong>{stock_name}</strong>
                <span class="opinion-badge {ui['badge']}">{status_text}</span>
            </div>
            <div style="color:#666b73;font-size:0.88rem;margin-top:0.25rem;">
                {stock_code}{' · ' + escape(market_industry) if market_industry else ''}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if status in {STATUS_BUY, STATUS_SELL}:
        st.markdown('<div class="detail-section-label">종합 판단</div>', unsafe_allow_html=True)
        st.markdown(clean_display_text(stock.get("summary"), "요약이 없습니다."))
    elif status == STATUS_ERROR:
        st.error(clean_display_text(stock.get("parse_error"), "의견 생성 과정에서 오류가 발생했습니다."))
        raw_response = clean_display_text(stock.get("raw_response"), "")
        if raw_response:
            with st.expander("미완성 모델 응답 확인"):
                st.text(raw_response)
    else:
        st.warning("선택한 기준일에 생성된 종목 의견이 없습니다.")


def select_stock(stock_code: str) -> None:
    st.session_state["selected_stock_code"] = stock_code


def render_grid(page_df: pd.DataFrame, signal_date: str, columns_per_row: int = 4) -> None:
    for start in range(0, len(page_df), columns_per_row):
        row_df = page_df.iloc[start : start + columns_per_row]
        columns = st.columns(columns_per_row)
        for column, (_, stock) in zip(columns, row_df.iterrows()):
            status = stock["status"]
            ui = STATUS_UI[status]
            code = stock["stock_code"]
            selected = st.session_state.get("selected_stock_code") == code
            selected_key = "_selected" if selected else ""
            with column:
                st.button(
                    stock["stock_name"],
                    key=f"tile_{ui['key']}{selected_key}_{signal_date}_{code}",
                    icon=ui["icon"],
                    width="stretch",
                    on_click=select_stock,
                    args=(code,),
                )


load_css()

try:
    dates = available_dates()
    if not dates:
        st.warning("조회 가능한 종목 의견 데이터가 없습니다.")
        st.stop()

    available_date_map = {
        parse_signal_date(date_text): date_text
        for date_text in dates
    }
    available_date_values = sorted(available_date_map)

    with st.container(key="product_header"):
        title_column, calendar_column = st.columns([4.7, 1.3], vertical_alignment="center")
        with title_column:
            st.markdown(
                """
                <div class="product-heading">
                    <div class="product-mark" aria-hidden="true">DSO</div>
                    <div>
                        <div class="product-kicker">DAILY STOCK OPINION</div>
                        <h1>KRX300 Signal Board</h1>
                        <p>종목별 단기 매수·매도 의견과 핵심 판단 근거</p>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with calendar_column:
            selected_calendar_date = st.date_input(
                "기준일",
                value=available_date_values[-1],
                min_value=available_date_values[0],
                max_value=available_date_values[-1],
            )

    signal_date = available_date_map.get(selected_calendar_date)
    if signal_date is None:
        st.warning(
            f"{selected_calendar_date:%Y-%m-%d}에 해당하는 의견 DB가 없습니다. "
            "조회 가능한 날짜를 선택해 주세요."
        )
        st.stop()

    stock_df = load_stocks(signal_date)
    query, market_name, industry_name = render_stock_filters(stock_df)
    filtered_df = filter_stocks(
        stock_df,
        query=query,
        market_name=market_name,
        industry_name=industry_name,
    )

    visible_codes = set(filtered_df["stock_code"].astype(str))
    selected_code = st.session_state.get("selected_stock_code")
    if visible_codes and selected_code not in visible_codes:
        selected_code = str(filtered_df.iloc[0]["stock_code"])
        st.session_state["selected_stock_code"] = selected_code

    list_column, detail_column = st.columns([4, 2], gap="large")
    with list_column:
        st.markdown(
            """
            <div class="universe-heading">
                <span>KRX300 UNIVERSE</span>
                <span class="universe-heading-line"></span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if filtered_df.empty:
            st.markdown(
                """
                <div class="empty-filter-result">
                    <span class="material-symbols-rounded" aria-hidden="true">search_off</span>
                    <strong>조건에 맞는 종목이 없습니다.</strong>
                    <p>검색어나 시장·업종 조건을 조정해 주세요.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            render_grid(filtered_df, signal_date)

    with detail_column:
        with st.container(key="stock_detail_panel"):
            selected_rows = stock_df[stock_df["stock_code"].astype(str).eq(str(selected_code))]
            selected_stock = None if selected_rows.empty else selected_rows.iloc[0].to_dict()
            render_stock_detail(selected_stock)
    st.markdown(
        """
        <footer class="product-footer">
            본 화면의 의견은 생성형 AI가 제공된 데이터만으로 작성한 참고 정보이며 투자 권유가 아닙니다.
        </footer>
        """,
        unsafe_allow_html=True,
    )

except Exception as exc:
    st.error(str(exc))
    st.info("SQLite 경로 또는 DATABASE_URL 설정을 확인해 주세요.")
