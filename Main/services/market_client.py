from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 분석 대상 업종 (KRX/KIND 표준산업분류 업종명, FinanceDataReader "KRX-DESC"의 Industry 컬럼)
# 코스닥의 "자연과학 및 공학 연구개발업"은 신약개발·바이오텍·CRO가 대부분이라 포함한다.
DEFAULT_BIO_INDUSTRIES: tuple[str, ...] = (
    "의약품 제조업",
    "기초 의약물질 제조업",
    "의료용품 및 기타 의약 관련제품 제조업",
    "자연과학 및 공학 연구개발업",
)


def get_target_industries() -> tuple[str, ...]:
    """BIO_INDUSTRIES 환경변수(쉼표 구분)로 덮어쓸 수 있다. 예: 의료기기까지 포함할 때."""
    raw = os.getenv("BIO_INDUSTRIES", "").strip()
    if not raw:
        return DEFAULT_BIO_INDUSTRIES
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class MarketCompany:
    rank: int
    ticker_code: str
    company_name: str
    market: str
    department: str
    market_cap_krw: int
    industry: str = ""


class MarketClient:
    def get_kosdaq_top_by_market_cap(self, limit: int = 50) -> list[MarketCompany]:
        """코스닥 바이오·제약 업종 기업 중 시가총액 상위 limit개를 반환한다.

        업종 정보를 불러오지 못하면 비바이오 기업이 섞이지 않도록 빈 목록을 반환한다.
        """
        industries = get_target_industries()
        industry_by_code = self._load_industry_by_code(industries)
        if not industry_by_code:
            logger.error(
                "[MarketClient] industry listing unavailable — refusing to load unfiltered companies"
            )
            return []

        try:
            import FinanceDataReader as fdr

            try:
                df = fdr.StockListing("KOSDAQ")
            except Exception as exc:
                logger.warning("[MarketClient] direct KOSDAQ listing failed, trying KRX: %s", exc)
                df = fdr.StockListing("KRX")
                if "Market" in df.columns:
                    df = df[df["Market"].astype(str).str.upper().str.startswith("KOSDAQ")]
        except Exception as exc:
            logger.error("[MarketClient] KOSDAQ listing load failed: %s", exc)
            return self._get_kosdaq_top_by_pykrx(limit, industry_by_code)

        required = {"Code", "Name", "Market", "Marcap"}
        missing = required - set(df.columns)
        if missing:
            logger.error("[MarketClient] KOSDAQ listing missing columns: %s", sorted(missing))
            return []

        df = df.copy()
        df["Code"] = df["Code"].astype(str).str.zfill(6)
        total_listed = len(df)
        df = df[df["Code"].isin(industry_by_code)]
        df["Marcap"] = df["Marcap"].fillna(0).astype("int64")
        df = df.sort_values("Marcap", ascending=False).head(limit)

        companies: list[MarketCompany] = []
        for rank, (_, row) in enumerate(df.iterrows(), start=1):
            code = row["Code"]
            companies.append(
                MarketCompany(
                    rank=rank,
                    ticker_code=code,
                    company_name=str(row["Name"]).strip(),
                    market=str(row.get("Market", "KOSDAQ")).strip(),
                    department=str(row.get("Dept", "")).strip(),
                    market_cap_krw=int(row["Marcap"]),
                    industry=industry_by_code[code],
                )
            )

        logger.info(
            "[MarketClient] loaded KOSDAQ bio/pharma top %d by market cap (%d bio/pharma of %d listed)",
            len(companies),
            len(industry_by_code),
            total_listed,
        )
        return companies

    def _load_industry_by_code(self, industries: tuple[str, ...]) -> dict[str, str]:
        """대상 업종에 속한 코스닥 종목코드 → 업종명. 실패 시 빈 dict."""
        try:
            import FinanceDataReader as fdr

            # "KOSDAQ-DESC"는 KOSDAQ GLOBAL 종목(알테오젠 등)이 빠지므로 KRX-DESC를 쓴다.
            desc = fdr.StockListing("KRX-DESC")
        except Exception as exc:
            logger.error("[MarketClient] KRX-DESC industry listing load failed: %s", exc)
            return {}

        if not {"Code", "Market", "Industry"} <= set(desc.columns):
            logger.error("[MarketClient] KRX-DESC missing columns: %s", list(desc.columns))
            return {}

        desc = desc[desc["Market"].astype(str).str.upper().str.startswith("KOSDAQ")]
        desc = desc[desc["Industry"].astype(str).str.strip().isin(industries)]
        return {
            str(code).zfill(6): str(industry).strip()
            for code, industry in zip(desc["Code"], desc["Industry"])
        }

    def _get_kosdaq_top_by_pykrx(
        self, limit: int, industry_by_code: dict[str, str]
    ) -> list[MarketCompany]:
        try:
            from pykrx import stock

            df = None
            used_date = ""
            for offset in range(0, 30):
                date = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
                candidate = stock.get_market_cap_by_ticker(date, market="KOSDAQ")
                if candidate is not None and not candidate.empty:
                    df = candidate
                    used_date = date
                    break

            if df is None or df.empty:
                logger.error("[MarketClient] pykrx KOSDAQ market cap data is empty")
                return []

            if "시가총액" not in df.columns:
                logger.error("[MarketClient] pykrx data missing market cap column: %s", list(df.columns))
                return []

            df = df.copy()
            df.index = df.index.astype(str).str.zfill(6)
            df = df[df.index.isin(industry_by_code)]
            df["시가총액"] = df["시가총액"].fillna(0).astype("int64")
            df = df.sort_values("시가총액", ascending=False).head(limit)

            companies: list[MarketCompany] = []
            for rank, (ticker_code, row) in enumerate(df.iterrows(), start=1):
                companies.append(
                    MarketCompany(
                        rank=rank,
                        ticker_code=ticker_code,
                        company_name=str(stock.get_market_ticker_name(ticker_code)).strip(),
                        market="KOSDAQ",
                        department="",
                        market_cap_krw=int(row["시가총액"]),
                        industry=industry_by_code[ticker_code],
                    )
                )

            logger.info(
                "[MarketClient] loaded KOSDAQ bio/pharma top %d by pykrx market cap date=%s",
                len(companies),
                used_date,
            )
            return companies
        except Exception as exc:
            logger.error("[MarketClient] pykrx KOSDAQ listing load failed: %s", exc)
            return []


_market_client: MarketClient | None = None


def get_market_client() -> MarketClient:
    global _market_client
    if _market_client is None:
        _market_client = MarketClient()
    return _market_client
