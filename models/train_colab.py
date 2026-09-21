#!/usr/bin/env python3
"""Fine-tune Qwen3-1.7B with LoRA on a Colab GPU and score it before and after.

Upload models/data/train.jsonl and eval.jsonl to /content, then run this file in the Colab
kernel. Writes /content/training.json, the adapter and a q8_0 GGUF.
"""
import json
import os
import re
import sys
import time

TRAIN = os.environ.get("TRAIN_PATH", "/content/train.jsonl")
EVAL = os.environ.get("EVAL_PATH", "/content/eval.jsonl")
OUT = os.environ.get("OUT_PATH", "/content/training.json")
MODEL = os.environ.get("BASE_MODEL", "unsloth/Qwen3-1.7B")
EPOCHS = float(os.environ.get("EPOCHS", "3"))
FOURBIT = os.environ.get("FOURBIT", "0") != "0"
MAX_SEQ = 4096

FAULTS = [
    "bad_image_tag",
    "crashloop_bad_command",
    "dependency_scaled_to_zero",
    "dns_broken",
    "init_container_failing",
    "liveness_probe_failing",
    "missing_configmap",
    "missing_secret",
    "readiness_probe_too_strict",
    "resource_quota_exceeded",
    "unschedulable_resources",
    "wrong_service_selector",
    "none",
]


def load(path: str) -> list:
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def gold(row: dict) -> str:
    return row["messages"][-1]["content"].strip()


def parse(text: str) -> str:
    low = (text or "").lower()
    hits = [f for f in FAULTS if re.search(rf"\b{re.escape(f)}\b", low)]
    return max(hits, key=lambda f: low.rfind(f)) if hits else "unparseable"


def macro_f1(pairs: list) -> float:
    labels = {g for g, _ in pairs}
    total = 0.0
    for label in labels:
        tp = sum(1 for g, p in pairs if g == label and p == label)
        fp = sum(1 for g, p in pairs if g != label and p == label)
        fn = sum(1 for g, p in pairs if g == label and p != label)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        total += 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return total / len(labels) if labels else 0.0


def score(model, tokenizer, rows: list, note: str) -> dict:
    from unsloth import FastLanguageModel

    FastLanguageModel.for_inference(model)
    pairs, per_class, started = [], {}, time.time()
    empty = 0
    for row in rows:
        try:
            prompt = tokenizer.apply_chat_template(
                row["messages"][:-1], tokenize=False, add_generation_prompt=True,
                enable_thinking=False,
            )
        except TypeError:
            prompt = tokenizer.apply_chat_template(
                row["messages"][:-1], tokenize=False, add_generation_prompt=True
            )
        ids = tokenizer(prompt, return_tensors="pt").to("cuda")
        out = model.generate(
            **ids, max_new_tokens=48, do_sample=False, pad_token_id=tokenizer.eos_token_id
        )
        said = tokenizer.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
        if not said.strip():
            empty += 1
        want, got = gold(row), parse(said)
        pairs.append((want, got))
        slot = per_class.setdefault(want, [0, 0])
        slot[1] += 1
        slot[0] += int(want == got)

    result = {
        "note": note,
        "accuracy": sum(w == g for w, g in pairs) / len(pairs),
        "macro_f1": macro_f1(pairs),
        "unparseable": sum(g == "unparseable" for _, g in pairs),
        "empty": empty,
        "seconds": round(time.time() - started, 1),
        "per_class": {k: f"{v[0]}/{v[1]}" for k, v in sorted(per_class.items())},
    }
    print(f"\n=== {note} ===", flush=True)
    print(
        f"accuracy {result['accuracy']:.1%}  macro F1 {result['macro_f1']:.3f}  "
        f"unparseable {result['unparseable']}  empty {result['empty']}  "
        f"({result['seconds']}s)",
        flush=True,
    )
    for k, v in result["per_class"].items():
        print(f"   {k:28s} {v}", flush=True)
    return result


def main() -> int:
    train_rows, eval_rows = load(TRAIN), load(EVAL)
    print(f"train {len(train_rows)}  eval {len(eval_rows)}", flush=True)

    trained_on = {json.dumps(r["messages"][1]["content"]) for r in train_rows}
    held = {json.dumps(r["messages"][1]["content"]) for r in eval_rows}
    leak = trained_on & held
    if leak:
        print(f"ABORT: {len(leak)} held-out prompts appear in training", file=sys.stderr)
        return 1

    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL, max_seq_length=MAX_SEQ, load_in_4bit=FOURBIT, dtype=None
    )
    before = score(model, tokenizer, eval_rows, f"{MODEL} untuned")

    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
        random_state=7,
    )
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"trainable {trainable:,} of {total:,} ({trainable / total:.2%})", flush=True)

    texts = [tokenizer.apply_chat_template(r["messages"], tokenize=False) for r in train_rows]
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=Dataset.from_dict({"text": texts}),
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=MAX_SEQ,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            warmup_steps=5,
            num_train_epochs=EPOCHS,
            learning_rate=2e-4,
            logging_steps=5,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=7,
            output_dir="/content/out",
            report_to="none",
        ),
    )
    stats = trainer.train()
    after = score(model, tokenizer, eval_rows, f"{MODEL} tuned")

    try:
        import torch

        hardware = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except Exception:
        hardware = "unknown"

    summary = {
        "base_model": MODEL,
        "quantization": "4-bit (bitsandbytes, via unsloth)" if FOURBIT else "16-bit base",
        "method": f"LoRA r=16, {EPOCHS} epochs",
        "trainable_params": trainable,
        "total_params": total,
        "trainable_share": round(trainable / total, 6),
        "train_records": len(train_rows),
        "eval_records": len(eval_rows),
        "train_runtime_s": round(getattr(stats, "metrics", {}).get("train_runtime", 0), 1),
        "hardware": hardware,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "untuned": before,
        "tuned": after,
    }
    with open(OUT, "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"\naccuracy {before['accuracy']:.1%} -> {after['accuracy']:.1%}", flush=True)
    print(f"macro F1 {before['macro_f1']:.3f} -> {after['macro_f1']:.3f}", flush=True)

    model.save_pretrained("/content/qwen3-1.7b-triage-lora")
    tokenizer.save_pretrained("/content/qwen3-1.7b-triage-lora")
    print(f"wrote {OUT} and the adapter", flush=True)

    try:
        model.save_pretrained_gguf(
            "/content/qwen3-1.7b-triage", tokenizer, quantization_method="q8_0"
        )
        print("wrote the q8_0 GGUF", flush=True)
    except Exception as exc:
        print(f"GGUF export failed ({type(exc).__name__}: {exc}); the adapter is safe",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
