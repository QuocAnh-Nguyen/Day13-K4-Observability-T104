from __future__ import annotations

import os
from typing import Any

from .incidents import STATE
from .mock_llm import FakeResponse, FakeUsage


class GeminiLLM:
    """Real Gemini-backed LLM implementing the same interface as FakeLLM.

    Uses GOOGLE_API_KEY from the environment. Real token usage comes from the
    API usage metadata; the lab's `cost_spike` incident still inflates output
    tokens so the alert/cost dashboards behave the same as with the mock.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
        self.client: Any | None = None

    def _client(self) -> Any:
        if self.client is None:
            from google import genai

            self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        return self.client

    def generate(self, prompt: str) -> FakeResponse:
        response = self._client().models.generate_content(
            model=self.model,
            contents=prompt,
        )
        text = (response.text or "").strip()
        metadata = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(metadata, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(metadata, "candidates_token_count", 0) or 0)
        if STATE["cost_spike"]:
            tokens_out *= 4
        return FakeResponse(text=text, usage=FakeUsage(tokens_in, tokens_out), model=self.model)
