"""
Ollama LLM client.
Calls qwen3-coder (fast pass) or deepseek-r1:32b (deep pass).
Handles JSON parsing, malformed responses, timeouts, and model swapping.
"""

import os
import re
import json
import httpx
import tiktoken

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "qwen3-coder:latest")
MODEL_DEEP = os.getenv("OLLAMA_MODEL_DEEP", "deepseek-r1:32b")

_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _extract_json(raw: str) -> dict:
    """
    Extract valid JSON from model output.
    Handles: markdown fences, preamble text, deepseek thinking tags.
    """
    # Strip thinking tags (deepseek-r1 outputs <think>...</think>)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Strip markdown fences
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Find the outermost { ... } block
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response: {raw[:200]}")

    depth = 0
    end = start
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    candidate = raw[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse extracted JSON: {e}\nRaw: {candidate[:500]}")


def _validate_response(data: dict) -> dict:
    """Ensure the response matches our schema. Fill missing fields with defaults."""
    if "issues" not in data:
        data["issues"] = []
    if "summary" not in data:
        data["summary"] = "Review complete."
    if "style_violations" not in data:
        data["style_violations"] = []
    if "language" not in data:
        data["language"] = "unknown"

    validated_issues = []
    for i, issue in enumerate(data["issues"]):
        validated_issues.append({
            "id": issue.get("id", i + 1),
            "severity": issue.get("severity", "medium"),
            "location": issue.get("location", "unknown"),
            "problem": issue.get("problem", "Issue detected"),
            "explanation": issue.get("explanation", ""),
            "fix": issue.get("fix", ""),
            "rule_violated": issue.get("rule_violated", "unspecified"),
        })
    data["issues"] = validated_issues
    return data


async def unload_model(model: str):
    """Unload a model from VRAM to free space for another."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": model, "keep_alive": 0},
            )
    except Exception:
        pass  # Best effort -- if it fails, Ollama will handle it


async def preload_model(model: str):
    """Preload a model into VRAM."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": "10m"},
            )
    except Exception:
        pass


async def call_llm(
    messages: list[dict],
    model: str = None,
    timeout: float = 120.0,
) -> dict:
    """
    Call Ollama /api/chat and return parsed, validated JSON.

    Returns:
        {
            "result": dict,
            "raw": str,
            "model": str,
            "input_tokens": int,
            "output_tokens": int,
            "error": str | None,
        }
    """
    if model is None:
        model = MODEL_FAST

    input_text = " ".join(m["content"] for m in messages)
    input_tokens = count_tokens(input_text)

    # For deep model, allow longer timeout
    if model == MODEL_DEEP:
        timeout = max(timeout, 600.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 4096,
                    },
                },
            )
            resp.raise_for_status()

    except httpx.TimeoutException:
        return {
            "result": _validate_response({"issues": [], "summary": f"Request timed out after {timeout}s. The model may be loading into memory."}),
            "raw": "",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "error": f"Timeout after {timeout}s",
        }
    except httpx.HTTPError as e:
        return {
            "result": _validate_response({"issues": [], "summary": f"Ollama error: {e}"}),
            "raw": "",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "error": str(e),
        }

    body = resp.json()
    raw_output = body.get("message", {}).get("content", "")
    output_tokens = count_tokens(raw_output)

    try:
        parsed = _extract_json(raw_output)
        validated = _validate_response(parsed)
        error = None
    except (ValueError, json.JSONDecodeError) as e:
        validated = _validate_response({
            "issues": [],
            "summary": "Model returned malformed output.",
        })
        error = str(e)

    return {
        "result": validated,
        "raw": raw_output,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "error": error,
    }
