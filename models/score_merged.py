"""Score a LoRA adapter as loaded, then merged to 16-bit and reloaded with plain transformers.

Expects /content/adapter.zip, /content/eval.jsonl, /content/baseline.py and a copy of this file
at /content/score_merged.py. Writes /content/merge_test.json.
"""

import json
import os
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, "/content")
from baseline import macro_f1, parse  # noqa: E402

ADAPTER_ZIP = "/content/adapter.zip"
ADAPTER_DIR = "/content/adapter"
MERGED_DIR = os.environ.get("MERGED_DIR", "/content/merged16")
EVAL = "/content/eval.jsonl"
OUT = "/content/merge_test.json"
CHILD_OUT = "/content/merged_score.json"
SELF = "/content/score_merged.py"
FOURBIT = os.environ.get("FOURBIT", "0") != "0"


def score(model, tokenizer, rows: list, note: str) -> dict:
    """Score a model on the eval records, parsing answers as train_colab.py does."""
    import torch

    pairs, per_class, started = [], {}, time.time()
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
        ids = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **ids, max_new_tokens=48, do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        said = tokenizer.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
        want = row["messages"][-1]["content"].strip()
        got = parse(said)
        pairs.append((want, got))
        slot = per_class.setdefault(want, [0, 0])
        slot[1] += 1
        slot[0] += int(want == got)

    correct = sum(1 for g, p in pairs if g == p)
    result = {
        "note": note,
        "accuracy": correct / len(pairs) if pairs else 0.0,
        "macro_f1": macro_f1(pairs),
        "unparseable": sum(1 for _, p in pairs if p == "unparseable"),
        "seconds": round(time.time() - started, 1),
        "per_class": {k: f"{v[0]}/{v[1]}" for k, v in sorted(per_class.items())},
    }
    print(f"=== {note} ===", flush=True)
    print(f"accuracy {result['accuracy']:.1%}  macro F1 {result['macro_f1']:.3f}", flush=True)
    return result


def child_main() -> int:
    """Score the merged model with transformers alone; unsloth must never be imported here."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rows = [json.loads(line) for line in open(EVAL) if line.strip()]
    tok = AutoTokenizer.from_pretrained(MERGED_DIR)
    model = AutoModelForCausalLM.from_pretrained(
        MERGED_DIR, torch_dtype=torch.float16, device_map="cuda"
    )
    model.eval()
    result = score(model, tok, rows, "merged 16-bit through transformers")
    with open(CHILD_OUT, "w") as fh:
        json.dump(result, fh, indent=2)
    return 0


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "unsloth", "unsloth_zoo"], check=True
    )
    with zipfile.ZipFile(ADAPTER_ZIP) as zf:
        zf.extractall(ADAPTER_DIR)

    rows = [json.loads(line) for line in open(EVAL) if line.strip()]
    print(f"eval records: {len(rows)}", flush=True)

    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=ADAPTER_DIR, max_seq_length=2048, load_in_4bit=FOURBIT
    )
    FastLanguageModel.for_inference(model)
    before = score(model, tokenizer, rows, "adapter as loaded")

    model.save_pretrained_merged(MERGED_DIR, tokenizer, save_method="merged_16bit")
    print("merged to 16-bit", flush=True)

    child = subprocess.run([sys.executable, SELF, "--child"], capture_output=True, text=True)
    print(child.stdout[-3000:], flush=True)
    if child.returncode != 0:
        print(child.stderr[-3000:], file=sys.stderr, flush=True)
        raise SystemExit("scoring the merged model failed in the child process")
    after = json.load(open(CHILD_OUT))

    with open(OUT, "w") as fh:
        json.dump(
            {
                "records": len(rows),
                "adapter_as_loaded": before,
                "merged_16bit": after,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            fh,
            indent=2,
        )
    print(f"\n{before['accuracy']:.1%} -> {after['accuracy']:.1%} after the merge", flush=True)
    print("wrote " + OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(child_main() if "--child" in sys.argv else main())
