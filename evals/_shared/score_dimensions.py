"""Independent dimension judgments; no sibling outputs or reference input."""
from __future__ import annotations

import json
from pathlib import Path

from airadar.provider.judgment import require_reason_first
from airadar.scorer.five import FIVE_WEIGHTS


def dimension_prompts(prompt: dict, user: str) -> dict:
    """The prompt file explicitly owns each rubric; never infer prose boundaries."""
    return {name: {"system": f"{prompt['system']}\n\n{rubric}\n\n"
                  f'只返回JSON，先写不超过120字的reason，再写{name}整数。'
                  f'格式：{{"reason":"...","{name}":0}}', "user": user}
            for name, rubric in prompt["dimension_rubrics"].items()}


def combine_calls(calls: list[dict]) -> dict:
    if len(calls) != len(FIVE_WEIGHTS) or [c["dimension"] for c in calls] != list(FIVE_WEIGHTS):
        raise ValueError("requires exactly one call for each dimension in order")
    values, reasons = {}, []
    for call in calls:
        if call["status"] != "ok":
            raise ValueError("dimension call failed")
        name = call["dimension"]
        payload = json.loads(call["response_json"])
        reason = require_reason_first(payload, name)
        if set(payload) != {"reason", name} or type(payload[name]) is not int or not 0 <= payload[name] <= 10:
            raise ValueError("dimension response must contain only reason and integer 0..10")
        values[name] = payload[name]
        reasons.append(f"{name}: {reason}")
    # This is a code aggregation, not a single model-emitted response.
    return {"reason": "\n".join(reasons), **values}


def run_calls(key: str, prompts: dict, request: dict, chat_for_case, attempts: Path) -> list[dict]:
    """Sequential per case so outer worker limit is also the HTTP call limit."""
    calls = []
    for dimension, prompt in prompts.items():
        row = {"dimension": dimension, "prompt": prompt, "request": request, "status": "error"}
        try:
            response = chat_for_case(key)(stage=f"score-{dimension}", prompt=prompt, request=request)
            row.update({k: response.get(k) for k in
                        ("raw", "usage", "model", "provider", "requested_model", "attempt_id")})
            payload = response["json"]
            row["response_json"] = json.dumps(payload, ensure_ascii=False)
            row["reason"] = require_reason_first(payload, dimension)
            if set(payload) != {"reason", dimension} or type(payload[dimension]) is not int or not 0 <= payload[dimension] <= 10:
                raise ValueError("dimension response must contain only reason and integer 0..10")
            row["status"] = "ok"
        except Exception as exc:
            row["error"] = type(exc).__name__
            if isinstance(getattr(exc, "attempt_id", None), str):
                row["attempt_id"] = exc.attempt_id
                attempt = json.loads((attempts / f"{exc.attempt_id}.json").read_text())
                row.update({k: attempt.get(k) for k in ("raw", "usage", "provider", "requested_model")})
                row["model"] = attempt.get("actual_model")
        calls.append(row)
    return calls
