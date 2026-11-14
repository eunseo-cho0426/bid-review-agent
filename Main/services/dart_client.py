from __future__ import annotations

import logging
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from xml.etree import ElementTree

import requests

from models.schemas import CashRunwayStatus, DisclosureData, DisclosureHit, FinancialData

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DartCompanyData:
    financial: FinancialData | None
    disclosure: DisclosureData
    financial_source: str
    disclosure_source: str


class DartClient:
    def __init__(self, timeout: int = 15) -> None:
        self._api_key = (
            os.getenv("DART_API_KEY", "")
            or os.getenv("OPEN_DART_API_KEY", "")
            or os.getenv("DART_KEY", "")
        ).strip()
        self._timeout = timeout
        self._corp_codes: dict[str, str] | None = None
        self._base_url = "https://opendart.fss.or.kr/api"

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def get_financial_data(
        self,
        ticker_code: str,
        fallback_data: FinancialData | None = None,
    ) -> FinancialData | None:
        company_data = self.get_company_data(ticker_code)
        return company_data.financial or fallback_data

    def get_company_data(self, ticker_code: str) -> DartCompanyData:
        corp_code = self.get_corp_code(ticker_code)
        if not corp_code:
            return DartCompanyData(
                financial=None,
                disclosure=DisclosureData(risk_keywords=[], data_errors=["DART corp_code unavailable"]),
                financial_source="DART corp_code unavailable",
                disclosure_source="DART corp_code unavailable",
            )

        financial, financial_source = self.get_latest_financial_data(corp_code)
        disclosure = self.get_recent_disclosure_data(corp_code)
        return DartCompanyData(
            financial=financial,
            disclosure=disclosure,
            financial_source=financial_source,
            disclosure_source="DART recent disclosures",
        )

    def get_corp_code(self, ticker_code: str) -> str | None:
        if not self.available:
            return None
        if self._corp_codes is None:
            self._corp_codes = self._load_corp_codes()
        return self._corp_codes.get(str(ticker_code).zfill(6))

    def _load_corp_codes(self) -> dict[str, str]:
        try:
            response = requests.get(
                f"{self._base_url}/corpCode.xml",
                params={"crtfc_key": self._api_key},
                timeout=self._timeout,
            )
            response.raise_for_status()
            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                xml_bytes = archive.read("CORPCODE.xml")
            root = ElementTree.fromstring(xml_bytes)
        except Exception as exc:
            logger.warning("[DART] corpCode load failed: %s", exc)
            return {}

        mapping: dict[str, str] = {}
        for item in root.findall("list"):
            corp_code = (item.findtext("corp_code") or "").strip()
            stock_code = (item.findtext("stock_code") or "").strip()
            if corp_code and stock_code:
                mapping[stock_code.zfill(6)] = corp_code
        logger.info("[DART] loaded %d stock corp codes", len(mapping))
        return mapping

    def get_latest_financial_data(self, corp_code: str) -> tuple[FinancialData | None, str]:
        current_year = datetime.now().year
        for year in range(current_year - 1, current_year - 4, -1):
            for fs_div in ("CFS", "OFS"):
                rows = self._financial_rows(corp_code, year, fs_div)
                if not rows:
                    continue
                financial = self._to_financial_data(rows)
                if financial:
                    if financial.rd_expense_ratio is None:
                        rcept_no = next((str(r.get("rcept_no")) for r in rows if r.get("rcept_no")), "")
                        revenue = self._find_amount(rows, ("매출액", "영업수익", "수익(매출액)"))
                        found = (
                            self._rd_ratio_from_business_report(corp_code, year, rcept_no, revenue)
                            if rcept_no else None
                        )
                        if found is not None:
                            rd_ratio, how = found
                            financial.rd_expense_ratio = round(rd_ratio, 2)
                            financial.rd_source = f"사업보고서 ({how}, {year})"
                    return financial, f"DART {year} annual {fs_div}"
        return None, "DART financial statements unavailable"

    # 1순위: 'II. 사업의 내용 > 연구개발활동' 표의 '연구개발비/매출액 비율' 행
    #   예) 연구개발비 / 매출액 비율 [연구개발비용계÷당기매출액×100] 28.24% 58.71% 114.76%
    #       연구개발비 / 매출액 비율 [연구개발비용합계÷당기매출액×100] 71.22 60.52 113.57   (% 기호 없음)
    #       연구개발비 / 매출액 비율(%) [...] N/A N/A N/A                                   (종속회사 표)
    _RD_RATIO_ROW = re.compile(
        r"(?:연구\s*개발\s*비(?:용)?\s*(?:합계|총계|계|총액)?\s*/\s*매출액?\s*비(?:율|중)"
        r"|매출액?\s*대비\s*연구\s*개발\s*비(?:용)?\s*(?:비율|비중)?)"
        r"\s*(?:\(\s*%\s*\))?\s*(?:\[[^\]]{0,80}\])?\s*(?:\(\s*%\s*\))?\s*"
        r"(N/A|n/a|-|△?-?\d[\d,]*(?:\.\d+)?)\s*%?"
    )
    # 2순위: 재무제표 주석 '연구개발비의 기능별 배분 공시' 당기 '연구와 개발 비용' 합계 ÷ 매출액
    #   예) 연구개발비의 기능별 배분 공시 당기 (단위 : 원) 판매비와 일반관리비 매출원가 기능별 항목 합계
    #       연구와 개발 비용 35,858,469,244 0 35,858,469,244
    _RD_FUNCTIONAL_NOTE = re.compile(
        r"연구개발비의\s*기능별\s*배분\s*공시.{0,60}?당기\s*\(\s*단위\s*:\s*(원|천원|백만원)\s*\)"
        r".{0,200}?연구와\s*개발\s*비용((?:\s+\(?-?[\d,]+\)?)+)"
    )
    _UNIT = {"원": 1, "천원": 1_000, "백만원": 1_000_000}
    _CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "dart_reports")

    def _business_report_texts(self, corp_code: str, year: int, rcept_no: str) -> list[str]:
        """사업보고서 원문(zip)을 corp_code+연도 기준으로 로컬 캐시하고, 태그를 제거한 본문 목록을 반환."""
        cache_path = os.path.join(self._CACHE_DIR, f"{corp_code}_{year}.zip")
        content: bytes | None = None
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as f:
                    content = f.read()
                zipfile.ZipFile(BytesIO(content)).close()
            except Exception:
                content = None  # 손상된 캐시는 다시 받는다
        if content is None:
            try:
                response = requests.get(
                    f"{self._base_url}/document.xml",
                    params={"crtfc_key": self._api_key, "rcept_no": rcept_no},
                    timeout=max(self._timeout, 30),
                )
                response.raise_for_status()
                content = response.content
                zipfile.ZipFile(BytesIO(content)).close()  # zip이 아니면(오류 JSON 등) 캐시하지 않음
            except Exception as exc:
                logger.warning("[DART] business report download failed corp=%s rcept=%s: %s", corp_code, rcept_no, exc)
                return []
            try:
                os.makedirs(self._CACHE_DIR, exist_ok=True)
                with open(cache_path, "wb") as f:
                    f.write(content)
            except OSError as exc:
                logger.warning("[DART] business report cache write failed %s: %s", cache_path, exc)

        texts: list[str] = []
        with zipfile.ZipFile(BytesIO(content)) as archive:
            # 본문(가장 큰 파일)부터
            names = sorted(archive.namelist(), key=lambda n: archive.getinfo(n).file_size, reverse=True)
            for name in names:
                raw = archive.read(name)
                for encoding in ("utf-8", "cp949"):
                    try:
                        text = raw.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue
                texts.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))
        return texts

    def _rd_ratio_from_business_report(
        self, corp_code: str, year: int, rcept_no: str, revenue: float | None
    ) -> tuple[float, str] | None:
        """사업보고서에서 R&D/매출 비율(%)을 찾는다. (값, 출처 설명) 또는 None."""
        texts = self._business_report_texts(corp_code, year, rcept_no)

        # 1순위: 연구개발활동 표의 비율 행 (첫 값 = 당기). 지배회사 표가 먼저 나오고 N/A 행은 건너뛴다.
        for plain in texts:
            for match in self._RD_RATIO_ROW.finditer(plain):
                token = match.group(1).replace(",", "").replace("△", "-")
                try:
                    value = float(token)
                except ValueError:
                    continue  # N/A, '-'
                if 0 <= value <= 100_000:
                    return value, "연구개발활동"

        # 2순위: 주석의 연구개발비 기능별 배분 합계 ÷ 매출액
        if revenue and revenue > 0:
            for plain in texts:
                match = self._RD_FUNCTIONAL_NOTE.search(plain)
                if not match:
                    continue
                numbers = [self._parse_amount(n) for n in match.group(2).split()]
                numbers = [n for n in numbers if n is not None]
                if not numbers:
                    continue
                total = numbers[-1] * self._UNIT[match.group(1)]  # 마지막 열 = 합계
                if total > 0:
                    return total / revenue * 100.0, "주석 연구개발비 기능별 배분 ÷ 매출액"

        logger.info("[DART] R&D ratio not found in business report corp=%s year=%s", corp_code, year)
        return None

    def _financial_rows(self, corp_code: str, year: int, fs_div: str) -> list[dict]:
        try:
            response = requests.get(
                f"{self._base_url}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": self._api_key,
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": "11011",
                    "fs_div": fs_div,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning("[DART] financial request failed corp=%s year=%s %s: %s", corp_code, year, fs_div, exc)
            return []

        if payload.get("status") != "000":
            return []
        return payload.get("list", []) or []

    def _to_financial_data(self, rows: list[dict]) -> FinancialData | None:
        current_assets = self._find_amount(rows, ("유동자산",))
        current_liabilities = self._find_amount(rows, ("유동부채",))
        total_liabilities = self._find_amount(rows, ("부채총계",))
        total_equity = self._find_amount(rows, ("자본총계",))
        revenue = self._find_amount(rows, ("매출액", "영업수익", "수익(매출액)"))
        operating_income = self._find_amount(rows, ("영업이익",))
        operating_cash_flow = self._find_amount(rows, ("영업활동현금흐름", "영업활동으로 인한 현금흐름"))
        cash_assets = self._find_amount(rows, ("현금및현금성자산", "현금 및 현금성자산"))
        rd_expense = self._find_amount(rows, ("연구개발비", "경상연구개발비"))

        if not any((current_assets, total_liabilities, revenue, operating_income, cash_assets)):
            return None

        current_ratio = self._ratio(current_assets, current_liabilities, multiplier=1.0, default=1.0)
        debt_ratio = self._ratio(total_liabilities, total_equity, multiplier=100.0, default=100.0)
        operating_profit_margin = self._ratio(operating_income, revenue, multiplier=100.0, default=0.0)
        # 연구개발비를 찾지 못하면 None (데이터 없음) — 임의 기본값을 넣지 않는다.
        rd_expense_ratio = (
            rd_expense / revenue * 100.0 if rd_expense is not None and revenue not in (None, 0) else None
        )

        ocf_eok = operating_cash_flow / 100_000_000 if operating_cash_flow is not None else None
        cash_eok = (cash_assets or 0.0) / 100_000_000
        runway: float | None = None
        if ocf_eok is None:
            runway_status = CashRunwayStatus.UNKNOWN
        elif ocf_eok >= 0:
            runway_status = CashRunwayStatus.NO_BURN
        elif cash_assets is None:
            runway_status = CashRunwayStatus.UNKNOWN
        else:
            runway_status = CashRunwayStatus.BURN
            runway = round(min(cash_eok / (abs(ocf_eok) / 12), 60.0), 1)

        return FinancialData(
            current_ratio=round(current_ratio, 2),
            debt_ratio=round(debt_ratio, 2),
            operating_cash_flow=round(ocf_eok, 2) if ocf_eok is not None else None,
            cash_assets=round(cash_eok, 2),
            cash_runway_months=runway,
            cash_runway_status=runway_status,
            operating_profit_margin=round(operating_profit_margin, 2),
            rd_expense_ratio=round(rd_expense_ratio, 2) if rd_expense_ratio is not None else None,
            rd_source="재무제표" if rd_expense_ratio is not None else "",
        )

    def get_recent_disclosure_data(self, corp_code: str, days: int = 365) -> DisclosureData:
        end = datetime.now()
        start = end - timedelta(days=days)
        items: list[dict] = []
        page_no, total_page = 1, 1
        while page_no <= min(total_page, 5):
            try:
                response = requests.get(
                    f"{self._base_url}/list.json",
                    params={
                        "crtfc_key": self._api_key,
                        "corp_code": corp_code,
                        "bgn_de": start.strftime("%Y%m%d"),
                        "end_de": end.strftime("%Y%m%d"),
                        "page_no": str(page_no),
                        "page_count": "100",
                    },
                    timeout=self._timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                logger.warning("[DART] disclosure request failed corp=%s: %s", corp_code, exc)
                return DisclosureData(risk_keywords=[], data_errors=[f"DART disclosure unavailable ({exc})"])

            status = payload.get("status")
            if status == "013":  # 조회된 데이터 없음
                break
            if status != "000":
                return DisclosureData(
                    risk_keywords=[],
                    data_errors=[f"DART disclosure unavailable (status={status} {payload.get('message', '')})".strip()],
                )
            items.extend(payload.get("list", []) or [])
            total_page = int(payload.get("total_page") or 1)
            page_no += 1

        keywords, hits = self._risk_keywords(items)
        return DisclosureData(risk_keywords=keywords, matched_disclosures=hits)

    # 공시 제목 → 리스크 라벨
    _KEYWORD_MAP: dict[str, str] = {
        "관리종목": "관리종목",
        "상장폐지": "상장폐지",
        "거래정지": "거래정지",
        "감사의견": "감사의견 관련",
        "횡령": "횡령",
        "배임": "배임",
        "회생": "회생절차",
        "불성실공시": "불성실공시",
        "유상증자": "유상증자",
        "최대주주 변경": "최대주주 변경",
        "최대주주변경": "최대주주 변경",
        "소송": "소송",
        "영업정지": "영업정지",
    }
    # 제목에 이 표현이 있으면 리스크 이벤트가 아님(해소/취소/정정 공시)
    _EXCLUDE_MARKERS: tuple[str, ...] = ("해제", "철회", "미지정", "정정")
    # 기술적 사유의 매매거래정지 — HIGH 리스크로 보지 않음
    _TECHNICAL_HALT_REASONS: tuple[str, ...] = (
        "주식병합", "이전상장", "신주상장", "무상증자", "액면분할", "주식분할", "액면병합",
    )
    RIGHTS_ISSUE_REPEAT_THRESHOLD = 2

    @classmethod
    def _risk_keywords(cls, items: list[dict]) -> tuple[list[str], list[DisclosureHit]]:
        """공시 제목을 1건씩 검사해 (리스크 라벨 목록, 매칭 상세)를 반환한다."""
        found: list[str] = []
        hits: list[DisclosureHit] = []
        rights_issues: list[DisclosureHit] = []

        def add_label(label: str) -> None:
            if label not in found:
                found.append(label)

        for item in items:
            title = re.sub(r"\s+", " ", str(item.get("report_nm", ""))).strip()
            date = str(item.get("rcept_dt", ""))
            for needle, label in cls._KEYWORD_MAP.items():
                if needle not in title:
                    continue
                marker = next((m for m in cls._EXCLUDE_MARKERS if m in title), None)
                if marker:
                    hits.append(DisclosureHit(date=date, title=title, keyword=label, applied=False,
                                              note=f"제외: '{marker}' 표현 포함"))
                    continue

                if label == "거래정지":
                    reason = next((r for r in cls._TECHNICAL_HALT_REASONS if r in title), None)
                    if reason:
                        hits.append(DisclosureHit(date=date, title=title, keyword=label, applied=False,
                                                  note=f"제외: 기술적 매매거래정지({reason})"))
                        continue
                elif label == "불성실공시":
                    if "예고" in title:
                        label = "불성실공시 지정예고"
                    elif "지정" not in title:
                        label = "불성실공시 관련"
                elif label == "유상증자":
                    hit = DisclosureHit(date=date, title=title, keyword="유상증자 반복", applied=False)
                    rights_issues.append(hit)
                    hits.append(hit)
                    continue

                hits.append(DisclosureHit(date=date, title=title, keyword=label, applied=True, note="반영"))
                add_label(label)

        # 유상증자: 1년 내 서로 다른 일자 기준 2건 이상일 때만 '반복'
        # (같은 날 여러 건 제출된 공시는 하나의 증자 이벤트로 본다)
        distinct_dates = sorted({h.date for h in rights_issues})
        repeated = len(distinct_dates) >= cls.RIGHTS_ISSUE_REPEAT_THRESHOLD
        for hit in rights_issues:
            hit.applied = repeated
            hit.note = (
                f"반영: 1년 내 {len(distinct_dates)}건(서로 다른 일자)"
                if repeated
                else f"제외: 1년 내 {len(distinct_dates)}건 (동일 일자 제출 {len(rights_issues)}건 포함) — 반복 아님"
            )
        if repeated:
            add_label("유상증자 반복")

        return found, hits

    @staticmethod
    def _find_amount(rows: list[dict], names: tuple[str, ...]) -> float | None:
        candidates: list[tuple[int, float]] = []
        for row in rows:
            account = str(row.get("account_nm", ""))
            if not any(name in account for name in names):
                continue
            amount = DartClient._parse_amount(row.get("thstrm_amount"))
            if amount is None:
                continue
            sj_div = str(row.get("sj_div", ""))
            priority = 0 if sj_div in {"BS", "IS", "CIS", "CF"} else 1
            candidates.append((priority, amount))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    @staticmethod
    def _parse_amount(value: object) -> float | None:
        if value is None:
            return None
        text = str(value).replace(",", "").strip()
        if not text or text == "-":
            return None
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            amount = float(text)
        except ValueError:
            return None
        return -amount if negative else amount

    @staticmethod
    def _ratio(numerator: float | None, denominator: float | None, multiplier: float, default: float) -> float:
        if numerator is None or denominator in (None, 0):
            return default
        return numerator / denominator * multiplier


_dart_client: DartClient | None = None


def get_dart_client() -> DartClient:
    global _dart_client
    if _dart_client is None:
        _dart_client = DartClient()
    return _dart_client
