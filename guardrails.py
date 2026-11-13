"""
guardrails.py — Input-tier guardrails (Pillar 3) + cost telemetry (Pillar 4).

Attached to the ADK agents as native callbacks:

  before_model_guardrail(callback_context, llm_request) -> LlmResponse | None
      * blocks prompt-injection attempts (short-circuits before the LLM runs)
      * blocks obviously off-topic requests (scope guard; defense-in-depth —
        the coordinator instruction is the primary scope mechanism)
      * masks PII in-place in the outgoing request (emails, phones, cards, ID codes)

  after_model_cost_tracker(callback_context, llm_response) -> None
      * reads usage_metadata on every model completion and accumulates token
        counts + estimated USD cost into the module-level COST tracker, which the
        Streamlit app reads for its telemetry widget.
"""

import re

from google.adk.models import LlmResponse
from google.genai import types

from config import price_for

# --------------------------------------------------------------------------- #
# PII masking
# --------------------------------------------------------------------------- #
# NOTE: patterns are deliberately conservative so they do NOT clobber in-domain
# identifiers the analyst needs (terminal IDs like IDR-R03 / JUP-T02 / STAR-D03,
# component IDs like IDR-LC-02). We mask genuine personal data only.
_PII_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL_REDACTED]"),
    # Credit-card-like 13-16 digit groups
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[CARD_REDACTED]"),
    # Malaysian NRIC  YYMMDD-PB-###G
    (re.compile(r"\b\d{6}-\d{2}-\d{4}\b"), "[ID_REDACTED]"),
    # Passport-like  A12345678
    (re.compile(r"\b[A-Z]\d{8}\b"), "[ID_REDACTED]"),
    # Phone numbers: +60..., 01x-xxxxxxx, or grouped 7+ digit sequences
    (re.compile(r"(?<!\w)(?:\+?6?0?1\d[-\s]?\d{3,4}[-\s]?\d{3,4}|\+\d[\d\s().-]{7,}\d)(?!\w)"),
     "[PHONE_REDACTED]"),
]


def mask_pii(text: str) -> str:
    """Return ``text`` with emails, phones, cards and ID codes redacted."""
    if not text:
        return text
    for pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# --------------------------------------------------------------------------- #
# Prompt-injection + scope detection
# --------------------------------------------------------------------------- #
_INJECTION_PATTERNS = [
    "ignore previous instructions", "ignore all previous", "ignore the above",
    "disregard previous", "disregard the above", "disregard your",
    "reveal your system prompt", "show me your system prompt", "system prompt",
    "you are now", "act as", "developer mode", "jailbreak", "ignore your rules",
    "bypass the", "override your", "drop table", "delete from", "truncate table",
    "wipe the database", "modify the database", "delete the", "drop the",
    "erase the", "wipe the", "update the", "insert into",
]

# Obvious off-topic requests (scope guard). Weather IS in scope (rain-fade), so it
# is intentionally absent here.
_OFF_TOPIC_PATTERNS = [
    "poem", "haiku", "write a song", "tell me a joke", "recipe for",
    "write a story", "write an essay", "do my homework",
    # General-knowledge / trivia questions that are clearly outside NOC operations.
    "who is the president", "who is the prime minister", "prime minister",
    "president of", "capital of", "who won", "how old is", "what year",
    "football", "election", "stock price", "translate this to",
]

_INJECTION_MSG = (
    "🛡️ **Request blocked (prompt-injection guardrail).** I can't act on instructions "
    "that try to override my operating rules or modify data. I'm a read-only NOC "
    "assistant — ask me about terminal/hub telemetry, incidents, or the runbooks."
)
_OFF_TOPIC_MSG = (
    "🛡️ I'm the NOC Virtual Analyst — I only help with satellite network operations "
    "(iDirect, JUPITER, Starlink terminals, the hub, incidents, runbooks, and site "
    "weather for rain-fade). That request is outside my scope. What can I help you "
    "troubleshoot?"
)


def _last_user_text(llm_request) -> str:
    for content in reversed(getattr(llm_request, "contents", []) or []):
        if getattr(content, "role", None) == "user" and content.parts:
            return " ".join(p.text for p in content.parts if getattr(p, "text", None))
    return ""


def _mask_request_in_place(llm_request) -> None:
    for content in getattr(llm_request, "contents", []) or []:
        if getattr(content, "role", None) != "user":
            continue
        for part in content.parts or []:
            if getattr(part, "text", None):
                part.text = mask_pii(part.text)


def _blocked_response(message: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=message)])
    )


def before_model_guardrail(callback_context, llm_request):
    """Input-tier guardrail. Returns an LlmResponse to short-circuit, or None."""
    text = _last_user_text(llm_request).lower()

    if any(p in text for p in _INJECTION_PATTERNS):
        return _blocked_response(_INJECTION_MSG)

    if any(p in text for p in _OFF_TOPIC_PATTERNS):
        return _blocked_response(_OFF_TOPIC_MSG)

    # No short-circuit: redact PII before the request reaches the model.
    _mask_request_in_place(llm_request)
    return None


# --------------------------------------------------------------------------- #
# Cost telemetry
# --------------------------------------------------------------------------- #
class CostTracker:
    """Process-wide accumulator for token usage and estimated USD cost."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.usd = 0.0
        self.calls = 0

    def add(self, model: str, in_tok: int, out_tok: int) -> None:
        price = price_for(model)
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.usd += (in_tok / 1_000_000) * price["input"]
        self.usd += (out_tok / 1_000_000) * price["output"]
        self.calls += 1

    def snapshot(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "usd": self.usd,
            "calls": self.calls,
        }


# Single shared instance the app reads from.
COST = CostTracker()


def after_model_cost_tracker(callback_context, llm_response):
    """Accumulate token usage + USD from every model completion."""
    um = getattr(llm_response, "usage_metadata", None)
    if um is None:
        return None
    in_tok = um.prompt_token_count or 0
    out_tok = (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)
    model = getattr(llm_response, "model_version", None) or ""
    COST.add(model, in_tok, out_tok)
    return None
