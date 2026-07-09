#!/usr/bin/env python3
"""
aichaind.routing.task_classifier — Tier 1 deterministic task classifier.

Implements step 1 of the routing pipeline (docs/DYNAMIC_AUTO.md §2):
classify a request into the 10-type task taxonomy and estimate difficulty
0..100. Tier 1 uses only deterministic signals — attachments, tool schemas,
code fences, markers, length — no network, no LLM, no embeddings.

Tier 2 (local embedding centroids) and tier 3 (harness hint / previous
turn) plug in behind the same interface: classify() accepts both as
optional inputs and uses them only when tier 1 is not confident.

Output feeds pom.Request(task_type=..., difficulty=...); the dynamic
intelligence floor is `max(min_intelligence, difficulty * 0.8)`.

MIT License.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TASK_TYPES = (
    "chat_casual", "creative_writing", "knowledge", "legal_formal",
    "coding", "agentic_tool_use", "vision_ocr", "vision_reasoning",
    "math_logic", "translation_language",
)

#: taxonomy -> catalog quality_by_task dimension (until roadmap #8 gives the
#: catalog native per-taxonomy scores).
TAXONOMY_TO_CATALOG = {
    "chat_casual": "general_chat",
    "creative_writing": "general_chat",
    "knowledge": "general_chat",
    "legal_formal": "reasoning",
    "coding": "coding",
    "agentic_tool_use": "tool_agent_compatibility",
    "vision_ocr": "vision",
    "vision_reasoning": "vision",
    "math_logic": "reasoning",
    "translation_language": "general_chat",
}

#: difficulty priors per DYNAMIC_AUTO §2 table (low~20, medium~45, high~72)
_DIFFICULTY_PRIOR = {
    "chat_casual": 15.0, "creative_writing": 45.0, "knowledge": 32.0,
    "legal_formal": 72.0, "coding": 58.0, "agentic_tool_use": 70.0,
    "vision_ocr": 45.0, "vision_reasoning": 70.0, "math_logic": 74.0,
    "translation_language": 32.0,
}


@dataclass
class Classification:
    task_type: str
    difficulty: float
    confidence: float
    signals: list[str] = field(default_factory=list)

    @property
    def catalog_dimension(self) -> str:
        return TAXONOMY_TO_CATALOG[self.task_type]


# ---------------------------------------------------------------- signals
# English + Serbian (owner's language) keyword surface. Word-boundary regexes
# to avoid substring accidents.

def _rx(*words: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)

_CODE_FENCE = re.compile(r"```")
_STACK_TRACE = re.compile(
    r"Traceback \(most recent call last\)|at [\w$.<>]+\([\w.]+:\d+\)"
    r"|^\s*File \"[^\"]+\", line \d+|Exception in thread|segfault|core dumped",
    re.MULTILINE)
_FILE_REF = re.compile(
    r"\b[\w/\\.-]+\.(?:py|js|ts|tsx|jsx|java|rs|go|c|cpp|h|cs|rb|php|sh|ps1|sql|html|css|json|ya?ml|toml)\b",
    re.IGNORECASE)
_CODE_WORDS = _rx("function", "funkcij[aue]", "class[ae]?", "bug", "refactor",
                  "compile", "debug", "unit test", "testovi?", "api endpoint",
                  "regex", "skript[aue]", "kod", "code", "implement(?:iraj|uj)?",
                  "napi[sš]i\\s+(?:mi\\s+)?(?:python|skriptu|funkciju|kod)")
_MATH_MARKUP = re.compile(r"\\frac|\\int|\\sum|\\sqrt|\$[^$]+\$|[∫∑√≤≥≠±]|\bd[xy]/d[xy]\b")
_MATH_WORDS = _rx("prove", "theorem", "lemma", "integral", "derivative",
                  "equation", "jedna[cč]in[aue]", "doka[zž]i", "teorem[aue]?",
                  "izvod", "puzzle", "zagonetk[aue]", "probability", "verovatno[cć][aue]",
                  "matrix", "matric[aue]", "logi[cč]ki")
_TRANSLATE = re.compile(
    r"\b(?:translate|prevedi|prevod|preveo|prevesti)\b|"
    r"\bna\s+(?:engleski|srpski|nema[cč]ki|francuski|[sš]panski|ruski|kineski)\b|"
    r"\b(?:in|into|to)\s+(?:english|serbian|german|french|spanish|russian|chinese|japanese|italian)\b",
    re.IGNORECASE)
_LEGAL = _rx("contract", "ugovora?", "clause", "klauzul[aue]", "statute",
             "zakona?", "pursuant", "nda", "liability", "odgovornost(?:i)?",
             "gdpr", "aneks", "tu[zž]b[aue]", "pravilnik[aue]?", "terms of service",
             "indemnif\\w+", "arbitra[zž]\\w*", "[cč]lan\\s+\\d+")
_CREATIVE = re.compile(
    r"\b(?:write|napi[sš]i|sastavi|compose)\b.{0,40}\b(?:story|pri[cč]u|poem|pesmu|song|scene|scenu|essay|esej|haiku|novel|roman|dijalog|dialogue|short story)\b"
    r"|\bnastavi\s+pri[cč]u\b|\bcontinue\s+the\s+story\b",
    re.IGNORECASE | re.DOTALL)
_KNOWLEDGE = re.compile(
    r"^(?:who|what|when|where|which|why|how many|how much|ko\s|[sš]ta\s|kada\s|gde\s|koj[aeiu]?|kolik[oa])\b"
    r"|\b(?:capital of|glavni grad|population of|broj stanovnika|history of|istorij[aue]|najdu[zž][aei]|najve[cć][aei]|najmanj[aei])\b",
    re.IGNORECASE)
_OCR_INTENT = _rx("extract", "izvuci", "read", "pro[cč]itaj", "transcribe",
                  "prekucaj", "ocr", "text from", "tekst sa", "table from",
                  "tabel[aue] iz")
_GREETING = re.compile(
    r"^(?:hi|hey|hello|zdravo|[cć]ao|hvala|thanks?|thank you|ok(?:ay)?|super|great|dobro jutro|good (?:morning|night)|kako si|how are you|laku no[cć])\b[\s!,.?]*",
    re.IGNORECASE)

_HARD_WORDS = _rx("production", "produkcij[aue]", "optimiz\\w+", "concurren\\w+",
                  "distributed", "race condition", "edge case\\w*", "formal\\w*",
                  "rigorous\\w*", "detaljn\\w+", "kompleksn\\w+", "arhitektur\\w+",
                  "security", "bezbednost")


def _text_of(messages: list[dict] | None) -> str:
    parts = []
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):  # OpenAI multi-part content
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
    return "\n".join(parts)


def _has_image(messages: list[dict] | None, attachment_types: list[str] | None) -> bool:
    if any(str(t).lower().startswith("image") for t in (attachment_types or [])):
        return True
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list) and any(
                isinstance(p, dict) and p.get("type") in ("image_url", "input_image")
                for p in c):
            return True
    return False


# ---------------------------------------------------------------- classify

def classify(messages: list[dict] | None = None, *,
             tool_schema_present: bool = False,
             attachment_types: list[str] | None = None,
             transcript_tokens: int = 0,
             harness_hint: str = "",
             previous_task_type: str = "") -> Classification:
    """Tier 1 deterministic classification -> (task_type, difficulty)."""
    text = _text_of(messages)
    last_user = next((m.get("content", "") for m in reversed(messages or [])
                      if m.get("role") == "user" and isinstance(m.get("content"), str)),
                     text)
    signals: list[str] = []

    # 1. Tool schema in the request: strongest signal there is.
    if tool_schema_present:
        return _result("agentic_tool_use", 0.95, ["tool_schema"], text)

    # 2. Image attachments: OCR intent vs analytical question.
    if _has_image(messages, attachment_types):
        if _OCR_INTENT.search(text):
            return _result("vision_ocr", 0.9, ["image", "ocr_intent"], text)
        return _result("vision_reasoning", 0.85, ["image"], text)

    # 3. Code: fences, stack traces, file refs, code vocabulary.
    code_score = 0
    if _CODE_FENCE.search(text):
        code_score += 2; signals.append("code_fence")
    if _STACK_TRACE.search(text):
        code_score += 2; signals.append("stack_trace")
    if _FILE_REF.search(text):
        code_score += 1; signals.append("file_ref")
    if _CODE_WORDS.search(text):
        code_score += 1; signals.append("code_words")
    if code_score >= 2:
        return _result("coding", 0.9, signals, text)

    # 4. Math / formal logic.
    if _MATH_MARKUP.search(text) or _MATH_WORDS.search(text):
        return _result("math_logic", 0.85, signals + ["math"], text)

    # 5. Translation.
    if _TRANSLATE.search(text):
        return _result("translation_language", 0.85, signals + ["translate"], text)

    # 6. Legal / formal register.
    if _LEGAL.search(text):
        return _result("legal_formal", 0.8, signals + ["legal"], text)

    # 7. Creative writing.
    if _CREATIVE.search(text):
        return _result("creative_writing", 0.85, signals + ["creative"], text)

    # 8. Single code hint without corroboration still means coding intent.
    if code_score == 1:
        return _result("coding", 0.6, signals, text)

    # 9. Factual question.
    if _KNOWLEDGE.search(last_user.strip() if isinstance(last_user, str) else text):
        return _result("knowledge", 0.75, signals + ["question"], text)

    # 10. Short casual message.
    if len(text) < 120 or _GREETING.match(text.strip()):
        return _result("chat_casual", 0.7, signals + ["short"], text)

    # Tier 3 fallbacks: harness hint, then previous turn's type.
    if harness_hint in TASK_TYPES:
        return _result(harness_hint, 0.5, signals + ["harness_hint"], text)
    if previous_task_type in TASK_TYPES:
        return _result(previous_task_type, 0.4, signals + ["previous_turn"], text)

    return _result("chat_casual", 0.3, signals + ["default"], text)


def _result(task_type: str, confidence: float, signals: list[str], text: str) -> Classification:
    return Classification(
        task_type=task_type,
        difficulty=_difficulty(task_type, text),
        confidence=confidence,
        signals=signals,
    )


def _difficulty(task_type: str, text: str) -> float:
    d = _DIFFICULTY_PRIOR[task_type]
    n = len(text)
    if n > 400:
        d += 6.0
    if n > 1500:
        d += 8.0
    if n > 6000:
        d += 8.0
    fences = len(_CODE_FENCE.findall(text)) // 2
    d += min(fences * 3.0, 9.0)
    if _STACK_TRACE.search(text):
        d += 8.0
    hard_hits = len(set(m.group(0).lower() for m in _HARD_WORDS.finditer(text)))
    d += min(hard_hits * 4.0, 12.0)
    if _rx("prove", "doka[zž]i", "theorem", "formal proof").search(text):
        d += 8.0
    return max(0.0, min(100.0, d))
