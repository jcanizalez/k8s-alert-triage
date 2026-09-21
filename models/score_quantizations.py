"""Export an adapter as q8_0 and q4_k_m GGUFs, serve each with llama.cpp and score it, inside Colab.

Expects /content/adapter.zip, /content/baseline.py, /content/alerts.jsonl and /content/split.json.
Prints the results as a single RESULTS_JSON line.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

ADAPTER_ZIP = "/content/adapter.zip"
ADAPTER_DIR = "/content/adapter"
LCPP = "/content/llama.cpp"
OUT = "/content/quantizations.json"
THREADS = "2"
CTX = "2048"
QUANTS = ["q8_0", "q4_k_m"]


def sh(cmd: list, note: str, check: bool = True) -> int:
    print(f"--- {note} ---", flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout[-1200:], flush=True)
    if p.returncode != 0:
        print(p.stderr[-2000:], file=sys.stderr, flush=True)
        if check:
            raise SystemExit(f"{note} failed with {p.returncode}")
    return p.returncode


def layout() -> None:
    for d in ("/content/eval", "/content/data", "/content/results"):
        os.makedirs(d, exist_ok=True)
    for src, dst in (("/content/baseline.py", "/content/eval/baseline.py"),
                     ("/content/alerts.jsonl", "/content/data/alerts.jsonl"),
                     ("/content/split.json", "/content/results/split-grouped.json")):
        if os.path.exists(src):
            subprocess.run(["cp", src, dst], check=True)


def build_server() -> str:
    binary = f"{LCPP}/build/bin/llama-server"
    if os.path.exists(binary):
        return binary
    if not os.path.isdir(LCPP):
        sh(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp", LCPP],
           "clone llama.cpp")
    sh(["cmake", "-B", f"{LCPP}/build", "-S", LCPP, "-DLLAMA_CURL=OFF",
        "-DGGML_CUDA=ON", "-DCMAKE_BUILD_TYPE=Release"], "cmake configure")
    sh(["cmake", "--build", f"{LCPP}/build", "--target", "llama-server",
        "-j", str(os.cpu_count() or 4)], "build llama-server")
    if not os.path.exists(binary):
        raise SystemExit("llama-server missing after the build")
    return binary


def export(quant: str) -> str:
    from unsloth import FastLanguageModel

    model, tok = FastLanguageModel.from_pretrained(
        model_name=ADAPTER_DIR, max_seq_length=2048, load_in_4bit=False
    )
    out = f"/content/export-{quant}"
    model.save_pretrained_gguf(out, tok, quantization_method=quant)
    roots = [d for d in (f"{out}_gguf", out) if os.path.isdir(d)]
    found = [os.path.join(r, f) for base in roots
             for r, _, fs in os.walk(base) for f in fs if f.endswith(".gguf")]
    found = [f for f in sorted(set(found)) if quant.upper() in os.path.basename(f).upper()]
    if len(found) != 1:
        raise SystemExit(
            f"expected exactly one {quant} gguf under {roots}, found {found}")
    path = max(found, key=os.path.getsize)
    print(f"  {quant}: {path} ({os.path.getsize(path)/1048576:.0f} MB)", flush=True)
    return path


def serve(binary: str, model: str, port: int):
    proc = subprocess.Popen(
        [binary, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-c", CTX,
         "--reasoning", "off", "-np", "1", "-t", THREADS, "-ngl", "99"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(180):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5):
                return proc
        except (OSError, urllib.error.URLError):
            if proc.poll() is not None:
                raise SystemExit(f"llama-server exited early for {model}")
    proc.kill()
    raise SystemExit(f"llama-server never became healthy for {model}")


def score(tag: str, binary: str, model: str, port: int) -> dict:
    server = serve(binary, model, port)
    log = f"/content/score-{tag}.log"
    try:
        with open(log, "w") as fh:
            run = subprocess.Popen(
                [sys.executable, "-u", "/content/eval/baseline.py", "--model", tag,
                 "--endpoint", f"http://127.0.0.1:{port}/v1/chat/completions",
                 "--split", "/content/results/split-grouped.json"],
                stdout=fh, stderr=subprocess.STDOUT,
            )
            while run.poll() is None:
                time.sleep(20)
                try:
                    last = open(log).read().strip().splitlines()[-1][:110]
                except (IndexError, OSError):
                    last = "(starting)"
                print(f"  [{tag}] {last}", flush=True)
        if run.returncode != 0:
            print(open(log).read()[-2000:], file=sys.stderr, flush=True)
            raise SystemExit(f"scoring {tag} failed with {run.returncode}")
    finally:
        server.terminate()
        server.wait(timeout=30)
    return json.load(open(f"/content/results/headtohead/{tag}.json"))


def main() -> int:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gguf", "sentencepiece",
                    "protobuf"], check=True)
    layout()
    with zipfile.ZipFile(ADAPTER_ZIP) as zf:
        zf.extractall(ADAPTER_DIR)

    binary = build_server()
    print(f"server: {binary}", flush=True)

    results = {}
    for quant in QUANTS:
        path = export(quant)
        tag = f"triage-13class-{quant}"
        results[quant] = score(tag, binary, path, 8090 + len(results))
        print(f"  {quant}: {results[quant]['accuracy']:.1%} "
              f"F1 {results[quant]['macro_f1']:.3f}", flush=True)

    with open(OUT, "w") as fh:
        json.dump({q: {k: r[k] for k in ("accuracy", "macro_f1", "latency_mean_s",
                                         "latency_p95_s", "per_class")}
                   for q, r in results.items()}, fh, indent=2)
    payload = {q: {k: r[k] for k in ("accuracy", "macro_f1", "per_class")}
               for q, r in results.items()}
    print("RESULTS_JSON " + json.dumps(payload), flush=True)
    for q, r in results.items():
        print(f"{q}: {r['accuracy']:.1%} F1 {r['macro_f1']:.3f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
