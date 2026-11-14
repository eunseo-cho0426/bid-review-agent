from __future__ import annotations
from typing import Optional, Any
from pydantic import AliasChoices, BaseModel, Field
from enum import Enum


class GradeEnum(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class DisclosureRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FinalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HUMAN_IN_THE_LOOP = "HUMAN_IN_THE_LOOP"


class CashRunwayStatus(str, Enum):
    BURN = "BURN"          # 영업현금흐름 음수 → 소진 개월 수 산출
    NO_BURN = "NO_BURN"    # 영업현금흐름 0 이상 → 현금 소진 없음
    UNKNOWN = "UNKNOWN"    # 영업현금흐름(또는 현금) 데이터 없음


class FinancialData(BaseModel):
    current_ratio: float
    debt_ratio: float
    operating_cash_flow: Optional[float]          # 억원, None = 데이터 없음
    cash_assets: float
    cash_runway_months: Optional[float]           # BURN일 때만 값 존재
    operating_profit_margin: float                # 영업이익률 (%)
    rd_expense_ratio: Optional[float]             # R&D 비용 비중 (%), None = 데이터 없음
    rd_source: str = ""                           # R&D 비중 출처
    cash_runway_status: Optional[CashRunwayStatus] = None

    @property
    def runway_status(self) -> CashRunwayStatus:
        if self.cash_runway_status is not None:
            return self.cash_runway_status
        return CashRunwayStatus.BURN if self.cash_runway_months is not None else CashRunwayStatus.UNKNOWN

    @property
    def runway_text(self) -> str:
        status = self.runway_status
        if status == CashRunwayStatus.BURN and self.cash_runway_months is not None:
            return f"{self.cash_runway_months:.1f}개월"
        if status == CashRunwayStatus.NO_BURN:
            return "현금 소진 없음"
        return "데이터 없음"

    @property
    def rd_text(self) -> str:
        if self.rd_expense_ratio is None:
            return "데이터 없음"
        return f"{self.rd_expense_ratio:.2f}%"

    @property
    def rd_source_text(self) -> str:
        """재무제표 / 사업보고서 (...) / 데이터 없음"""
        if self.rd_expense_ratio is None:
            return "데이터 없음"
        return self.rd_source or "입력값"


class NewsItem(BaseModel):
    title: str
    date: str
    content: str


class BioDomainData(BaseModel):
    clinical_stage: str
    pipeline_count: int
    has_tech_export: bool
    has_patent: bool
    core_pipeline_dependency: float


class DisclosureHit(BaseModel):
    """공시 제목 1건에 대한 키워드 매칭 결과."""
    date: str = ""
    title: str
    keyword: str
    applied: bool            # True = 리스크로 반영, False = 제외
    note: str = ""           # 반영/제외 사유


class DisclosureData(BaseModel):
    risk_keywords: list[str]
    matched_disclosures: list[DisclosureHit] = Field(default_factory=list)
    data_errors: list[str] = Field(default_factory=list)   # 'DART disclosure unavailable' 등


class CompanyData(BaseModel):
    company_id: str
    company_name: str
    ticker_code: str                 # 종목코드 (예: 091990)
    industry_category: str           # 산업 분류 (예: 제약/바이오)
    market_cap: float                # 시가총액 (억원 단위)
    financial: FinancialData
    news: Optional[list[NewsItem]]
    bio_domain: BioDomainData
    disclosure_data: DisclosureData = Field(
        validation_alias=AliasChoices("disclosure_data", "disclosure")
    )

    @property
    def disclosure(self) -> DisclosureData:
        return self.disclosure_data


class FinancialResult(BaseModel):
    financial_score: float
    risk_factors: list[str]


class NewsResult(BaseModel):
    news_score: Optional[float]
    positive_keywords: list[str]
    negative_keywords: list[str]                     # 강한 + 중간 부정 (합집합)
    negative_critical_event: bool
    missing_news: bool
    negative_high_keywords: list[str] = Field(default_factory=list)   # 강한 부정 (NEG_HIGH)
    negative_mid_keywords: list[str] = Field(default_factory=list)    # 중간 부정 (NEG_MID)
    llm_status: str = ""                                              # 성공 / 실패 / 미호출(...)
    keyword_score: Optional[float] = None
    keyword_hits: int = 0
    llm_score: Optional[float] = None
    llm_summary: str = ""
    merge_weights: str = ""


class BioDomainResult(BaseModel):
    bio_domain_score: float = Field(
        validation_alias=AliasChoices("bio_domain_score", "bio_score")
    )
    domain_risks: list[str]
    summary: str = ""

    @property
    def bio_score(self) -> float:
        return self.bio_domain_score


class DisclosureResult(BaseModel):
    disclosure_risk_level: DisclosureRiskLevel
    detected_keywords: list[str]
    matched_disclosures: list[DisclosureHit] = Field(default_factory=list)
    data_errors: list[str] = Field(default_factory=list)
    lookup_failed: bool = False          # 공시 조회 실패 (MEDIUM 처리 + 전문가 검토)


class RiskScoreResult(BaseModel):
    final_score: float
    grade: GradeEnum
    missing_news: bool
    score_breakdown: dict[str, Any]


class SupervisoryResult(BaseModel):
    special_case:        bool
    special_case_reason: str
    flags:               list[str]
    original_grade:      Optional[str] = None
    adjusted_grade:      Optional[str] = None
    llm_called:          bool = False
    is_error:            bool = False


class LoanDecisionResult(BaseModel):
    final_decision: FinalDecision
    decision_reason: str


class CompanyReport(BaseModel):
    company_id: str
    company_name: str
    grade: GradeEnum
    final_score: float
    financial_score: float
    news_score: Optional[float]
    bio_score: float
    disclosure_risk_level: DisclosureRiskLevel
    special_case: bool
    special_case_reason: str
    final_decision: FinalDecision
    decision_reason: str
    report_text: str
    financial_risk_factors: list[str] = Field(default_factory=list)
    news_positive_keywords: list[str] = Field(default_factory=list)
    news_negative_keywords: list[str] = Field(default_factory=list)
    news_negative_critical_event: bool = False
    news_keyword_score: Optional[float] = None
    news_keyword_hits: int = 0
    news_llm_score: Optional[float] = None
    news_llm_summary: str = ""
    news_merge_weights: str = ""
    bio_domain_risks: list[str] = Field(default_factory=list)
    bio_domain_summary: str = ""
    disclosure_detected_keywords: list[str] = Field(default_factory=list)
    disclosure_matched: list[DisclosureHit] = Field(default_factory=list)
    disclosure_data_errors: list[str] = Field(default_factory=list)
    disclosure_lookup_failed: bool = False
    rd_expense_text: str = "데이터 없음"
    rd_source_text: str = "데이터 없음"
    cash_runway_text: str = "데이터 없음"
    news_negative_high_keywords: list[str] = Field(default_factory=list)
    news_negative_mid_keywords: list[str] = Field(default_factory=list)
    # LLM 호출 결과: {"news": ..., "supervisory": ..., "report": ...} → 성공 / 실패 / 미호출(사유)
    llm_status: dict[str, str] = Field(default_factory=dict)


class BioAgentState(BaseModel):
    company_data: CompanyData
    financial_result: Optional[FinancialResult] = None
    news_result: Optional[NewsResult] = None
    bio_domain_result: Optional[BioDomainResult] = None
    disclosure_result: Optional[DisclosureResult] = None
    risk_score_result: Optional[RiskScoreResult] = None
    supervisory_result: Optional[SupervisoryResult] = None
    loan_decision_result: Optional[LoanDecisionResult] = None
    report: Optional[CompanyReport] = None
    errors: list[str] = []

    # 흐름 제어 필드
    restart_required:   bool = False
    restart_count:      int  = 0
    needs_human_review: bool = False
