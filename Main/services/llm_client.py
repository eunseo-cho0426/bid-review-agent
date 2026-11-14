"""공용 LLM 클라이언트 — Google Gemini (langchain-google-genai).

환경변수:
  GEMINI_API_KEY : Google AI Studio에서 발급한 API 키 (GOOGLE_API_KEY도 허용)
  GEMINI_MODEL   : 사용할 모델명 (기본값 DEFAULT_GEMINI_MODEL)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def get_llm_api_key() -> str:
    _load_env()
    return (os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")).strip()


def get_llm_model() -> str:
    _load_env()
    return os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL


def get_chat_model(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.1,
):
    """LangChain Chat 모델을 반환한다. 키가 없으면 None."""
    api_key = (api_key or get_llm_api_key()).strip()
    if not api_key:
        return None

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model or get_llm_model(),
        google_api_key=api_key,
        temperature=temperature,
    )


def message_text(message) -> str:
    """AIMessage/청크의 content를 문자열로 변환 (Gemini는 리스트로 줄 때가 있음)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return "".join(parts)
    return str(content or "")


class LLMClient:
    """단순 텍스트 생성용 래퍼 (report_writer_agent 등에서 사용)."""

    def __init__(self) -> None:
        self._llm = None
        try:
            self._llm = get_chat_model()
            if self._llm is not None:
                logger.info("Gemini client initialized: model=%s", get_llm_model())
        except Exception as e:
            logger.warning("Gemini client init failed: %s", e)

    def is_available(self) -> bool:
        return self._llm is not None

    def generate(self, prompt: str) -> Optional[str]:
        if not self.is_available():
            return None
        try:
            return message_text(self._llm.invoke(prompt)).strip()
        except Exception as e:
            logger.warning("Gemini generate failed: %s", e)
            return None


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
