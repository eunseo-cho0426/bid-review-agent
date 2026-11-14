from __future__ import annotations

import logging

from models.schemas import BioAgentState, DisclosureResult, DisclosureRiskLevel

logger = logging.getLogger(__name__)

HIGH_RISK_KEYWORDS = {
    "관리종목",
    "상장폐지",
    "거래정지",
    "감사의견 관련",
    "감사의견 한정",
    "감사의견 거절",
    "계속기업 불확실성",
    "횡령",
    "배임",
    "회생절차",
    "불성실공시",          # 불성실공시법인 지정 확정
    "영업정지",
}
MEDIUM_RISK_KEYWORDS = {
    "유상증자 반복",
    "최대주주 변경",
    "소송",
    "불성실공시 지정예고",
    "불성실공시 관련",
}


class DisclosureAgent:
    """Classify disclosure risk keywords into LOW / MEDIUM / HIGH."""

    def run(self, state: BioAgentState) -> BioAgentState:
        disclosure_data = state.company_data.disclosure_data
        keywords = disclosure_data.risk_keywords
        company_name = state.company_data.company_name

        detected: list[str] = []
        has_high = False
        has_medium = False

        for keyword in keywords:
            if keyword in HIGH_RISK_KEYWORDS:
                has_high = True
            elif keyword in MEDIUM_RISK_KEYWORDS:
                has_medium = True
            if keyword not in detected:
                detected.append(keyword)

        # 공시 조회 실패 → 리스크를 확인할 수 없으므로 LOW가 아닌 MEDIUM(60점)으로 처리하고,
        # SupervisoryReviewAgent가 disclosure_data_missing 특이사항으로 전문가 검토에 넘긴다.
        lookup_failed = bool(disclosure_data.data_errors)

        if has_high:
            risk_level = DisclosureRiskLevel.HIGH
        elif lookup_failed or has_medium or detected:
            risk_level = DisclosureRiskLevel.MEDIUM
        else:
            risk_level = DisclosureRiskLevel.LOW

        state.disclosure_result = DisclosureResult(
            disclosure_risk_level=risk_level,
            detected_keywords=detected,
            matched_disclosures=disclosure_data.matched_disclosures,
            data_errors=disclosure_data.data_errors,
            lookup_failed=lookup_failed,
        )
        logger.info(
            "[DisclosureAgent] %s | risk=%s | keywords=%s | data_errors=%s",
            company_name,
            risk_level,
            detected,
            disclosure_data.data_errors,
        )
        return state
