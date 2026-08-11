from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import metrics
from .gemini_llm import GeminiLLM
from .mock_llm import FakeLLM
from .mock_rag import retrieve
from .pii import hash_user_id, summarize_text
from .prompt_management import resolve_prompt
from .response_cache import ResponseCache
from .tracing import get_langfuse_client, observe, tracing_enabled


@dataclass
class AgentResult:
    answer: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    quality_score: float


@observe(as_type="span", name="retrieval", capture_input=False, capture_output=False)
def _retrieve_span(message: str) -> list[str]:
    """Trace the RAG retrieval as its own span so 'retrieval' is visible in the waterfall."""
    return retrieve(message)


class LabAgent:
    def __init__(self, model: str = "gemini-3.1-flash-lite") -> None:
        self.model = model
        self.llm = GeminiLLM(model=model) if os.getenv("GOOGLE_API_KEY") else FakeLLM(model=model)
        self.cache = ResponseCache()

    @observe(as_type="generation", capture_input=False, capture_output=False)
    def run(self, user_id: str, feature: str, session_id: str, message: str) -> AgentResult:
        started = time.perf_counter()
        cache_key = self.cache.key(feature, message)
        hit = self.cache.get(cache_key) if self.cache.enabled else None
        if hit is not None:
            latency_ms = int((time.perf_counter() - started) * 1000)
            metrics.record_request(
                latency_ms=latency_ms,
                cost_usd=0.0,
                tokens_in=0,
                tokens_out=0,
                quality_score=hit["quality_score"],
            )
            return AgentResult(
                answer=hit["answer"],
                latency_ms=latency_ms,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                quality_score=hit["quality_score"],
            )

        docs = _retrieve_span(message)
        langfuse_client = get_langfuse_client()
        prompt = resolve_prompt(
            langfuse_client,
            feature=feature,
            docs=docs,
            message=message,
            enabled=tracing_enabled(),
        )
        response = self.llm.generate(prompt.text)
        quality_score = self._heuristic_quality(message, response.text, docs)
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost_usd = self._estimate_cost(response.usage.input_tokens, response.usage.output_tokens)

        langfuse_client.update_current_trace(
            user_id=hash_user_id(user_id),
            session_id=session_id,
            tags=["lab", feature, self.model],
            metadata={
                "prompt_name": prompt.name,
                "prompt_label": prompt.label,
                "prompt_version": prompt.version,
                "prompt_source": prompt.source,
            },
        )
        langfuse_client.update_current_generation(
            model=self.model,
            metadata={
                "doc_count": len(docs),
                "query_preview": summarize_text(message),
                "prompt_name": prompt.name,
                "prompt_label": prompt.label,
                "prompt_version": prompt.version,
                "prompt_source": prompt.source,
                "prompt_fetch_error": prompt.fetch_error,
            },
            usage_details={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            },
            cost_details={"total": cost_usd},
            prompt=prompt.managed_prompt,
        )

        metrics.record_request(
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            quality_score=quality_score,
        )

        self.cache.set(
            cache_key,
            {"answer": response.text, "quality_score": quality_score},
        )

        return AgentResult(
            answer=response.text,
            latency_ms=latency_ms,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            cost_usd=cost_usd,
            quality_score=quality_score,
        )

    def _estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        input_cost = (tokens_in / 1_000_000) * 3
        output_cost = (tokens_out / 1_000_000) * 15
        return round(input_cost + output_cost, 6)

    def _heuristic_quality(self, question: str, answer: str, docs: list[str]) -> float:
        score = 0.5
        if docs:
            score += 0.2
        if len(answer) > 40:
            score += 0.1
        if question.lower().split()[0:1] and any(token in answer.lower() for token in question.lower().split()[:3]):
            score += 0.1
        if "[REDACTED" in answer:
            score -= 0.2
        return round(max(0.0, min(1.0, score)), 2)
