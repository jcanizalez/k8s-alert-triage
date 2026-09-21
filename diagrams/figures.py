#!/usr/bin/env python3
"""Build the figures as draw.io diagrams from results/ and data/, and export each to PNG.

Run: python3 diagrams/figures.py  (needs the draw.io desktop CLI)
"""
import datetime as dt
import html
import json
import shutil
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "diagrams"
DRAWIO = shutil.which("drawio") or "/Applications/draw.io.app/Contents/MacOS/draw.io"

INK, MUTED, RULE, SOFT = "#000000", "#444444", "#000000", "#F2F2F2"
GREEN, GREEN_D, GREEN_L = "#117733", "#117733", "#E3F0E7"
ORANGE, ORANGE_D, ORANGE_L = "#88661A", "#6B5014", "#F4EEDF"
BLUE, BLUE_D, BLUE_L = "#332288", "#332288", "#E7E5F2"
PURPLE, PURPLE_L = "#555555", "#EDEDED"
RED, RED_L = "#882255", "#F3E3EB"
FONT = "fontFamily=Times New Roman;"
MONO = "fontFamily=Courier New;"


class Figure:
    def __init__(self, name: str):
        self.name, self.cells, self.n = name, [], 1

    def _id(self) -> str:
        self.n += 1
        return f"c{self.n}"

    def box(self, x, y, w, h, label="", fill="#FFFFFF", stroke=RULE, color=INK, size=14, bold=False,
            align="center", valign="middle", mono=False, rounded=False, dashed=False, extra="", sub=None):
        cid = self._id()
        style = (f"rounded={int(rounded)};arcSize=6;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=1;"
                 f"fontColor={color};fontSize={size};fontStyle={1 if bold else 0};align={align};"
                 f"verticalAlign={valign};spacingLeft=8;spacingRight=8;{MONO if mono else FONT}"
                 f"{'dashed=1;' if dashed else ''}{extra}")
        value = html.escape(label).replace("\n", "<br>")
        if sub:
            value += (f"<br><span style='font-size:{size - 2}px;font-weight:normal;color:{MUTED}'>"
                      f"{html.escape(sub)}</span>")
        self.cells.append(f'<mxCell id="{cid}" value="{html.escape(value)}" style="{style}" vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def text(self, x, y, w, h, label, color=INK, size=14, bold=False, align="left", mono=False, italic=False):
        style_bits = (1 if bold else 0) + (2 if italic else 0)
        cid = self._id()
        style = (f"text;html=1;whiteSpace=wrap;fontColor={color};fontSize={size};fontStyle={style_bits};"
                 f"align={align};verticalAlign=middle;{MONO if mono else FONT}")
        value = html.escape(label).replace("\n", "<br>")
        self.cells.append(f'<mxCell id="{cid}" value="{html.escape(value)}" style="{style}" vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def dot(self, cx, cy, r, fill, stroke=None):
        cid = self._id()
        style = f"ellipse;html=1;fillColor={fill};strokeColor={stroke or fill};strokeWidth=2;"
        self.cells.append(f'<mxCell id="{cid}" value="" style="{style}" vertex="1" parent="1">'
                          f'<mxGeometry x="{cx - r}" y="{cy - r}" width="{2 * r}" height="{2 * r}" as="geometry"/></mxCell>')

    def line(self, x1, y1, x2, y2, color=MUTED, width=1.5, dashed=False, arrow=False):
        cid = self._id()
        style = (f"endArrow={'block' if arrow else 'none'};endFill=1;html=1;strokeColor={color};"
                 f"strokeWidth={width};{'dashed=1;' if dashed else ''}")
        self.cells.append(f'<mxCell id="{cid}" style="{style}" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
                          f'<mxPoint x="{x1}" y="{y1}" as="sourcePoint"/><mxPoint x="{x2}" y="{y2}" as="targetPoint"/>'
                          f'</mxGeometry></mxCell>')

    def edge(self, src, tgt, label="", color=MUTED, dashed=False, width=1.5, exit_=None, entry=None):
        cid = self._id()
        ports = ""
        if exit_:
            ports += f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;"
        if entry:
            ports += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeColor={color};"
                 f"strokeWidth={width};fontColor={color};fontSize=12;{FONT}labelBackgroundColor=#FFFFFF;"
                 f"{'dashed=1;' if dashed else ''}{ports}")
        self.cells.append(f'<mxCell id="{cid}" value="{html.escape(label)}" style="{style}" edge="1" parent="1" '
                          f'source="{src}" target="{tgt}"><mxGeometry relative="1" as="geometry"/></mxCell>')

    def write(self) -> Path:
        xml = ('<mxfile><diagram name="Page-1"><mxGraphModel background="#FFFFFF"><root>'
               '<mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(self.cells) +
               "</root></mxGraphModel></diagram></mxfile>")
        src = OUT / f"{self.name}.drawio"
        src.write_text(xml)
        png = OUT / f"{self.name}.drawio.png"
        subprocess.run([DRAWIO, "-x", "-f", "png", "-e", "-s", "2", "-b", "16", "-o", str(png), str(src)],
                       check=True, capture_output=True)
        src.unlink()
        return png


def load(name):
    return json.loads((ROOT / "results" / name).read_text())


def framework():
    f = Figure("framework")
    groups = [("Decide and collect", BLUE, BLUE_L, ["Choose the approach", "Choose the base model",
                                                    "Build a labelled dataset", "Split by incident"]),
              ("Train and measure", GREEN, GREEN_L, ["Format as chat", "Fine-tune with LoRA",
                                                    "Evaluate against baselines"]),
              ("Ship", PURPLE, PURPLE_L, ["Merge and quantize", "Serve in the cluster"])]
    x, step, prev = 0, 1, None
    for title, stroke, fill, steps in groups:
        w = 190
        h = 60 + len(steps) * 70
        f.box(x, 0, w, h, "", fill=fill, stroke=stroke, rounded=False)
        f.text(x + 12, 8, w - 24, 30, title, color=stroke, size=14, bold=True)
        for i, s in enumerate(steps):
            cid = f.box(x + 15, 48 + i * 70, w - 30, 52, f"{step}. {s}", stroke=stroke, size=13)
            if prev and i == 0:
                f.edge(prev, cid, color=MUTED, exit_=(1, 0.5), entry=(0, 0.5))
            elif prev:
                f.edge(prev, cid, color=MUTED, exit_=(0.5, 1), entry=(0.5, 0))
            prev, step = cid, step + 1
        x += w + 40
    return f


def architecture():
    f = Figure("architecture")
    f.box(0, 0, 520, 330, "", fill=SOFT, stroke=RULE)
    f.text(14, 6, 200, 30, "Cluster A", color=MUTED, size=14, bold=True)
    am = f.box(24, 50, 140, 60, "Alertmanager", stroke=BLUE, bold=True)
    tr = f.box(214, 50, 150, 60, "triage service", sub="builds the prompt", stroke=INK, bold=True)
    ll = f.box(260, 190, 150, 64, "llama-server", sub="Qwen3-1.7B, q8_0", fill=GREEN_L, stroke=GREEN, bold=True)
    st = f.box(24, 190, 150, 64, "Kubernetes API", sub="pods, services, events", fill=PURPLE_L, stroke=PURPLE, bold=True)
    f.edge(am, tr, "webhook")
    f.edge(tr, ll, "prompt", exit_=(0.45, 1), entry=(0.15, 0))
    f.edge(ll, tr, "fault id", exit_=(0.6, 0), entry=(0.9, 1))
    f.edge(st, tr, "read only", color=PURPLE, dashed=True, exit_=(1, 0.5), entry=(0.1, 1))
    f.text(24, 280, 480, 40, "Alerts, pod state and events stay in the cluster. Only the verdict leaves.",
           color=MUTED, size=13, italic=True)
    cv = f.box(580, 50, 180, 110, "Central view", sub="verdicts from every cluster",
               fill=BLUE_L, stroke=BLUE, color=BLUE_D, bold=True)
    lg = f.box(580, 220, 180, 70, "Large model API", sub="escalation, not measured here",
               stroke=ORANGE, color=ORANGE_D, dashed=True, bold=True)
    f.edge(tr, cv, "verdict", color=BLUE_D, width=2, exit_=(1, 0.3), entry=(0, 0.3))
    f.edge(tr, lg, "unsure", color=ORANGE, dashed=True, exit_=(1, 0.8), entry=(0, 0.5))
    cb = f.box(0, 360, 520, 50, "Cluster B, C, ...  the same two workloads in each", fill=SOFT, stroke=RULE,
               color=MUTED, bold=True)
    f.edge(cb, cv, "verdicts", color=BLUE_D, width=2, exit_=(1, 0.5), entry=(1, 0.75))
    return f


def windows():
    rows = [json.loads(l) for l in (ROOT / "data" / "alerts.jsonl").read_text().splitlines() if l.strip()]
    ts = lambda r: dt.datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00"))
    lo = dt.datetime(2026, 9, 16, 14, 43, tzinfo=dt.timezone.utc)
    pick = sorted((r for r in rows if lo <= ts(r) < lo + dt.timedelta(minutes=14)), key=ts)
    wins = {}
    for r in pick:
        start = ts(r) - dt.timedelta(seconds=float(r["seconds_since_injection"]))
        wins.setdefault(r["fault"], {"start": start, "alerts": []})["alerts"].append(r)
    t0 = min(w["start"] for w in wins.values())
    lanes = ["PodNotReady", "PodPending", "ContainerWaiting", "ServiceHasNoEndpoints",
             "DeploymentReplicasMismatch", "PodCrashLooping"]
    x0, span, px = 200, 15 * 60, 560
    X = lambda t: x0 + px * (t - t0).total_seconds() / span
    f = Figure("injection-windows")
    for i, name in enumerate(lanes):
        f.text(0, 70 + i * 30, 190, 24, name, color=MUTED, size=12, align="right")
    palette = [(GREEN, GREEN_L), (BLUE, BLUE_L), (PURPLE, PURPLE_L)]
    for (fault, w), (c, cl) in zip(sorted(wins.items(), key=lambda kv: kv[1]["start"]), palette):
        xs = X(w["start"])
        f.box(xs, 20, px * 300 / span - 4, 240, "", fill=cl, stroke=c, rounded=False)
        f.text(xs + 8, 24, 170, 36, f"{fault}\n{len(w['alerts'])} alerts", color=c, size=12, bold=True, mono=True)
        for r in w["alerts"]:
            lane = lanes.index(r["alert"]["labels"]["alertname"])
            f.dot(X(ts(r)), 82 + lane * 30, 7, c)
    f.line(x0, 280, x0 + px, 280, color=INK)
    for m in (0, 5, 10, 15):
        xm = x0 + px * m * 60 / span
        f.line(xm, 275, xm, 285, color=INK)
        f.text(xm - 30, 288, 60, 20, f"{m} min", color=MUTED, size=12, align="center")
    f.text(0, 320, 760, 24, "Every alert in a window is labelled with the fault injected at its start.", size=14, bold=True)
    return f


def records():
    f = Figure("two-alerts")
    cards = [
        ("PodCrashLooping", "crashloop_bad_command", "The pod explains the fault",
         [("frontend-55684596c5-5svdf", False, "5 restarts, last exit Error"),
          ("backend-7ff67445f6-mt8dx", True, "0 restarts"), ("loadgen-68f58f486d-hkl7k", True, "0 restarts")],
         ("frontend", "app=frontend", False), None),
        ("ServiceHasNoEndpoints", "wrong_service_selector", "Every pod is healthy; the Service is not",
         [("frontend-554f685c79-tlhjb", True, "0 restarts"), ("backend-7ff67445f6-mt8dx", True, "0 restarts"),
          ("loadgen-68f58f486d-hkl7k", True, "0 restarts")],
         ("frontend", "app=nothing-matches-this", True), "ImagePullBackOff x15, left over from the previous fault"),
    ]
    x = 0
    for alert, label, note, pods, svc, event in cards:
        f.box(x, 0, 380, 420 if event else 380, "", fill="#FFFFFF", stroke=RULE)
        f.box(x + 16, 16, 348, 44, f"{alert}", fill=ORANGE_L, stroke=ORANGE, color=ORANGE_D, bold=True, size=14,
              align="left")
        f.text(x + 16, 70, 200, 20, "PODS", color=MUTED, size=11, bold=True)
        for i, (name, ready, detail) in enumerate(pods):
            y = 94 + i * 40
            ok_fill, ok_stroke, ok_text = (GREEN_L, GREEN, "ready") if ready else (RED_L, RED, "not ready")
            f.box(x + 16, y, 348, 34, "", fill=SOFT if ready else RED_L, stroke=RULE if ready else RED)
            f.text(x + 26, y + 2, 200, 30, name.split("-")[0], size=13, bold=True, mono=True)
            f.box(x + 120, y + 7, 72, 20, ok_text, fill=ok_fill, stroke=ok_stroke, color=ok_stroke, size=11, bold=True)
            f.text(x + 200, y + 2, 160, 30, detail, color=RED if not ready else MUTED, size=12)
        yy = 94 + len(pods) * 40 + 12
        f.text(x + 16, yy, 200, 20, "SERVICE", color=MUTED, size=11, bold=True)
        name, sel, hot = svc
        f.box(x + 16, yy + 24, 348, 34, "", fill=RED_L if hot else SOFT, stroke=RED if hot else RULE)
        f.text(x + 26, yy + 26, 330, 30, f"{name}  selector {sel}", color=RED if hot else INK, size=12, bold=hot, mono=True)
        yy += 70
        if event:
            f.text(x + 16, yy, 200, 20, "EVENTS", color=MUTED, size=11, bold=True)
            f.text(x + 16, yy + 20, 348, 30, event, color=MUTED, size=12, italic=True)
            yy += 56
        f.box(x + 16, yy, 348, 36, f"label: {label}", fill=GREEN_L, stroke=GREEN, color=GREEN_D, bold=True,
              size=13, mono=True)
        f.text(x + 16, yy + 42, 348, 24, note, size=13, italic=True)
        x += 410
    return f


def messages():
    f = Figure("chat-example")
    f.box(0, 0, 640, 110, "", fill=SOFT, stroke=RULE)
    f.box(12, 12, 90, 26, "system", fill=PURPLE_L, stroke=PURPLE, color=PURPLE, bold=True, size=12)
    f.text(112, 8, 520, 34, "You triage Kubernetes alerts. Name the single fault that caused it.", size=13)
    f.text(12, 44, 616, 60, "Answer with exactly one of: bad_image_tag · crashloop_bad_command · "
           "dependency_scaled_to_zero · dns_broken · ... · none", color=MUTED, size=12, mono=True)
    f.box(0, 124, 640, 250, "", fill="#FFFFFF", stroke=RULE)
    f.box(12, 136, 90, 26, "user", fill=BLUE_L, stroke=BLUE, color=BLUE_D, bold=True, size=12)
    parts = [("ALERT", "ServiceHasNoEndpoints · critical · demo/frontend", ORANGE_L, ORANGE),
             ("PODS", "frontend ready, 0 restarts · backend ready, 0 restarts", SOFT, RULE),
             ("SERVICES", "frontend selector app=nothing-matches-this", SOFT, RULE),
             ("RECENT EVENTS", "last ten events in the namespace", SOFT, RULE)]
    for i, (k, v, fill, stroke) in enumerate(parts):
        y = 172 + i * 48
        f.box(12, y, 130, 40, k, fill=fill, stroke=stroke, bold=True, size=12)
        f.text(152, y, 480, 40, v, size=12, mono=True)
    f.box(0, 388, 640, 60, "", fill=GREEN_L, stroke=GREEN)
    f.box(12, 405, 90, 26, "assistant", fill="#FFFFFF", stroke=GREEN, color=GREEN_D, bold=True, size=12)
    f.text(112, 400, 400, 36, "wrong_service_selector", color=GREEN_D, size=14, bold=True, mono=True)
    f.text(660, 40, 150, 60, "same for every example", color=MUTED, size=12, italic=True)
    f.text(660, 230, 150, 60, "one alert and its namespace, about 700 tokens", color=MUTED, size=12, italic=True)
    f.text(660, 400, 150, 40, "the label: one id", color=MUTED, size=12, italic=True)
    return f


def template():
    f = Figure("chat-template")
    toks = [("<|im_start|>system", PURPLE_L, PURPLE), ("You triage ...", "#FFFFFF", RULE), ("<|im_end|>", PURPLE_L, PURPLE),
            ("<|im_start|>user", BLUE_L, BLUE), ("ALERT: ...", "#FFFFFF", RULE), ("<|im_end|>", BLUE_L, BLUE),
            ("<|im_start|>assistant", GREEN_L, GREEN), ("<think></think>", SOFT, RULE),
            ("wrong_service_selector", GREEN_L, GREEN), ("<|im_end|>", GREEN_L, GREEN)]
    x, y = 0, 0
    for label, fill, stroke in toks:
        w = 14 + 8.2 * len(label)
        if x + w > 760:
            x, y = 0, y + 50
        f.box(x, y, w, 36, label, fill=fill, stroke=stroke, size=12, mono=True)
        x += w + 8
    f.text(0, y + 50, 760, 24, "apply_chat_template() turns the three messages into this one string of tokens.",
           color=MUTED, size=13, italic=True)
    f.text(0, y + 74, 760, 24, "The empty think block is how Qwen3 marks thinking as off. Serving must use the same template.",
           color=MUTED, size=13, italic=True)
    return f


def lora():
    t = load("training.json")
    f = Figure("lora")
    f.text(0, 0, 300, 28, "Full fine-tuning", size=16, bold=True)
    f.box(0, 40, 220, 220, "W\n2048 × 2048", fill=GREEN_L, stroke=GREEN, color=GREEN_D, size=18, bold=True)
    f.text(0, 268, 220, 44, "4,194,304 weights\nall updated", align="center", size=13)
    f.text(300, 0, 300, 28, "LoRA, rank 16", size=16, bold=True)
    f.box(300, 40, 220, 220, "W\nfrozen", fill=SOFT, stroke=RULE, color=MUTED, size=18, bold=True)
    f.text(530, 130, 30, 40, "+", size=28, bold=True, align="center")
    f.box(566, 40, 22, 220, "", fill=GREEN_L, stroke=GREEN)
    f.text(548, 262, 60, 36, "B\n2048×16", color=GREEN_D, size=11, bold=True, align="center", mono=True)
    f.text(592, 130, 20, 40, "×", size=20, bold=True, align="center")
    f.box(616, 40, 150, 22, "", fill=GREEN_L, stroke=GREEN)
    f.text(616, 66, 150, 36, "A\n16×2048", color=GREEN_D, size=11, bold=True, align="center", mono=True)
    f.text(300, 300, 470, 24, "65,536 weights trained, 1.6% of this matrix", size=13, bold=True, align="center")
    f.box(0, 340, 770, 70, f"output = W·x + (α/r)·B·A·x  ·  α = 16, r = 16\n"
          f"7 projections × 28 layers: {t['trainable_params']:,} of {t['total_params']:,} weights trained "
          f"({t['trainable_share']:.2%})", fill=SOFT, stroke=RULE, size=13, align="left", mono=True)
    return f


def split():
    g = load("split-grouped.json")
    f = Figure("split-by-window")
    by_record = {0: [1], 2: [0], 3: [2], 5: [0, 2], 7: [1], 9: [2], 10: [0], 12: [1]}
    by_window = {1, 6, 11}
    for row, (title, sub, pick) in enumerate([
            ("Split by alert", "siblings of a test alert stay in training",
             lambda w: by_record.get(w, [])),
            ("Split by window", "each injection is on one side only",
             lambda w: [0, 1, 2] if w in by_window else [])]):
        y0 = row * 190
        f.text(0, y0, 400, 24, title, size=15, bold=True)
        f.text(0, y0 + 22, 400, 20, sub, color=MUTED, size=12)
        for w in range(14):
            x = w * 54
            tests = pick(w)
            leaked = 0 < len(tests) < 3
            f.box(x, y0 + 50, 44, 116, "", fill=RED_L if leaked else SOFT, stroke=RED if leaked else RULE)
            for k in range(3):
                test = k in tests
                f.dot(x + 22, y0 + 76 + k * 32, 11, "#FFFFFF" if test else BLUE, INK if test else BLUE)
    f.dot(8, 404, 8, BLUE)
    f.text(22, 394, 60, 20, "train", size=12)
    f.dot(98, 404, 8, "#FFFFFF", INK)
    f.text(112, 394, 60, 20, "test", size=12)
    f.box(170, 396, 16, 16, "", fill=RED_L, stroke=RED, rounded=False)
    f.text(192, 394, 300, 20, "window split across both sides", size=12)
    f.text(0, 424, 760, 24, f"This dataset: {g['total_windows']} windows, {g['test_windows']} held out, "
           f"{g['test_records']} test alerts, {g['shared_windows']} windows on both sides.", size=13, bold=True)
    return f


def gguf():
    path = ROOT / "models" / "qwen3-1.7b-triage-16bit-q8_0.gguf"
    fh = path.open("rb")
    u32 = lambda: struct.unpack("<I", fh.read(4))[0]
    u64 = lambda: struct.unpack("<Q", fh.read(8))[0]
    s = lambda: fh.read(u64()).decode("utf-8", "replace")
    fmts = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}

    def val(t):
        if t == 8:
            return s()
        if t == 9:
            et, n = u32(), u64()
            for _ in range(n):
                val(et)
            return n
        return struct.unpack(fmts[t], fh.read(struct.calcsize(fmts[t])))[0]

    assert fh.read(4) == b"GGUF"
    version, n_t, n_kv = u32(), u64(), u64()
    kv = {}
    for _ in range(n_kv):
        k = s()
        kv[k] = val(u32())
    types = {}
    for _ in range(n_t):
        s()
        [u64() for _ in range(u32())]
        t = u32()
        u64()
        types[t] = types.get(t, 0) + 1
    head, size = fh.tell(), path.stat().st_size
    f = Figure("gguf")
    f.text(0, 0, 760, 24, f"model.gguf, {size / 2**30:.2f} GiB, one file", size=15, bold=True, mono=True)
    f.box(0, 34, 560, 40, f"header · GGUF v{version} · {n_t} tensors · {len(kv)} metadata keys",
          fill=BLUE_L, stroke=BLUE, color=BLUE_D, bold=True, size=13, mono=True)
    meta = [f"architecture = {kv['general.architecture']}", f"block_count = {kv['qwen3.block_count']}",
            f"context_length = {kv['qwen3.context_length']:,}",
            f"tokenizer.tokens = [{kv['tokenizer.ggml.tokens']:,}]",
            f"chat_template = {len(kv['tokenizer.chat_template']):,} characters"]
    f.box(0, 84, 560, 16 + 15 * (len(meta) + 1), "metadata\n" + "\n".join(meta), fill=PURPLE_L, stroke=PURPLE,
          size=12, align="left", valign="top", mono=True)
    y = 84 + 16 + 15 * (len(meta) + 1) + 10
    f.box(0, y, 560, 40, "tensor index · name, shape, type, offset", fill=SOFT, stroke=RULE, size=12, mono=True)
    f.box(0, y + 50, 560, 150, f"tensor data\n{types.get(8, 0)} tensors in Q8_0 · {types.get(0, 0)} norms in F32\n"
          f"{(size - head) / 2**30:.2f} GiB", fill=GREEN_L, stroke=GREEN, color=GREEN_D, size=14, bold=True)
    f.text(580, 34, 180, 90, f"everything above the weights: {head / 1e6:.1f} MB", color=MUTED, size=12, italic=True)
    f.text(580, y + 60, 180, 90, "llama-server needs only this file", color=MUTED, size=12, italic=True)
    return f


def quantization():
    f = Figure("quantization")
    weights = [0.2, -1.3, 4.1, 0.05]
    f.text(0, 0, 150, 24, "weights", color=MUTED, size=12, bold=True)
    for i, w in enumerate(weights):
        f.box(150 + i * 100, 0, 88, 34, f"{w}", size=14, mono=True)
    for row, (bits, q, color, fill) in enumerate([(8, 127, GREEN, GREEN_L), (4, 7, RED, RED_L)]):
        y = 70 + row * 150
        scale = q / 4.1
        f.text(0, y, 150, 24, f"{bits}-bit, scale {q}/4.1", color=color, size=12, bold=True)
        f.text(0, y + 44, 150, 24, "read back", color=MUTED, size=12, bold=True)
        for i, w in enumerate(weights):
            n = round(w * scale)
            back = n / scale
            lost = n == 0
            f.box(150 + i * 100, y, 88, 34, f"{n}", fill=fill, stroke=color, size=14, bold=True, mono=True)
            f.box(150 + i * 100, y + 40, 88, 34, f"{back:.2f}", fill=RED_L if lost else "#FFFFFF",
                  stroke=RED if lost else RULE, color=RED if lost else INK, size=14, mono=True)
    f.text(560, 70, 200, 74, "Every weight comes back close.", color=GREEN_D, size=13, bold=True)
    f.text(560, 220, 200, 74, "The two small weights round to 0 and are gone.", color=RED, size=13, bold=True)
    return f


def request_flow():
    f = Figure("request-flow")
    names = [("Alertmanager", BLUE), ("triage service", INK), ("Kubernetes API", PURPLE), ("llama-server", GREEN)]
    for i, (n, c) in enumerate(names):
        x = i * 190
        f.box(x, 0, 160, 40, n, stroke=c, bold=True, size=13)
        f.line(x + 80, 40, x + 80, 300, color=RULE, dashed=True)
    steps = [(0, 1, "POST /alert (webhook)", INK), (1, 2, "list pods, services, events", PURPLE),
             (1, 3, "chat completion, temperature 0", GREEN_D), (3, 1, "one fault id", GREEN_D),
             (1, 0, "200, verdict logged", INK)]
    for k, (a, b, label, c) in enumerate(steps):
        y = 70 + k * 48
        xa, xb = a * 190 + 80, b * 190 + 80
        f.line(xa, y, xb, y, color=c, arrow=True, width=1.8)
        f.text(min(xa, xb) + 6, y - 22, abs(xb - xa) - 12, 20, label, color=c, size=12, align="center")
    return f


FIGURES = [framework, architecture, windows, records, messages, template, lora, split, gguf, quantization, request_flow]

if __name__ == "__main__":
    for build in FIGURES:
        print(build().write().name)
