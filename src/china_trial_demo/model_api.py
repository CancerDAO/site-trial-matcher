from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def _integer(name: str, default: int) -> int:
    try: return int(os.environ.get(name, str(default)))
    except ValueError as error: raise ValueError(f"{name}必须是整数") from error


def load_skill(skill_path: str | Path) -> str:
    entry = Path(skill_path).resolve()
    if entry.name != "SKILL.md" or not entry.is_file(): raise ValueError(f"无效Skill入口：{entry}")
    root = entry.parent; queue = [entry]; seen: set[Path] = set(); sections: list[str] = []
    while queue:
        path = queue.pop(0).resolve()
        if path in seen: continue
        try: path.relative_to(root)
        except ValueError as error: raise ValueError(f"Skill引用越界：{path}") from error
        seen.add(path); content = path.read_text(encoding="utf-8")
        sections.append(f"--- BEGIN {path.relative_to(root).as_posix()} ---\n{content}\n--- END {path.relative_to(root).as_posix()} ---")
        for match in LINK.finditer(content):
            target_text = match.group(1).split("#", 1)[0].strip()
            if not target_text or "://" in target_text: continue
            target = (path.parent / target_text).resolve()
            if target.suffix.lower() == ".md" and target.is_file(): queue.append(target)
    return "\n\n".join(sections)


def build_prompt(job: dict[str, Any]) -> str:
    expected = job.get("expected_trial_ids") or []
    if not expected: raise ValueError("模型任务缺少expected_trial_ids")
    instructions = load_skill(job["skill_path"])
    return ("执行以下中国临床试验排除预筛任务。严格遵守Skill和输出契约，只返回一个JSON对象。"
            "不得遗漏、增加或选择性处理试验。不要输出思考过程。保留项使用null证据，不重复标准。\n\n" + instructions + "\n\n--- TASK ---\n" +
            json.dumps({"mode": job["mode"], "patient": job["patient"], "trials": job["trials"], "expected_trial_ids": expected,
                        "validation_feedback": job.get("validation_feedback")}, ensure_ascii=False, separators=(",", ":")))


def extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I).strip()
    candidates = [text.strip(), *[m.group(1).strip() for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)]]
    fallback: dict[str, Any] | None = None
    for candidate in candidates:
        try: payload = json.loads(candidate)
        except json.JSONDecodeError: continue
        if isinstance(payload, dict):
            if "analyzed_trials" in payload: return payload
            fallback = payload
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try: payload, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError: continue
        if isinstance(payload, dict):
            if "analyzed_trials" in payload: return payload
            fallback = payload
    if fallback is not None: return fallback
    raise ValueError("模型未返回有效JSON对象")


def call_minimax(job: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not key: raise ValueError("请通过环境变量MINIMAX_API_KEY提供密钥")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
    if not base_url.startswith("https://"): raise ValueError("MINIMAX_BASE_URL必须使用HTTPS")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M2.7")
    body = {"model": model, "messages": [{"role": "system", "content": "只返回一个符合契约的JSON对象。"}, {"role": "user", "content": build_prompt(job)}],
            "temperature": float(os.environ.get("MINIMAX_TEMPERATURE", "0.1")), "max_completion_tokens": _integer("MINIMAX_MAX_COMPLETION_TOKENS", 8192)}
    request = urllib.request.Request(f"{base_url}/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    retries = _integer("MINIMAX_API_RETRIES", 5); timeout = _integer("MINIMAX_API_TIMEOUT_SECONDS", 180)
    started = time.perf_counter()
    for attempt in range(retries + 1):
        retry_delay = min(2**attempt, 30)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                raw = json.loads(response.read().decode("utf-8")); choice = (raw.get("choices") or [{}])[0] or {}
                if choice.get("finish_reason") in {"length", "max_tokens"}: raise RuntimeError("模型输出被截断")
                content = ((choice.get("message") or {}).get("content") or "")
                return extract_json(str(content)), {"model": raw.get("model") or model, "usage": raw.get("usage") or {},
                    "elapsed_ms": round((time.perf_counter()-started)*1000, 3), "attempts": attempt+1}
        except urllib.error.HTTPError as error:
            detail = error.read(1000).decode("utf-8", errors="replace")
            if error.code not in RETRYABLE or attempt == retries: raise RuntimeError(f"MiniMax HTTP {error.code}: {detail}") from error
            if error.code == 429:
                try: retry_delay = max(retry_delay, float(error.headers.get("Retry-After") or 0))
                except (TypeError, ValueError): pass
                retry_delay = max(retry_delay, 10)
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == retries: raise RuntimeError(f"MiniMax连接失败：{error}") from error
        time.sleep(retry_delay)
    raise AssertionError("unreachable")
