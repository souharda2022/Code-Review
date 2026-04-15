"""
Token routing pre-flight check.
Decides how to handle input based on total token count.

Routes:
  < 6,000  tokens  ->  send_as_is       (single call, full quality)
  6,000 - 20,000   ->  chunk_by_method  (split at method boundaries)
  20,000 - 28,000  ->  chunk_aggressive (50-line blocks with summary carry)
  > 28,000          ->  reject           (tell user to paste less)
"""

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def route_by_token_count(code: str, system_prompt_tokens: int = 500, rag_tokens: int = 600) -> dict:
    """
    Determine how to handle the input.

    Returns:
        {
            "route": "send_as_is" | "chunk_by_method" | "chunk_aggressive" | "reject",
            "code_tokens": int,
            "total_tokens": int,
            "reason": str,
        }
    """
    code_tokens = count_tokens(code)
    question_tokens = 20  # approximate
    total = system_prompt_tokens + rag_tokens + code_tokens + question_tokens

    if total < 6_000:
        return {
            "route": "send_as_is",
            "code_tokens": code_tokens,
            "total_tokens": total,
            "reason": f"Total {total} tokens -- well within single-call limit",
        }

    elif total < 20_000:
        return {
            "route": "chunk_by_method",
            "code_tokens": code_tokens,
            "total_tokens": total,
            "reason": f"Total {total} tokens -- will split at method boundaries",
        }

    elif total < 28_000:
        return {
            "route": "chunk_aggressive",
            "code_tokens": code_tokens,
            "total_tokens": total,
            "reason": f"Total {total} tokens -- will split into ~50-line blocks",
        }

    else:
        return {
            "route": "reject",
            "code_tokens": code_tokens,
            "total_tokens": total,
            "reason": f"Total {total} tokens exceeds safe limit (28,000). Please paste one method or class at a time.",
        }
