"""The ONLY module that talks to the LLM provider (Gemini).

Everything else calls `LLMClient.generate_json(...)` with a Pydantic schema and
gets back a validated object. Switching providers = rewriting this file only.

Contract enforcement, in two layers:
1. Gemini is asked for JSON matching the schema (response_schema).
2. We validate the text ourselves with Pydantic (we don't trust layer 1 alone).
   On failure we retry once, showing the model its invalid output and the error.
   A second failure raises LLMOutputError, and the caller falls back safely.
"""

import os
from typing import TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from weather_risk.config import ROOT

# Read GEMINI_API_KEY (and optional GEMINI_MODEL) from .env (git-ignored).
# Variables already set in the shell take precedence.
load_dotenv(ROOT / ".env")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-hydrogen")
T = TypeVar("T", bound=BaseModel)


class LLMOutputError(RuntimeError):
    """The model didn't produce valid JSON for the schema after a retry."""


class LLMNotConfigured(RuntimeError):
    """No Gemini API key. Only chat needs one; scores, alerts and tests work without it."""


class LLMClient:
    def __init__(self, model: str = MODEL):
        self.model = model
        self._client = None   # created on first use, so the app can start without a key

    def _get_client(self) -> genai.Client:
        if self._client is None:
            if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
                raise LLMNotConfigured("Chat needs a GEMINI_API_KEY (free from Google AI Studio). "
                                       "Scores, alerts and tests work without it.")
            self._client = genai.Client()  # reads GEMINI_API_KEY from the environment
        return self._client

    def generate_json(self, system: str, prompt: str, schema: type[T], temperature: float = 0.0) -> T:
        client = self._get_client()
        contents = prompt
        for attempt in (1, 2):
            resp = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=temperature,  # 0 = as repeatable as possible
                ),
            )
            text = resp.text or ""
            try:
                return schema.model_validate_json(text)
            except ValidationError as e:
                if attempt == 2:
                    raise LLMOutputError(f"invalid {schema.__name__} after retry: {e}") from e
                contents = (f"{prompt}\n\nYour previous reply was not valid for the schema.\n"
                            f"Reply: {text[:2000]}\nErrors: {e}\nReturn corrected JSON only.")
        raise AssertionError("unreachable")
