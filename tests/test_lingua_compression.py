"""Roadmap #6: LLMLingua-2 compression hooks — off by default, guardrailed,
inert without the optional dependency."""
from aichaind.compression import LinguaCompressor


def _fake(text: str, ratio: float) -> str:
    return text[: max(1, int(len(text) * ratio))]


LONG = "Ovo je vrlo dugacak istorijski deo razgovora koji sme da se kompresuje. " * 20
MSGS = [
    {"role": "system", "content": "You are a helpful assistant." + LONG},
    {"role": "user", "content": LONG},
    {"role": "assistant", "content": "```python\nx = 1\n```" + LONG},
    {"role": "user", "content": LONG},
    {"role": "assistant", "content": LONG},
    {"role": "user", "content": "kratko poslednje pitanje?"},
]


def test_disabled_by_default_is_noop():
    c = LinguaCompressor()
    out, meta = c.compress_messages(MSGS)
    assert out == MSGS and meta["skipped_reason"] == "disabled"


def test_unavailable_dependency_is_noop():
    c = LinguaCompressor({"enabled": True})
    if c.available:  # llmlingua actually installed — nothing to assert here
        return
    out, meta = c.compress_messages(MSGS)
    assert out == MSGS and meta["skipped_reason"] == "llmlingua_not_installed"


def test_guardrails_protect_system_code_and_recent_turns():
    c = LinguaCompressor({"enabled": True, "keep_last_turns": 2}, _compress_fn=_fake)
    out, meta = c.compress_messages(MSGS)
    assert meta["lingua_compressed"] and meta["chars_saved"] > 0
    assert out[0]["content"] == MSGS[0]["content"]        # system untouched
    assert out[2]["content"] == MSGS[2]["content"]        # code fence untouched
    assert out[-1]["content"] == MSGS[-1]["content"]      # recent turns untouched
    assert out[-2]["content"] == MSGS[-2]["content"]
    assert len(out[1]["content"]) < len(MSGS[1]["content"])  # old turn compressed


def test_nocompress_override():
    c = LinguaCompressor({"enabled": True}, _compress_fn=_fake)
    msgs = MSGS + [{"role": "user", "content": "@aichain nocompress molim te"}]
    out, meta = c.compress_messages(msgs)
    assert out == msgs and meta["skipped_reason"] == "nocompress_override"


def test_compression_failure_keeps_verbatim():
    def boom(text, ratio):
        raise RuntimeError("model exploded")
    c = LinguaCompressor({"enabled": True}, _compress_fn=boom)
    out, meta = c.compress_messages(MSGS)
    assert out == MSGS and not meta["lingua_compressed"]


def test_can_fit_by_compression():
    c = LinguaCompressor({"enabled": True, "ratio": 0.5}, _compress_fn=_fake)
    # 150k transcript, 128k window: 150k - 105k*0.5 = 97.5k + 2k overhead -> fits
    assert c.can_fit_by_compression(150_000, 128_000)
    # 400k transcript cannot fit 128k even compressed
    assert not c.can_fit_by_compression(400_000, 128_000)
    # disabled -> never claims to fit
    assert not LinguaCompressor().can_fit_by_compression(150_000, 128_000)
