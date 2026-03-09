#!/usr/bin/env python3
"""
aichaind.transport.msgpack_codec — MessagePack Codec

Optional MessagePack serialization for IPC transport.
Falls back to JSON if msgpack is not installed.
Includes benchmark utility for JSON vs MessagePack comparison.

Usage:
    from aichaind.transport.msgpack_codec import encode, decode, benchmark
"""

import json
import time
import logging
from typing import Any

log = logging.getLogger("aichaind.transport.msgpack")

# Try to import msgpack
try:
    import msgpack
    MSGPACK_AVAILABLE = True
except ImportError:
    msgpack = None
    MSGPACK_AVAILABLE = False


def encode(data: Any, use_msgpack: bool = True) -> bytes:
    """Encode data to bytes. Uses msgpack if available, else JSON."""
    if use_msgpack and MSGPACK_AVAILABLE:
        return msgpack.packb(data, use_bin_type=True)
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def decode(raw: bytes, use_msgpack: bool = True) -> Any:
    """Decode bytes to data. Uses msgpack if available, else JSON."""
    if use_msgpack and MSGPACK_AVAILABLE:
        return msgpack.unpackb(raw, raw=False)
    return json.loads(raw.decode("utf-8"))


def content_type(use_msgpack: bool = True) -> str:
    """Return appropriate content type header."""
    if use_msgpack and MSGPACK_AVAILABLE:
        return "application/msgpack"
    return "application/json"


def benchmark(data: dict = None, iterations: int = 1000) -> dict:
    """
    Benchmark JSON vs MessagePack encoding/decoding.

    Returns:
        {"json": {...}, "msgpack": {...}, "winner": "...", "speedup": float}
    """
    if data is None:
        # Generate realistic test data
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "This is a test response " * 50
                },
                "finish_reason": "stop",
                "index": 0,
            }],
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 250,
                "total_tokens": 400,
            },
            "model": "openai/gpt-4o",
            "_aichaind": {
                "routed_model": "openai/gpt-4o",
                "route_confidence": 0.92,
                "route_layers": ["L1:heuristic", "L2:semantic:code_generation"],
                "route_latency_ms": 2.34,
                "exec_latency_ms": 1523.45,
                "pii_redacted": False,
            },
        }

    results = {}

    # JSON benchmark
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    start = time.perf_counter()
    for _ in range(iterations):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        _ = json.loads(encoded.decode("utf-8"))
    json_time = (time.perf_counter() - start) * 1000

    results["json"] = {
        "size_bytes": len(json_bytes),
        "total_ms": round(json_time, 2),
        "per_op_us": round(json_time / iterations * 1000, 2),
    }

    # MessagePack benchmark
    if MSGPACK_AVAILABLE:
        mp_bytes = msgpack.packb(data, use_bin_type=True)
        start = time.perf_counter()
        for _ in range(iterations):
            encoded = msgpack.packb(data, use_bin_type=True)
            _ = msgpack.unpackb(encoded, raw=False)
        mp_time = (time.perf_counter() - start) * 1000

        results["msgpack"] = {
            "size_bytes": len(mp_bytes),
            "total_ms": round(mp_time, 2),
            "per_op_us": round(mp_time / iterations * 1000, 2),
        }

        size_ratio = len(mp_bytes) / len(json_bytes)
        speed_ratio = json_time / mp_time if mp_time > 0 else 1

        results["comparison"] = {
            "size_reduction": f"{(1 - size_ratio) * 100:.1f}%",
            "speed_improvement": f"{speed_ratio:.2f}x",
            "winner": "msgpack" if speed_ratio > 1 else "json",
        }
    else:
        results["msgpack"] = {"error": "msgpack not installed (pip install msgpack)"}
        results["comparison"] = {"winner": "json (msgpack not available)"}

    return results
