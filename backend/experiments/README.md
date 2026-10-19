# Doctor emotion → SP response study

Controlled experiment: 20 doctor utterances (5× empathetic / cold / rushing / baseline) on `acute_appendicitis`, measuring how injected attitude changes SP disclosure and tone.

## Run

```bash
cd backend
PYTHONPATH=. python3 experiments/run_emotion_response_study.py --mode inject --backend auto
```

- `--mode inject` (default): force gold `SpEmotionKey` into `build_sp_system_prompt` (isolates `emotional_model`).
- `--mode detect`: run `detect_sp_emotion_shift` (Omni if `DASHSCOPE_API_KEY`, else regex).
- `--backend auto|live|proxy`: `auto` uses live Qwen SP when API key present, else rule-based proxy aligned to case layers + emotion disclosure policy.

Outputs: `experiments/out/emotion_study_*.json` and `emotion_study_latest.json`.

## Correctness checks

Per case:

1. Prompt injection: `{emotion_state}` and all `{emo_*}` strings from case YAML appear in rendered `sp_agent` prompt; profile + info layers present.
2. Emotion key: inject mode requires applied key == gold.
3. Behavior / direction: length and deep-layer disclosure align with attitude (empathetic open; cold/rushing brief, omit deep).

Aggregate hypotheses H1–H4 are scored in the JSON report.
