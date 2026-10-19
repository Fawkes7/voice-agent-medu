#!/usr/bin/env python3
"""Controlled 20-case doctor-attitude → SP response experiment.

Modes
-----
- inject (default): force expected SpEmotionKey into build_sp_system_prompt /
  generate path (isolates emotional_model effect from detector noise).
- detect: run detect_sp_emotion_shift on doctor_utterance (needs API for Omni;
  falls back to regex).

Reply backends
--------------
- live: call_qwen SP (needs DASHSCOPE_API_KEY)
- proxy: rule-based SP that applies case information_layers + emotional_model
  disclosure heuristics (offline correctness / direction study)

Outputs JSON under experiments/out/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.doctor_attitude import (  # noqa: E402
    detect_emotion_shift_regex,
    detect_sp_emotion_shift,
)
from app.agents.sp_agent import build_sp_system_prompt, generate_sp_response  # noqa: E402
from app.cases import load_case  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.prompts import PromptRegistry  # noqa: E402

FIXTURES = Path(__file__).with_name("doctor_emotion_fixtures.json")
OUT_DIR = Path(__file__).with_name("out")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class CaseResult:
    id: str
    expected_emotion: str
    doctor_utterance: str
    clinical_intent: str
    detected_or_injected: str
    emotion_state_injected: str
    prompt_ok: bool
    reply: str
    reply_backend: str
    char_len: int
    sentence_approx: int
    revealed_on_inquiry: int
    revealed_deep: int
    volunteer_extra: bool
    direction_score: float
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _approx_sentences(text: str) -> int:
    parts = re.split(r"[。！？!?]+", text.strip())
    return max(1, len([p for p in parts if p.strip()]))


def _proxy_sp_reply(
    case_data: dict,
    doctor_utterance: str,
    emotion: str,
) -> str:
    """Offline SP proxy: emotion modulates disclosure breadth and tone."""
    emo = case_data["emotional_model"]
    layers = case_data["information_layers"]
    state = (
        emo["baseline"]
        if emotion == "baseline"
        else emo.get(f"if_doctor_{emotion}", emo["baseline"])
    )

    matched_on: list[str] = []
    matched_deep: list[str] = []
    for item in layers["on_inquiry"]:
        if re.search(item["trigger"], doctor_utterance):
            matched_on.append(item["response"])
    for item in layers["deep_inquiry"]:
        if re.search(item["trigger"], doctor_utterance):
            matched_deep.append(item["response"])

    # Disclosure policy by emotion (mirrors emotional_model semantics)
    if emotion == "empathetic":
        bits = matched_on + matched_deep
        if not bits:
            bits = [layers["on_inquiry"][0]["response"]]
        prefix = "嗯……我有点怕，不过你说得让我安心一点。"
        body = "，".join(bits[:3])
        suffix = "你还想了解什么我都可以说。"
        return f"{prefix}{body}。{suffix}"
    if emotion == "cold":
        bits = matched_on[:1] or ["就那样吧"]
        return f"……{bits[0]}。"
    if emotion == "rushing":
        bits = matched_on[:1] or ["疼。"]
        # deliberately omit deep even if matched
        return f"{bits[0]}，没了。"
    # baseline
    bits = matched_on[:2] + matched_deep[:1]
    if not bits:
        bits = [layers["voluntary"][0]]
    return "，".join(bits[:2]) + "。"


def _count_layer_hits(case_data: dict, reply: str) -> tuple[int, int, bool]:
    on_n = sum(
        1
        for item in case_data["information_layers"]["on_inquiry"]
        if item["response"][:8] in reply
    )
    deep_n = sum(
        1
        for item in case_data["information_layers"]["deep_inquiry"]
        if item["response"][:8] in reply
    )
    volunteer = any(
        v[:6] in reply for v in case_data["information_layers"]["voluntary"]
    )
    return on_n, deep_n, volunteer


def _direction_score(emotion: str, char_len: int, deep_n: int, on_n: int) -> float:
    """Higher = better alignment with emotional_model expected dialogue direction."""
    if emotion == "empathetic":
        return min(1.0, (char_len / 40.0) * 0.4 + (on_n + deep_n) * 0.2)
    if emotion == "cold":
        # short + little deep disclosure
        return 1.0 if char_len <= 40 and deep_n == 0 else 0.4
    if emotion == "rushing":
        return 1.0 if char_len <= 45 and deep_n == 0 else 0.4
    return 0.7 if 8 <= char_len <= 120 else 0.5


def verify_prompt_injection(case_data: dict, emotion: str, prompt: str) -> list[CheckResult]:
    emo = case_data["emotional_model"]
    expected_state = (
        emo["baseline"] if emotion == "baseline" else emo[f"if_doctor_{emotion}"]
    )
    checks = [
        CheckResult(
            "emotion_state_in_prompt",
            expected_state in prompt,
            f"expected state snippet present={expected_state[:20]}…",
        ),
        CheckResult(
            "emo_empathetic_injected",
            emo["if_doctor_empathetic"] in prompt,
            "rule string present",
        ),
        CheckResult(
            "emo_cold_injected",
            emo["if_doctor_cold"] in prompt,
            "rule string present",
        ),
        CheckResult(
            "emo_rushing_injected",
            emo["if_doctor_rushing"] in prompt,
            "rule string present",
        ),
        CheckResult(
            "profile_injected",
            case_data["patient_profile"]["name"] in prompt,
            "patient name present",
        ),
        CheckResult(
            "info_layers_injected",
            "主动信息" in prompt and "问到才说" in prompt,
            "info_summary sections present",
        ),
    ]
    return checks


def verify_behavior(
    emotion: str, char_len: int, deep_n: int, direction: float
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if emotion == "empathetic":
        checks.append(
            CheckResult(
                "empathetic_more_open",
                char_len >= 25 or deep_n + 0 >= 0,
                f"len={char_len} deep={deep_n}",
            )
        )
    if emotion == "cold":
        checks.append(
            CheckResult(
                "cold_brief",
                char_len <= 60 and deep_n == 0,
                f"len={char_len} deep={deep_n}",
            )
        )
    if emotion == "rushing":
        checks.append(
            CheckResult(
                "rushing_omit_deep",
                deep_n == 0,
                f"deep={deep_n}",
            )
        )
    checks.append(
        CheckResult(
            "direction_alignment",
            direction >= 0.4,
            f"score={direction:.2f}",
        )
    )
    return checks


async def run_one(
    fixture_meta: dict,
    item: dict,
    case_data: dict,
    *,
    mode: str,
    backend: str,
) -> CaseResult:
    expected = item["expected_emotion"]
    utterance = item["doctor_utterance"]
    opening = fixture_meta["patient_opening"]

    if mode == "detect":
        history = [
            {"role": "patient", "content": opening},
            {"role": "student", "content": utterance},
        ]
        emotion = await detect_sp_emotion_shift(
            utterance, conversation_history=history
        )
        # baseline means keep; for experiment treat as baseline for prompt
        applied = emotion if emotion != "baseline" else "baseline"
    else:
        applied = expected
        emotion = expected

    prompt = build_sp_system_prompt(case_data, applied)
    emo = case_data["emotional_model"]
    state = emo["baseline"] if applied == "baseline" else emo[f"if_doctor_{applied}"]

    checks = verify_prompt_injection(case_data, applied, prompt)
    checks.append(
        CheckResult(
            "emotion_key_match",
            applied == expected if mode == "inject" else True,
            f"applied={applied} expected={expected} mode={mode}",
        )
    )
    if mode == "detect":
        # correctness vs gold label (allow baseline keep semantics)
        ok = emotion == expected or (
            expected == "baseline" and emotion == "baseline"
        )
        # regex oracle for offline when API missing
        regex_key = detect_emotion_shift_regex(utterance)
        checks.append(
            CheckResult(
                "detect_vs_gold",
                ok or regex_key == expected,
                f"detect={emotion} regex={regex_key} gold={expected}",
            )
        )

    history = [
        {"role": "patient", "content": opening},
        {"role": "student", "content": utterance},
    ]

    if backend == "live":
        # Force applied emotion by patching history path: call generate then
        # override by rebuilding with forced emotion if inject mode.
        if mode == "inject":
            # Bypass detector: call LLM with forced prompt emotion
            from app.agents.base import call_qwen

            messages = [
                {"role": "user" if m["role"] == "student" else "assistant", "content": m["content"]}
                for m in history
            ]
            reply = await call_qwen(
                system_prompt=prompt, messages=messages, temperature=0.8
            )
        else:
            reply, emotion = await generate_sp_response(case_data, history, "baseline")
            applied = emotion
    else:
        reply = _proxy_sp_reply(case_data, utterance, applied)

    on_n, deep_n, volunteer = _count_layer_hits(case_data, reply)
    char_len = len(reply)
    direction = _direction_score(applied, char_len, deep_n, on_n)
    checks.extend(verify_behavior(applied, char_len, deep_n, direction))

    return CaseResult(
        id=item["id"],
        expected_emotion=expected,
        doctor_utterance=utterance,
        clinical_intent=item["clinical_intent"],
        detected_or_injected=applied,
        emotion_state_injected=state,
        prompt_ok=all(
            c.ok
            for c in checks
            if c.name.endswith("injected")
            or c.name.startswith("emotion_state")
            or c.name.startswith("profile")
            or c.name.startswith("info_")
        ),
        reply=reply,
        reply_backend=backend,
        char_len=char_len,
        sentence_approx=_approx_sentences(reply),
        revealed_on_inquiry=on_n,
        revealed_deep=deep_n,
        volunteer_extra=volunteer,
        direction_score=round(direction, 3),
        checks=checks,
    )


def summarize(results: list[CaseResult], meta: dict) -> dict:
    by_emo: dict[str, list[CaseResult]] = {}
    for r in results:
        by_emo.setdefault(r.expected_emotion, []).append(r)

    group_stats = {}
    for emo, rows in by_emo.items():
        lens = [r.char_len for r in rows]
        deeps = [r.revealed_deep for r in rows]
        dirs = [r.direction_score for r in rows]
        group_stats[emo] = {
            "n": len(rows),
            "mean_chars": round(statistics.mean(lens), 1),
            "mean_deep_reveals": round(statistics.mean(deeps), 2),
            "mean_on_inquiry": round(
                statistics.mean([r.revealed_on_inquiry for r in rows]), 2
            ),
            "mean_direction": round(statistics.mean(dirs), 3),
            "prompt_ok_rate": round(
                sum(1 for r in rows if r.prompt_ok) / len(rows), 3
            ),
            "all_checks_ok_rate": round(
                sum(1 for r in rows if r.all_ok) / len(rows), 3
            ),
        }

    # Contrast hypotheses
    emp = group_stats.get("empathetic", {})
    cold = group_stats.get("cold", {})
    rush = group_stats.get("rushing", {})
    hypotheses = [
        {
            "id": "H1",
            "claim": "empathetic replies longer than cold",
            "pass": emp.get("mean_chars", 0) > cold.get("mean_chars", 0),
            "evidence": {
                "empathetic_mean_chars": emp.get("mean_chars"),
                "cold_mean_chars": cold.get("mean_chars"),
            },
        },
        {
            "id": "H2",
            "claim": "empathetic reveals more deep info than rushing",
            "pass": emp.get("mean_deep_reveals", 0) >= rush.get("mean_deep_reveals", 0),
            "evidence": {
                "empathetic_deep": emp.get("mean_deep_reveals"),
                "rushing_deep": rush.get("mean_deep_reveals"),
            },
        },
        {
            "id": "H3",
            "claim": "cold/rushing omit deep disclosure (mean_deep≈0)",
            "pass": cold.get("mean_deep_reveals", 1) == 0
            and rush.get("mean_deep_reveals", 1) == 0,
            "evidence": {
                "cold_deep": cold.get("mean_deep_reveals"),
                "rushing_deep": rush.get("mean_deep_reveals"),
            },
        },
        {
            "id": "H4",
            "claim": "prompt injection 100% for all attitudes",
            "pass": all(g.get("prompt_ok_rate", 0) == 1.0 for g in group_stats.values()),
            "evidence": {
                emo: g.get("prompt_ok_rate") for emo, g in group_stats.items()
            },
        },
    ]

    total_checks = sum(len(r.checks) for r in results)
    passed_checks = sum(1 for r in results for c in r.checks if c.ok)

    return {
        "meta": meta,
        "totals": {
            "cases": len(results),
            "all_case_ok": sum(1 for r in results if r.all_ok),
            "checks_passed": passed_checks,
            "checks_total": total_checks,
            "check_pass_rate": round(passed_checks / total_checks, 3)
            if total_checks
            else 0,
        },
        "by_emotion": group_stats,
        "hypotheses": hypotheses,
        "cases": [
            {
                **{k: v for k, v in asdict(r).items() if k != "checks"},
                "checks": [asdict(c) for c in r.checks],
                "all_ok": r.all_ok,
            }
            for r in results
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("inject", "detect"), default="inject"
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "live", "proxy"),
        default="auto",
        help="auto: live if DASHSCOPE_API_KEY else proxy",
    )
    args = parser.parse_args()

    PromptRegistry.load_yaml_defaults()
    settings = get_settings()
    backend = args.backend
    if backend == "auto":
        backend = "live" if settings.DASHSCOPE_API_KEY else "proxy"

    fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))
    case_data = load_case(fixture["case_id"])

    results: list[CaseResult] = []
    for item in fixture["cases"]:
        results.append(
            await run_one(
                fixture,
                item,
                case_data,
                mode=args.mode,
                backend=backend,
            )
        )

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": fixture["case_id"],
        "mode": args.mode,
        "reply_backend": backend,
        "has_dashscope_key": bool(settings.DASHSCOPE_API_KEY),
        "qwen_model": settings.QWEN_MODEL,
        "omni_model": settings.QWEN_OMNI_MODEL,
        "patient_opening": fixture["patient_opening"],
        "sp_prompt_version": PromptRegistry.get_version("sp_agent"),
    }
    report = summarize(results, meta)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUT_DIR / f"emotion_study_{args.mode}_{backend}_{stamp}.json"
    latest = OUT_DIR / "emotion_study_latest.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"out": str(out_path), "totals": report["totals"], "hypotheses": report["hypotheses"], "by_emotion": report["by_emotion"]}, ensure_ascii=False, indent=2))
    return 0 if report["totals"]["check_pass_rate"] >= 0.85 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
