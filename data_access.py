from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Protocol

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_UNIVERSE_PATH = PROJECT_ROOT / "data" / "KRX300.xlsx"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results"

STATUS_BUY = "매수"
STATUS_SELL = "매도"
STATUS_ERROR = "생성 실패"
STATUS_MISSING = "미분석"
VALID_OPINIONS = {STATUS_BUY, STATUS_SELL}


def normalize_stock_code(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def read_universe_excel(path: Path = DEFAULT_UNIVERSE_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"KRX300 파일을 찾지 못했습니다: {path}")

    df = pd.read_excel(path, dtype={"종목코드": str, "종목명": str})
    required = {"종목코드", "종목명"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"KRX300 파일에 필요한 열이 없습니다: {sorted(missing)}")

    universe = pd.DataFrame(
        {
            "stock_code": df["종목코드"].map(normalize_stock_code),
            "stock_name": df["종목명"].fillna("").astype(str).str.strip(),
            "market_name": df.get("시장구분", pd.Series("", index=df.index)).fillna(""),
            "industry_name": df.get("업종명", pd.Series("", index=df.index)).fillna(""),
        }
    )
    universe = universe[universe["stock_code"].ne("")].drop_duplicates("stock_code")
    universe.insert(0, "display_order", range(1, len(universe) + 1))
    return universe.reset_index(drop=True)


def classify_status(row: pd.Series) -> str:
    opinion_value = row.get("opinion")
    error_value = row.get("parse_error")
    opinion = "" if opinion_value is None or pd.isna(opinion_value) else str(opinion_value).strip()
    parse_error = "" if error_value is None or pd.isna(error_value) else str(error_value).strip()
    if opinion in VALID_OPINIONS:
        return opinion
    if parse_error:
        return STATUS_ERROR
    return STATUS_MISSING


def merge_universe_and_opinions(
    universe: pd.DataFrame,
    opinions: pd.DataFrame,
    signal_date: str,
) -> pd.DataFrame:
    opinion_columns = [
        "signal_date",
        "stock_code",
        "opinion",
        "summary",
        "raw_response",
        "parse_error",
        "model_name",
        "created_at",
    ]
    opinions = opinions.reindex(columns=opinion_columns).copy()
    if not opinions.empty:
        opinions["stock_code"] = opinions["stock_code"].map(normalize_stock_code)
        opinions = opinions.drop_duplicates("stock_code", keep="last")

    result = universe.merge(opinions, on="stock_code", how="left")
    result["signal_date"] = result["signal_date"].fillna(str(signal_date))
    result["status"] = result.apply(classify_status, axis=1)
    return result.sort_values("display_order").reset_index(drop=True)


class OpinionRepository(Protocol):
    backend_label: str

    def available_dates(self) -> list[str]: ...

    def load_stocks(self, signal_date: str) -> pd.DataFrame: ...


class SQLiteOpinionRepository:
    backend_label = "Local SQLite"

    def __init__(
        self,
        universe_path: Path = DEFAULT_UNIVERSE_PATH,
        results_dir: Path = DEFAULT_RESULTS_DIR,
    ) -> None:
        self.universe_path = Path(universe_path)
        self.results_dir = Path(results_dir)

    def db_path(self, signal_date: str) -> Path:
        return self.results_dir / f"stock_opinions_{signal_date}.sqlite"

    def available_dates(self) -> list[str]:
        dates = []
        for path in self.results_dir.glob("stock_opinions_*.sqlite"):
            date_text = path.stem.removeprefix("stock_opinions_")
            if len(date_text) == 8 and date_text.isdigit():
                dates.append(date_text)
        return sorted(set(dates), reverse=True)

    def load_opinions(self, signal_date: str) -> pd.DataFrame:
        path = self.db_path(signal_date)
        columns = [
            "signal_date",
            "stock_code",
            "opinion",
            "summary",
            "raw_response",
            "parse_error",
            "model_name",
            "created_at",
        ]
        if not path.exists():
            return pd.DataFrame(columns=columns)

        with sqlite3.connect(path) as conn:
            table = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'stock_llm_opinions'
                """
            ).fetchone()
            if table is None:
                return pd.DataFrame(columns=columns)

            df = pd.read_sql(
                """
                SELECT
                    signal_date,
                    "종목코드" AS stock_code,
                    opinion,
                    summary,
                    raw_response,
                    parse_error,
                    model_name,
                    created_at
                FROM stock_llm_opinions
                WHERE signal_date = ?
                """,
                conn,
                params=(str(signal_date),),
            )
        return df.reindex(columns=columns)

    def load_stocks(self, signal_date: str) -> pd.DataFrame:
        universe = read_universe_excel(self.universe_path)
        opinions = self.load_opinions(signal_date)
        return merge_universe_and_opinions(universe, opinions, signal_date)


class RemoteDatabaseOpinionRepository:
    backend_label = "Remote Database"

    def __init__(self, database_url: str) -> None:
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise ImportError(
                "원격 DB를 사용하려면 sqlalchemy와 해당 DB 드라이버를 설치해 주세요."
            ) from exc
        self.engine = create_engine(database_url, pool_pre_ping=True)

    def available_dates(self) -> list[str]:
        from sqlalchemy import text

        query = text("SELECT DISTINCT signal_date FROM stock_opinions ORDER BY signal_date DESC")
        with self.engine.connect() as conn:
            df = pd.read_sql(query, conn)
        return df["signal_date"].astype(str).tolist()

    def load_stocks(self, signal_date: str) -> pd.DataFrame:
        from sqlalchemy import text

        universe_query = text("""
            SELECT
                display_order,
                stock_code,
                stock_name,
                COALESCE(market_name, '') AS market_name,
                COALESCE(industry_name, '') AS industry_name
            FROM stock_universe
            WHERE active = TRUE
            ORDER BY display_order
        """)
        opinion_query = text("""
            SELECT
                signal_date,
                stock_code,
                opinion,
                summary,
                raw_response,
                parse_error,
                model_name,
                created_at
            FROM stock_opinions
            WHERE signal_date = :signal_date
        """)
        with self.engine.connect() as conn:
            universe = pd.read_sql(universe_query, conn)
            opinions = pd.read_sql(
                opinion_query,
                conn,
                params={"signal_date": str(signal_date)},
            )
        universe["stock_code"] = universe["stock_code"].map(normalize_stock_code)
        return merge_universe_and_opinions(universe, opinions, signal_date)


def get_repository() -> OpinionRepository:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return RemoteDatabaseOpinionRepository(database_url)

    results_dir = Path(os.getenv("OPINION_RESULTS_DIR", DEFAULT_RESULTS_DIR))
    universe_path = Path(os.getenv("KRX300_PATH", DEFAULT_UNIVERSE_PATH))
    return SQLiteOpinionRepository(
        universe_path=universe_path,
        results_dir=results_dir,
    )
