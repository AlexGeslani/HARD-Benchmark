from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hard1.runners.common import canonical, sha, utc_now

SECRET_KEYS = ("api_key", "apikey", "authorization", "auth", "cookie", "password", "secret", "credential", "access_token", "refresh_token")
RESERVED_SETTINGS = {"model", "messages", "stream", "tools", "api_key", "authorization", "headers"}
CLASSIFIER_VERSION = "hard1-completion-v4"


def excluded_completion(outcome: str, classification: str) -> dict[str, Any]:
    return {"outcome": outcome, "classification": classification,
            "termination": outcome.lower(), "admissible": False, "scored": False,
            "score": None, "classifier_version": CLASSIFIER_VERSION}


def classify_exchange(body: Any, observation: dict[str, Any]) -> dict[str, Any] | None:
    """Transport/protocol only: never classify tool-turn answer semantics here."""
    status = observation.get("http_status")
    if isinstance(status, int) and not 200 <= status < 300:
        return excluded_completion("HTTP_ERROR", "transport")
    if observation.get("transport_outcome") == "http_exception":
        return excluded_completion("HTTP_EXCEPTION", "transport")
    if observation.get("response_body_bytes") == 0 or observation.get("transport_outcome") == "body_absent":
        return excluded_completion("TRANSPORT_EMPTY", "transport")
    if observation.get("parse_outcome") == "malformed_json":
        return excluded_completion("MALFORMED_JSON", "protocol")
    if not isinstance(body, dict):
        return excluded_completion("MALFORMED_COMPLETION", "protocol")
    if body.get("error") is not None:
        return excluded_completion("PROVIDER_ERROR", "protocol")
    return None


class CompletionFailure(RuntimeError):
    def __init__(self, terminal: dict[str, Any]):
        self.terminal = terminal
        super().__init__(terminal["outcome"])


OUTPUT_LIMIT_REASON = "OUTPUT_LIMIT"
EMPTY_OUTPUT_REASON = "EMPTY_OUTPUT"
TIMEOUT_REASON = "TIMEOUT"


class OutputLimitFailure(RuntimeError):
    """Parsed completion exhausted the configured output allowance with no
    scoreable visible output. Raised only after the raw completion is durably
    recorded; never used for transport/protocol/orchestrator defects."""

    def __init__(self, role: str):
        self.role = role
        super().__init__(f"{OUTPUT_LIMIT_REASON}:{role}")


class EmptyOutputFailure(RuntimeError):
    """Parsed normal stop delivered neither visible content nor tool calls."""

    def __init__(self, role: str):
        self.role = role
        super().__init__(f"{EMPTY_OUTPUT_REASON}:{role}")


def length_stop_without_visible_output(body: Any) -> bool:
    """True only for a parsed finish_reason=length completion with no scoreable
    visible output. Visible output is message content or tool calls; hidden
    reasoning never counts. Anything unparsed or malformed is not this condition."""
    if not isinstance(body, dict):
        return False
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    choice = choices[0]
    if choice.get("finish_reason") != "length":
        return False
    message = choice.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        return False
    if isinstance(content, str) and content.strip():
        return False
    if message.get("tool_calls"):
        return False
    return True


def stop_without_visible_output(body: Any) -> bool:
    """True only for a parsed normal stop with no visible content or tool calls."""
    if not isinstance(body, dict):
        return False
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        return False
    message = choice.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        return False
    return not (isinstance(content, str) and content.strip()) and not message.get("tool_calls")


def output_limit_completion(role: str) -> dict[str, Any]:
    """Owner-approved scored completion failure (HARD2 amendment
    completion-failure-scoring-01, owner approval 1546218534114299908):
    FAIL, score 0, reason OUTPUT_LIMIT, role-attributed. An admissible
    scheduled trial, not infrastructure invalidity."""
    return {"outcome": OUTPUT_LIMIT_REASON, "classification": "completion_failure",
            "termination": "completion_failure", "admissible": True, "scored": True,
            "score": 0, "finish_reason": "length",
            "completion_failure": {"reason": OUTPUT_LIMIT_REASON, "role": role},
            "classifier_version": CLASSIFIER_VERSION}


def empty_output_completion(role: str) -> dict[str, Any]:
    """Configured-model failure: normal stop without a delivered answer."""
    return {"outcome": EMPTY_OUTPUT_REASON, "classification": "completion_failure",
            "termination": "completion_failure", "admissible": True, "scored": True,
            "score": 0, "finish_reason": "stop",
            "completion_failure": {"reason": EMPTY_OUTPUT_REASON, "role": role},
            "classifier_version": CLASSIFIER_VERSION}


def timeout_completion(role: str, deadline_seconds: float) -> dict[str, Any]:
    """Configured-system failure: an attributed model request did not finish
    inside the campaign's pinned liveness deadline. It is one scored trial,
    never an automatic retry or a relaxation of native step limits."""
    if role not in ("candidate", "simulator"):
        raise ValueError("timeout completion requires a model role")
    return {"outcome": TIMEOUT_REASON, "classification": "completion_failure",
            "termination": "completion_failure", "admissible": True, "scored": True,
            "score": 0, "completion_failure": {"reason": TIMEOUT_REASON, "role": role,
            "deadline_seconds": deadline_seconds}, "classifier_version": CLASSIFIER_VERSION}


def classify_if_completion(body: Any, observation: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """IF-only pre-score gate. None admits the unchanged native IF scorer.

    A retained parsed envelope can establish length termination without an
    observed HTTP status. Unknown wire/body measurements never imply zero.
    """
    terminal = classify_exchange(body, observation or {})
    if terminal is not None: return terminal
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return excluded_completion("MALFORMED_COMPLETION", "protocol")
    choice = choices[0]
    message = choice.get("message")
    finish = choice.get("finish_reason")
    if not isinstance(message, dict) or not isinstance(finish, str) or not finish:
        return excluded_completion("MALFORMED_COMPLETION", "protocol")
    if finish == "length":
        return {**excluded_completion("LENGTH_STOP", "resource"), "finish_reason": finish}
    text = message.get("content")
    if text is not None and not isinstance(text, str):
        return excluded_completion("MALFORMED_COMPLETION", "protocol")
    if not isinstance(text, str) or not text.strip():
        outcome = "EMPTY_ANSWER" if message.get("reasoning_content") or message.get("reasoning") else "EMPTY_COMPLETION"
        return {**excluded_completion(outcome, "completion"), "finish_reason": finish}
    return None


def generation_parameters(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlist only; never project prompts, stop strings, headers or routing."""
    numeric = ("max_tokens", "max_completion_tokens", "n_predict", "temperature", "top_p", "top_k",
               "min_p", "seed", "frequency_penalty", "presence_penalty", "repeat_penalty")
    result = {k: payload[k] for k in numeric if _number(payload.get(k)) is not None}
    if isinstance(payload.get("stream"), bool): result["stream"] = payload["stream"]
    template = payload.get("chat_template_kwargs")
    if isinstance(template, dict) and isinstance(template.get("enable_thinking"), bool):
        result["chat_template_kwargs"] = {"enable_thinking": template["enable_thinking"]}
    return result


def completion_observation(body: Any, *, http_status: int | None = None,
                           response_body_bytes: int | None = None,
                           transport_outcome: str | None = None,
                           parse_outcome: str | None = None) -> dict[str, Any]:
    """Content-free channel and exchange facts; omitted is null, not measured zero."""
    value = body if isinstance(body, dict) else {}
    choices = value.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    build = value.get("system_fingerprint")
    return {"http_status": http_status, "http_status_observed": http_status is not None,
            "response_body_bytes": response_body_bytes, "transport_outcome": transport_outcome,
            "parse_outcome": parse_outcome,
            "choice_count": len(choices) if isinstance(choices, list) else None,
            "finish_reason": choice.get("finish_reason"),
            "channel_bytes": {k: len(message[k].encode("utf-8")) if isinstance(message.get(k), str) else None
                              for k in ("content", "reasoning_content", "reasoning")},
            "provider_build": build if isinstance(build, str) else None,
            "stop_flags": {k: value.get(k) if isinstance(value.get(k), (bool, int)) else None
                           for k in ("stopped_limit", "stopped_eos", "stopped_word", "truncated")}}


def redact(value: Any, secret_values: tuple[str, ...] = ()) -> Any:
    """Recursively scrub auth carriers and exact canaries without hiding token metrics."""
    secrets = tuple(s for s in secret_values if s)
    if isinstance(value, dict):
        return {k: ("<redacted>" if k.lower().replace("-", "_") in SECRET_KEYS else redact(v, secrets))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, tuple):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "<redacted>")
        return value
    return value


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def usage_fields(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    prompt = _number(value.get("prompt_tokens") if "prompt_tokens" in value else value.get("input_tokens"))
    output = _number(value.get("completion_tokens") if "completion_tokens" in value else value.get("output_tokens"))
    total = _number(value.get("total_tokens"))
    details = value.get("prompt_tokens_details") or value.get("input_tokens_details") or {}
    return {
        "input_tokens": prompt, "output_tokens": output, "total_tokens": total,
        "cache_read_tokens": _number(details.get("cached_tokens")),
        "cache_write_tokens": _number(details.get("cache_write_tokens")),
        "reasoning_tokens": _number((value.get("completion_tokens_details") or {}).get("reasoning_tokens")),
        "raw": raw if isinstance(raw, dict) else None,
        "provenance": "provider_usage" if isinstance(raw, dict) else "unavailable_provider_omitted",
    }


@dataclass
class EvidenceLog:
    root: Path
    campaign_id: str
    case_id: str
    attempt_id: str
    secret_env_names: list[str] | None = None

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def secrets(self) -> tuple[str, ...]:
        return tuple(os.environ.get(name, "") for name in (self.secret_env_names or []))

    def append(self, name: str, record: dict[str, Any]) -> None:
        path = self.root / name
        payload = redact({"campaign_id": self.campaign_id, "case_id": self.case_id, "attempt_id": self.attempt_id, **record}, self.secrets())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(canonical(payload) + "\n"); handle.flush(); os.fsync(handle.fileno())


class OpenAITransport:
    def __init__(self, endpoint: str, model: str, key_env: str, settings: dict[str, Any], evidence: EvidenceLog, role: str):
        bad = sorted(RESERVED_SETTINGS.intersection(settings))
        if bad: raise ValueError("reserved request setting: " + ", ".join(bad))
        self.endpoint, self.model, self.key_env = endpoint.rstrip("/"), model, key_env
        self.settings, self.evidence, self.role = dict(settings), evidence, role

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        request_settings = {k: v for k, v in self.settings.items() if k not in {"timeout", "num_retries"} and v is not None}
        override_values = overrides or {}
        bad = sorted(RESERVED_SETTINGS.intersection(override_values))
        if bad: raise ValueError("reserved request setting: " + ", ".join(bad))
        request_settings.update({k: v for k, v in override_values.items() if v is not None})
        extra = request_settings.pop("extra_body", None)
        if extra is not None:
            if not isinstance(extra, dict): raise ValueError("extra_body must be an object")
            overlap = RESERVED_SETTINGS.intersection(extra)
            if overlap: raise ValueError("reserved request setting: " + ", ".join(sorted(overlap)))
            request_settings.update(extra)
        payload = {**request_settings, "model": self.model, "messages": messages, "stream": False}
        if tools is not None: payload["tools"] = tools
        started_wall, started = utc_now(), time.monotonic()
        serialized = canonical(payload).encode()
        base = {"schema": "hard1-request-v1.1", "request_id": request_id, "role": self.role,
                "attempt_number": 1, "requested_model": self.model, "settings": request_settings,
                "request": payload, "started_at": started_wall, "classifier_version": CLASSIFIER_VERSION,
                "effective_generation_parameters": generation_parameters(json.loads(serialized)),
                "generation_parameters_provenance": "final_serialized_request"}
        self.evidence.append("request-events.jsonl", {"schema": "hard1-request-event-v1",
            "event": "STARTED", "request_id": request_id, "role": self.role,
            "started_at": started_wall, "started_monotonic": started})
        body = None; status = None; wire_length = None
        transport_outcome = None; parse_outcome = "not_attempted"
        try:
            key = os.environ.get(self.key_env, "")
            if not key: raise RuntimeError(f"missing credential environment variable for {self.role}: {self.key_env}")
            request = urllib.request.Request(self.endpoint + "/v1/chat/completions", data=serialized,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=(None if self.settings.get("timeout") is None else float(self.settings["timeout"]))) as response:
                status = getattr(response, "status", None)
                if status is None and hasattr(response, "getcode"): status = response.getcode()
                transport_outcome = "response_received"
                wire = response.read(); wire_length = len(wire) if wire is not None else None
                if wire is None: transport_outcome = "body_absent"
            # Known non-2xx and absent bodies precede JSON parsing.
            observation = completion_observation(None, http_status=status, response_body_bytes=wire_length,
                transport_outcome=transport_outcome, parse_outcome=parse_outcome)
            if (status is not None and not 200 <= status < 300) or wire_length == 0 or wire is None:
                raise CompletionFailure(classify_exchange(None, observation))
            try:
                body = json.loads(wire); parse_outcome = "parsed"
            except (ValueError, UnicodeError):
                parse_outcome = "malformed_json"
                raise CompletionFailure(excluded_completion("MALFORMED_JSON", "protocol"))
            observation = completion_observation(body, http_status=status, response_body_bytes=wire_length,
                transport_outcome=transport_outcome, parse_outcome=parse_outcome)
            terminal = classify_exchange(body, observation)
            if terminal is not None: raise CompletionFailure(terminal)
            duration = time.monotonic() - started
            timing = body.get("timings") if isinstance(body.get("timings"), dict) else None
            row = {**base, "completed_at": utc_now(), "duration_seconds": duration, "response": body,
                   "effective_model": body.get("model"), "finish_reason": observation["finish_reason"],
                   "completion_observation": observation, "exchange_terminal": None,
                   "usage": usage_fields(body.get("usage")), "raw_timing": timing,
                   "ttft_seconds": None, "ttft_provenance": "unavailable_nonstreaming",
                   "end_to_end_output_rate": _rate(usage_fields(body.get("usage"))["output_tokens"], duration, "client_monotonic"),
                   "server_decode_rate": _server_rate(timing), "error": None}
            self.evidence.append("requests.jsonl", row)
            self.evidence.append("request-events.jsonl", {"schema": "hard1-request-event-v1",
                "event": "COMPLETED", "request_id": request_id, "role": self.role,
                "completed_at": utc_now(), "duration_seconds": duration})
            return body
        except BaseException as exc:
            terminal = exc.terminal if isinstance(exc, CompletionFailure) else None
            if isinstance(exc, urllib.error.HTTPError):
                status = exc.code; transport_outcome = "http_error"
                terminal = excluded_completion("HTTP_ERROR", "transport")
            elif isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
                transport_outcome = "http_exception"
                terminal = excluded_completion("HTTP_EXCEPTION", "transport")
            observation = completion_observation(body, http_status=status, response_body_bytes=wire_length,
                transport_outcome=transport_outcome, parse_outcome=parse_outcome)
            self.evidence.append("requests.jsonl", {**base, "completed_at": utc_now(),
                "duration_seconds": time.monotonic() - started, "response": None,
                "usage": usage_fields(None),
                "completion_observation": observation, "exchange_terminal": terminal,
                "error": {"type": type(exc).__name__, "message": str(exc)}, "cancelled": isinstance(exc, KeyboardInterrupt)})
            self.evidence.append("request-events.jsonl", {"schema": "hard1-request-event-v1",
                "event": "FAILED", "request_id": request_id, "role": self.role,
                "completed_at": utc_now(), "duration_seconds": time.monotonic() - started,
                "error_type": type(exc).__name__})
            if terminal is not None and not isinstance(exc, CompletionFailure):
                raise CompletionFailure(terminal) from exc
            raise


def _rate(tokens: Any, seconds: Any, source: str) -> dict[str, Any]:
    return {"value": tokens / seconds if isinstance(tokens, (int, float)) and seconds else None,
            "numerator": tokens, "denominator_seconds": seconds, "units": "tokens/second", "source": source}


def _server_rate(timing: dict[str, Any] | None) -> dict[str, Any]:
    if not timing: return _rate(None, None, "unavailable_provider_omitted")
    tokens = _number(timing.get("predicted_n")); millis = _number(timing.get("predicted_ms"))
    return _rate(tokens, millis / 1000 if millis is not None else None, "provider_timing")


def _projection(r: dict[str, Any]) -> dict[str, Any]:
    usage = r.get("usage") or {}
    return {k: r.get(k) for k in ("campaign_id", "case_id", "attempt_id", "request_id", "role",
            "requested_model", "effective_model", "finish_reason", "started_at", "completed_at", "duration_seconds", "termination", "admissible")} | {
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"), "cache_read_tokens": usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"), "reasoning_tokens": usage.get("reasoning_tokens"),
        "usage_provenance": usage.get("provenance"), "end_to_end_output_rate": (r.get("end_to_end_output_rate") or {}).get("value"),
        "end_to_end_rate_basis": (r.get("end_to_end_output_rate") or {}).get("source"),
        "server_decode_rate": (r.get("server_decode_rate") or {}).get("value"),
        "server_rate_basis": (r.get("server_decode_rate") or {}).get("source"),
        "effective_settings": (generation_parameters(r["effective_generation_parameters"])
                               if isinstance(r.get("effective_generation_parameters"), dict) else None),
        "completion_observation": r.get("completion_observation") or completion_observation(r.get("response")),
        "classifier_version": r.get("classifier_version"),
        "generation_parameters_provenance": r.get("generation_parameters_provenance"),
        "error_type": (r.get("error") or {}).get("type"),
        "raw_timing": r.get("raw_timing"), "ttft_seconds": r.get("ttft_seconds"),
        "ttft_provenance": r.get("ttft_provenance"),
        "end_to_end_rate_definition": r.get("end_to_end_output_rate"),
        "server_decode_rate_definition": r.get("server_decode_rate"),
    }


def _aggregates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    numeric = ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
    for role in ("candidate", "simulator"):
        selected = [r for r in rows if r.get("role") == role]
        result[role] = {"requests": len(selected), "errors": sum(r.get("error_type") is not None for r in selected),
                        "field_coverage": {k: sum(r.get(k) is not None for r in selected) for k in numeric},
                        **{k: (sum(r[k] for r in selected if _number(r.get(k)) is not None)
                               if any(_number(r.get(k)) is not None for r in selected) else None) for k in numeric}}
    return result


def export_requests(paths: list[Path], output: Path, fmt: str) -> None:
    rows = [json.loads(line) for path in paths for line in path.read_text().splitlines() if line.strip()]
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    projected = [_projection(r) for r in rows]
    if fmt == "private-json": output.write_text(json.dumps(rows, indent=2) + "\n")
    elif fmt == "private-jsonl": output.write_text("".join(canonical(r) + "\n" for r in rows))
    elif fmt == "sanitized-json": output.write_text(json.dumps({"records": projected, "aggregates": _aggregates(projected)}, indent=2) + "\n")
    else:
        fields = list(projected[0]) if projected else list(_projection({}))
        with output.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
            for row in projected:
                writer.writerow({k: canonical(v) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    os.chmod(output, 0o600)
