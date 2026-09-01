# Nova DRL — PRE-200 Local Model Benchmark

This package continues the fixed 25-event / 140-fact PRE-200 bake-off.
It uses the exact same 25 page images and fixed gold facts so model/prompt changes are directly comparable.

## Why this benchmark is split into two stages

1. **Vision-only**: score the raw evidence emitted directly by a local multimodal model. This isolates visual reading/acquisition recall.
2. **Reason/classification**: feed that cached raw evidence into Nova's existing text reason model and score ANYWHERE vs RIGHT FIELD. This isolates schema/classification loss.

The previously measured v1.5.2 end-to-end structured baseline is:
- ANYWHERE: 83/140 = 59.3%
- RIGHT FIELD: 66/140 = 47.1%

## Step 1 — current 8B model with the original production vision prompt

Smoke test first:

```bash
python3 tools/pre200_vision_model_benchmark.py --model qwen3-vl-drl:8b-q8-16k --prompt production --max-events 2
```

Then full 25:

```bash
python3 tools/pre200_vision_model_benchmark.py --model qwen3-vl-drl:8b-q8-16k --prompt production --show-misses
```

## Step 2 — same 8B model, but high-recall evidence prompt

```bash
python3 tools/pre200_vision_model_benchmark.py --model qwen3-vl-drl:8b-q8-16k --prompt high-recall --show-misses
```

This isolates whether the current prompt/early summarization is a major bottleneck even before changing models.

## Step 3 — run Nova's existing reason model on the better 8B vision cache

For example, if high-recall wins:

```bash
python3 tools/pre200_reason_from_vision_benchmark.py --vision-model qwen3-vl-drl:8b-q8-16k --vision-prompt high-recall --reason-model qwen25-drl:14b-q6-16k --show-misses
```

## Step 4 — stronger vision model, same high-recall prompt

Check Ollama version first:

```bash
ollama --version
```

If compatible and the model is not already present:

```bash
ollama pull qwen3-vl:30b
```

Then:

```bash
python3 tools/pre200_vision_model_benchmark.py --model qwen3-vl:30b --prompt high-recall --show-misses
```

The 30B model is intentionally tested with the SAME image set, prompt, scorer, and gold facts. It is a model-capability comparison rather than a pipeline change.

If 30B vision recall is materially better, run the same reason stage:

```bash
python3 tools/pre200_reason_from_vision_benchmark.py --vision-model qwen3-vl:30b --vision-prompt high-recall --reason-model qwen25-drl:14b-q6-16k --show-misses
```

## Cache / resume behavior

All model outputs are cached under:

`/opt/nova-drl/output/pre200_25_event_model_benchmark/`

and reason outputs under:

`/opt/nova-drl/output/pre200_25_event_reason_benchmark/`

Re-running a command resumes from cached pages. Add `--force` only when intentionally rerunning every page.

## Success target

For the vision-only stage, an 80/20 useful threshold is approximately **80–85% overall raw-evidence recall**, with failures/parts/PNs preferably **85–90%+** and repair actions **75–80%+**.

Do not change production ingestion from this benchmark alone. First compare all stages, then choose the simplest global architecture supported by the numbers.
