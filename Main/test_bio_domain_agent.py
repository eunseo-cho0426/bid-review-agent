import sys; sys.stdout.reconfigure(encoding='utf-8')
import os
from pprint import pprint

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.bio_domain_agent import BioDomainAgent
import agents.bio_domain_agent as bda

# --- Mocking Gemini Client ---
class MockGeminiClient:
    def is_available(self):
        return True
    def generate(self, prompt):
        print("====== [Gemini LLM ?꾨＼?꾪듃 ?꾨떖 ?댁슜 ?뺤씤] ======\n" + prompt + "\n==================================================")
        return "?뚯뒪?몃컮?댁삤??二쇱슂 ?뚯씠?꾨씪?몄? ?꾩옱 ?꾩긽 2??吏꾪뻾 以묒씠硫? ClinicalTrials.gov???깆옱?섏뼱 湲濡쒕쾶 ?щ챸?깆쓣 ?뺣낫?섍퀬 ?덉뒿?덈떎. ?붾텋??FDA ?ш??섏빟??吏??ODD) ?몃옓??以鍮?以묒씠誘濡?湲濡쒕쾶 洹쒖젣 湲곌? 吏꾩텧 媛?μ꽦??湲띿젙?곸씠?? ?듭떖 ?뚯씠?꾨씪???섏〈?꾧? 55%濡??ㅼ냼 ?믪? ?먯? ?좎쓽??由ъ뒪?ъ엯?덈떎."

# Inject the mock to simulate LLM execution perfectly
bda.get_gemini_client = lambda: MockGeminiClient()

# --- Mocking State and Models ---
class MockBioDomain:
    clinical_stage: str = "Phase 2"
    pipeline_count: int = 5
    has_tech_export: bool = True
    has_patent: bool = True
    core_pipeline_dependency: float = 0.55

class MockCompanyData:
    company_name: str = "?뚯뒪?몃컮?댁삤(媛??"
    bio_domain: MockBioDomain = MockBioDomain()

class MockNewsResult:
    def __init__(self, negative_critical_event=False, negative_keywords=None):
        self.negative_critical_event = negative_critical_event
        self.negative_keywords = negative_keywords

class MockState:
    def __init__(self, news_result=None):
        self.company_data = MockCompanyData()
        self.news_result = news_result
        self.bio_domain_result = None

def run_tests():
    agent = BioDomainAgent()

    print("======================================================")
    print("???쒕굹由ъ삤 A: ?뺤긽/?몄옱 耳?댁뒪 (news_result 媛 None??寃쎌슦)")
    print("======================================================")
    state_a = MockState(news_result=None)
    result_a = agent.run(state_a)

    res_a = result_a.bio_domain_result
    print(f"\n[?쒕굹由ъ삤 A ?곗텧 寃곌낵]")
    print(f"理쒖쥌 諛붿씠???먯닔: {res_a.bio_score}")
    print(f"?꾩텧???꾨찓??由ъ뒪?? {res_a.domain_risks}")
    print("\n\n")

    print("======================================================")
    print("???쒕굹由ъ삤 B: 移섎챸???댁뒪 ?섎꼸??耳?댁뒪")
    print("======================================================")
    # negative_critical_event=True, negative_keywords??由ъ뒪???⑥뼱 ?ы븿
    news_res = MockNewsResult(
        negative_critical_event=True,
        negative_keywords=["?꾩긽", "?ㅽ뙣", "嫄곗젅"]
    )
    state_b = MockState(news_result=news_res)
    result_b = agent.run(state_b)

    res_b = result_b.bio_domain_result
    print(f"\n[?쒕굹由ъ삤 B ?곗텧 寃곌낵]")
    print(f"理쒖쥌 諛붿씠???먯닔: {res_b.bio_score}")
    print(f"?꾩텧???꾨찓??由ъ뒪?? {res_b.domain_risks}")

if __name__ == "__main__":
    run_tests()
