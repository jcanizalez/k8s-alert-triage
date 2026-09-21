#!/usr/bin/env python3
"""Draw the figures as SVG from results/ and render them to PNG with headless Edge.

Run: python3 diagrams/build.py
"""
import datetime as dt
import html
import json
import shutil
import struct
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "diagrams" / "src"
OUT = ROOT / "diagrams"
RESULTS = ROOT / "results"
EDGE = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"

W = 700
INK, MUTED, RULE, SOFT, GROUND = "#1F2328", "#5F6B76", "#D5DBE1", "#F3F5F7", "#FFFFFF"
GREEN, GREEN_D, GREEN_L = "#50C878", "#237A48", "#E6F6EC"
ORANGE, ORANGE_D, ORANGE_L = "#FF8C00", "#A85C00", "#FFF1E0"
BLUE, BLUE_D, BLUE_L = "#4A90D9", "#2B6CB0", "#E8F1FB"
PURPLE, PURPLE_L = "#9B59B6", "#F3EAF7"
RED, RED_L = "#C93C3C", "#FBE5E5"
SANS = "-apple-system, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, monospace"


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def text(x, y, s, size=16, fill=INK, weight=400, anchor="start", mono=False, italic=False):
    fam = MONO if mono else SANS
    style = ' font-style="italic"' if italic else ""
    return (f'<text x="{x}" y="{y}" font-family="{esc(fam)}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{style}>{esc(s)}</text>')


def rect(x, y, w, h, fill=GROUND, stroke=RULE, sw=1.5, r=4, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d}/>')


def line(x1, y1, x2, y2, stroke=MUTED, sw=1.5, dash=None, arrow=False):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    m = ' marker-end="url(#arrow)"' if arrow else ""
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"{d}{m}/>'


def box(x, y, w, h, title, sub=None, fill=GROUND, stroke=RULE, title_fill=INK):
    out = [rect(x, y, w, h, fill, stroke, 1.5)]
    cy = y + h / 2 + (-4 if sub else 6)
    out.append(text(x + w / 2, cy, title, 17, title_fill, 600, "middle"))
    if sub:
        out.append(text(x + w / 2, cy + 20, sub, 14, MUTED, 400, "middle"))
    return "".join(out)


def svg(h, body) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}">'
            f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/>'
            f'</marker></defs><rect width="{W}" height="{h}" fill="{GROUND}"/>{body}</svg>')


def load(name):
    return json.loads((RESULTS / name).read_text())


def fig_architecture():
    b = []
    b.append(rect(20, 20, 460, 330, SOFT, RULE, 1.5))
    b.append(text(36, 46, "Cluster A", 15, MUTED, 600))
    b.append(box(40, 70, 130, 60, "Alertmanager", "fires the alert", GROUND, BLUE))
    b.append(box(220, 70, 150, 60, "triage", "builds the prompt", GROUND, INK))
    b.append(box(220, 200, 150, 64, "llama-server", "Qwen3-1.7B q8_0", GREEN_L, GREEN, GREEN_D))
    b.append(box(34, 200, 166, 64, "Cluster state", "pods · services · events", PURPLE_L, PURPLE))
    b.append(line(170, 100, 216, 100, arrow=True))
    b.append(line(275, 134, 275, 196, arrow=True))
    b.append(line(315, 196, 315, 134, arrow=True))
    b.append(text(268, 170, "prompt", 13, MUTED, anchor="end"))
    b.append(text(322, 170, "fault id", 13, MUTED))
    b.append(line(200, 206, 236, 136, dash="5 4", arrow=True))
    b.append(text(196, 176, "read only", 13, PURPLE, 600, "end"))
    b.append(text(40, 312, "Alerts, pod state and events never leave the cluster.", 14, MUTED, italic=True))
    b.append(text(40, 332, "Only the verdict does.", 14, MUTED, italic=True))

    b.append(rect(520, 70, 160, 130, BLUE_L, BLUE, 1.5))
    b.append(text(600, 118, "Central view", 17, BLUE_D, 600, "middle"))
    b.append(text(600, 140, "verdicts only:", 14, MUTED, anchor="middle"))
    b.append(text(600, 158, "fault, workload,", 14, MUTED, anchor="middle"))
    b.append(text(600, 176, "confidence", 14, MUTED, anchor="middle"))
    b.append(line(370, 92, 516, 92, stroke=BLUE_D, sw=2, arrow=True))
    b.append(text(443, 84, "verdict", 13, BLUE_D, 600, "middle"))

    b.append(rect(500, 244, 124, 76, GROUND, ORANGE, 1.5, dash="6 4"))
    b.append(text(562, 272, "Large model", 16, ORANGE_D, 600, "middle"))
    b.append(text(562, 292, "API", 14, ORANGE_D, 600, "middle"))
    b.append(text(562, 310, "not measured here", 12, MUTED, anchor="middle", italic=True))
    b.append(line(370, 122, 512, 240, stroke=ORANGE, dash="6 4", arrow=True))
    b.append(text(430, 200, "unsure", 13, ORANGE_D, 600, "middle"))

    b.append(rect(20, 372, 460, 48, SOFT, RULE, 1.5))
    b.append(text(36, 402, "Cluster B, C, …   the same two pods in each", 15, MUTED, 600))
    b.append(f'<path d="M480,396 L650,396 L650,206" fill="none" stroke="{BLUE_D}" stroke-width="2" marker-end="url(#arrow)"/>')
    b.append(text(560, 388, "verdicts", 13, BLUE_D, 600, "middle"))
    return "fig1-architecture", 440, svg(440, "".join(b))


def fig_windows():
    rows = [json.loads(l) for l in (ROOT / "data" / "alerts.jsonl").read_text().splitlines() if l.strip()]

    def ts(r):
        return dt.datetime.fromisoformat(r["captured_at"].replace("Z", "+00:00"))

    rows.sort(key=ts)
    pick = [r for r in rows if dt.datetime(2026, 9, 16, 14, 43, tzinfo=dt.timezone.utc) <= ts(r)
            < dt.datetime(2026, 9, 16, 14, 57, tzinfo=dt.timezone.utc)]
    windows = {}
    for r in pick:
        start = ts(r) - dt.timedelta(seconds=float(r["seconds_since_injection"]))
        windows.setdefault(r["fault"], {"start": start, "alerts": []})["alerts"].append(r)
    t0 = min(w["start"] for w in windows.values())
    span = 15 * 60
    x0, x1 = 210, 670

    def X(t):
        return x0 + (x1 - x0) * (t - t0).total_seconds() / span

    b = []
    colours = [GREEN_D, BLUE_D, PURPLE]
    lanes = {"PodNotReady": 0, "PodPending": 1, "ContainerWaiting": 2, "ServiceHasNoEndpoints": 3,
             "DeploymentReplicasMismatch": 4, "PodCrashLooping": 5}
    top = 80
    for i, (fault, w) in enumerate(sorted(windows.items(), key=lambda kv: kv[1]["start"])):
        c = colours[i % 3]
        xs, xe = X(w["start"]), X(w["start"] + dt.timedelta(minutes=5))
        b.append(rect(xs, top, xe - xs, 200, SOFT, RULE, 1, 2))
        b.append(line(xs, top - 6, xs, top + 206, stroke=c, sw=2.5))
        b.append(text(xs + 6, top + 20, fault.replace("_", " "), 12, c, 700))
        b.append(text(xs + 6, top + 38, f"{len(w['alerts'])} alerts", 13, MUTED))
        for r in w["alerts"]:
            lane = lanes.get(r["alert"]["labels"]["alertname"], 5)
            cx, cy = X(ts(r)), top + 60 + lane * 24
            b.append(f'<circle cx="{cx:.1f}" cy="{cy}" r="6.5" fill="{c}"/>')
        b.append(text(xs, top - 14, "inject", 13, c, 600))
        b.append(text(xe - 4, top + 196, "revert", 13, MUTED, anchor="end"))
    for name, lane in lanes.items():
        b.append(text(x0 - 12, top + 64 + lane * 24, name, 12, MUTED, anchor="end"))
    b.append(line(x0, 300, x1, 300, stroke=INK, sw=1.2))
    for m in range(0, 16, 5):
        xm = x0 + (x1 - x0) * m * 60 / span
        b.append(line(xm, 296, xm, 304, stroke=INK, sw=1.2))
        b.append(text(xm, 322, f"{m} min", 13, MUTED, anchor="middle"))
    b.append(text(30, 360, "Every alert inside a window is labelled with the fault injected at its start.",
                  15, INK, 600))
    b.append(text(30, 382, "The noise class, none, comes from windows where nothing was injected.",
                  15, MUTED))
    b.append(text(30, 36, f"Real capture, {t0:%d %b %Y %H:%M} UTC, 15 minutes", 13, MUTED))
    return "fig2-injection-windows", 400, svg(400, "".join(b))


def fig_records():
    b = []
    rows_ = [
        ("PodCrashLooping", "crashloop_bad_command", "Pod frontend-55684596c5-5svdf is restarting repeatedly",
         [("backend-7ff67445f6-mt8dx", "ready", "0", "", False),
          ("frontend-55684596c5-5svdf", "NOT ready", "5", "last exit: Error", True),
          ("loadgen-68f58f486d-hkl7k", "ready", "0", "", False)],
         [("frontend", "app=frontend", False)], None,
         "The pod says what is wrong."),
        ("ServiceHasNoEndpoints", "wrong_service_selector", "Service frontend has no ready endpoints",
         [("backend-7ff67445f6-mt8dx", "ready", "0", "", False),
          ("frontend-554f685c79-tlhjb", "ready", "0", "", False),
          ("loadgen-68f58f486d-hkl7k", "ready", "0", "", False)],
         [("frontend", "app=nothing-matches-this", True)],
         "Warning Failed ImagePullBackOff ×15 (left over from the last fault)",
         "Every pod is healthy. The answer is one line in the Service."),
    ]
    y = 20
    for alert, label, summary, pods, svcs, event, note in rows_:
        card = len(b)
        b.append(text(40, y + 32, alert, 17, INK, 700, mono=True))
        lw = 12 + 8.6 * len(label)
        b.append(rect(680 - 20 - lw, y + 14, lw, 26, GREEN_L, GREEN, 1, 3))
        b.append(text(680 - 20 - lw / 2, y + 32, label, 14, GREEN_D, 600, "middle", mono=True))
        b.append(text(40, y + 58, summary, 14, MUTED))
        yy = y + 90
        b.append(text(40, yy, "pods", 13, MUTED, 600))
        for name, ready, restarts, extra, hot in pods:
            yy += 22
            if hot:
                b.append(rect(34, yy - 16, 632, 22, RED_L, RED_L, 0, 2))
            fill = RED if hot else INK
            b.append(text(40, yy, name, 14, fill, 600 if hot else 400, mono=True))
            b.append(text(300, yy, ready, 14, fill, 600 if hot else 400, mono=True))
            b.append(text(400, yy, f"{restarts} restarts", 14, fill, mono=True))
            b.append(text(510, yy, extra, 14, fill, 600, mono=True))
        yy += 30
        b.append(text(40, yy, "services", 13, MUTED, 600))
        for name, sel, hot in svcs:
            yy += 22
            if hot:
                b.append(rect(34, yy - 16, 632, 22, RED_L, RED_L, 0, 2))
            b.append(text(40, yy, name, 14, RED if hot else INK, 600 if hot else 400, mono=True))
            b.append(text(300, yy, f"selector {sel}", 14, RED if hot else INK, 600 if hot else 400, mono=True))
        if event:
            yy += 30
            b.append(text(40, yy, "events", 13, MUTED, 600))
            yy += 22
            b.append(text(40, yy, event, 14, MUTED, mono=True))
        yy += 34
        b.append(text(40, yy, note, 15, INK, 600, italic=True))
        h = yy + 20 - y
        b.insert(card, rect(20, y, 660, h, GROUND, RULE, 1.5))
        y += h + 20
    return "fig3-two-records", y, svg(y, "".join(b))


def fig_lora():
    t = load("training.json")
    trainable, total = t["trainable_params"], t["total_params"]
    b = []
    s = 200
    b.append(text(40, 36, "Full fine-tuning", 18, INK, 700))
    b.append(text(40, 58, "one attention matrix, q_proj", 14, MUTED))
    b.append(rect(40, 80, s, s, GREEN_L, GREEN, 2))
    b.append(text(140, 170, "W", 34, GREEN_D, 700, "middle"))
    b.append(text(140, 196, "2048 × 2048", 15, GREEN_D, 600, "middle", mono=True))
    b.append(text(140, 306, "4,194,304 weights", 16, INK, 600, "middle"))
    b.append(text(140, 326, "all of them updated", 14, MUTED, anchor="middle"))

    b.append(text(330, 36, "LoRA, rank 16", 18, INK, 700))
    b.append(text(330, 58, "the same matrix", 14, MUTED))
    b.append(rect(330, 80, s, s, SOFT, RULE, 2))
    b.append(text(430, 170, "W", 34, MUTED, 700, "middle"))
    b.append(text(430, 196, "frozen", 15, MUTED, 600, "middle"))
    b.append(text(548, 186, "+", 30, INK, 600, "middle"))
    b.append(rect(566, 80, 14, s, GREEN_L, GREEN, 2, 2))
    b.append(text(573, 300, "B", 18, GREEN_D, 700, "middle"))
    b.append(text(573, 318, "2048×16", 12, GREEN_D, anchor="middle", mono=True))
    b.append(rect(594, 80, 84, 14, GREEN_L, GREEN, 2, 2))
    b.append(text(636, 116, "A", 18, GREEN_D, 700, "middle"))
    b.append(text(636, 134, "16×2048", 12, GREEN_D, anchor="middle", mono=True))
    b.append(text(504, 350, "65,536 weights trained, 1.6% of this matrix", 16, INK, 600, "middle"))
    b.append(text(504, 370, "B and A drawn wider than scale; they are 16 wide", 13, MUTED, anchor="middle",
                  italic=True))

    b.append(rect(20, 400, 660, 96, SOFT, RULE, 1.5))
    b.append(text(40, 432, "output = W·x + (α / r) · B·A·x", 16, INK, 600, mono=True))
    b.append(text(660, 432, "α = 16, r = 16", 15, MUTED, anchor="end", mono=True))
    b.append(text(40, 462, f"Across 7 projections in each of 28 layers: {trainable:,} of {total:,}", 15, INK))
    b.append(text(40, 482, f"weights trained, {trainable / total:.2%}. The adapter file is 66 MB.", 15, INK))
    return "fig4-lora", 516, svg(516, "".join(b))


def fig_split():
    g = load("split-grouped.json")
    b = []
    n_windows, per = 14, 3
    gap, dot = 46, 11
    x0 = 40

    def row(y, title, sub, test_of):
        b.append(text(x0, y, title, 17, INK, 700))
        b.append(text(x0, y + 22, sub, 14, MUTED))
        leaked = 0
        for wi in range(n_windows):
            cx0 = x0 + wi * gap
            tests = test_of(wi)
            split = 0 < len(tests) < per
            if split:
                leaked += 1
                b.append(rect(cx0 - 6, y + 40, 40, 96, RED_L, RED, 1, 3))
            else:
                b.append(rect(cx0 - 6, y + 40, 40, 96, SOFT, RULE, 1, 3))
            for k in range(per):
                cy = y + 60 + k * 28
                if k in tests:
                    b.append(f'<circle cx="{cx0 + 14}" cy="{cy}" r="{dot}" fill="{GROUND}" stroke="{INK}" stroke-width="2.5"/>')
                else:
                    b.append(f'<circle cx="{cx0 + 14}" cy="{cy}" r="{dot}" fill="{BLUE}"/>')
        return leaked

    pattern = {0: [1], 2: [0], 3: [2], 5: [0, 2], 7: [1], 9: [2], 10: [0], 12: [1]}
    row(40, "Split by record", "each alert drawn on its own", lambda wi: pattern.get(wi, []))
    b.append(text(x0, 206, "Red: a test alert with siblings from the same injection in training.", 14, RED, 600))
    grouped = {1, 6, 11}
    row(262, "Split by window", "whole injections drawn together", lambda wi: list(range(per)) if wi in grouped else [])
    b.append(text(x0, 428, f"{g['total_windows']} windows, {g['test_windows']} held out, "
                           f"{g['test_records']} test alerts, {g['shared_windows']} windows on both sides.",
                  14, INK, 600))
    lx = 40
    b.append(f'<circle cx="{lx + 8}" cy="464" r="8" fill="{BLUE}"/>')
    b.append(text(lx + 24, 469, "train", 14, INK))
    b.append(f'<circle cx="{lx + 98}" cy="464" r="8" fill="{GROUND}" stroke="{INK}" stroke-width="2.5"/>')
    b.append(text(lx + 114, 469, "test", 14, INK))
    b.append(text(lx + 170, 469, "one column = one injection window, its alerts stacked", 14, MUTED))
    return "fig5-split-by-window", 492, svg(492, "".join(b))


def fig_per_class():
    tuned = load("training.json")["tuned"]["per_class"]
    opus = load("headtohead/anthropic_claude-opus-5.json")["per_class"]
    lite = load("headtohead/google_gemini-3.5-flash-lite.json")["per_class"]

    def frac(s):
        c, n = s.split("/")
        return int(c), int(n)

    rows = []
    for k in tuned:
        tc, n = frac(tuned[k])
        rows.append((k, n, tc, opus[k]["correct"], lite[k]["correct"]))
    rows.sort(key=lambda r: (-(r[2] - r[3]) / r[1], r[0]))
    series = [("Qwen3-1.7B tuned", GREEN), ("Opus", ORANGE), ("flash-lite", "#F2C48D")]
    b = []
    for i, (name, c) in enumerate(series):
        lx = 40 + i * 190
        b.append(rect(lx, 22, 16, 16, c, c, 0, 2))
        b.append(text(lx + 24, 36, name, 15, INK, 600))
    bx0, bx1 = 262, 630
    y = 66
    for k, n, tc, oc, lc in rows:
        b.append(text(250, y + 21, k, 14, INK, 500, "end", mono=True))
        for j, (v, (_, c)) in enumerate(zip((tc, oc, lc), series)):
            yy = y + j * 12
            wbar = (bx1 - bx0) * v / n
            b.append(rect(bx0, yy + 2, max(wbar, 1.5), 10, c, c, 0, 1))
            b.append(text(bx0 + wbar + 6, yy + 11, f"{v}/{n}", 11, MUTED))
        y += 46
    b.append(line(bx0, 60, bx0, y - 6, stroke=RULE))
    b.append(text(40, y + 16, "Held-out alerts answered correctly, per fault. Sorted by the tuned model's lead over Opus.",
                  13, MUTED))
    return "fig6-per-class", y + 34, svg(y + 34, "".join(b))


def fig_rounding():
    b = []
    lo, hi = 0.0, 0.04
    x0, x1 = 60, 640
    w, d = 0.0213, 0.0009
    amax = 0.08

    def X(v):
        return x0 + (x1 - x0) * (v - lo) / (hi - lo)

    fmts = [("16-bit", None, "the change survives"),
            ("8-bit, q8_0", amax / 127, "the change survives, one step apart"),
            ("4-bit", amax / 7, "both round to the same value: the change is gone")]
    b.append(text(40, 34, f"One weight before tuning ({w}) and after (+{d}), stored three ways", 16, INK, 700))
    y = 80
    for name, step, verdict in fmts:
        b.append(text(40, y, name, 16, INK, 700))
        ly = y + 50
        b.append(line(x0, ly, x1, ly, stroke=INK, sw=1.2))
        if step is None:
            for i in range(0, 201):
                xv = x0 + (x1 - x0) * i / 200
                b.append(line(xv, ly - 4, xv, ly + 4, stroke=RULE, sw=1))
            qa, qb = w, w + d
        else:
            k = 0
            while k * step <= hi + 1e-9:
                xv = X(k * step)
                b.append(line(xv, ly - 9, xv, ly + 9, stroke=MUTED, sw=1.3))
                k += 1
            qa, qb = round(w / step) * step, round((w + d) / step) * step
        b.append(f'<circle cx="{X(w):.1f}" cy="{ly - 26}" r="7" fill="{MUTED}"/>')
        b.append(f'<circle cx="{X(w + d):.1f}" cy="{ly - 26}" r="7" fill="{GREEN}"/>')
        b.append(line(X(w), ly - 19, X(qa), ly - 2, stroke=MUTED, sw=1.5))
        b.append(line(X(w + d), ly - 19, X(qb), ly - 2, stroke=GREEN_D, sw=1.5))
        lost = abs(qa - qb) < 1e-12
        b.append(text(x1, y, verdict, 14, RED if lost else GREEN_D, 600, "end"))
        y += 104
    for v in (0.0, 0.01, 0.02, 0.03, 0.04):
        b.append(text(X(v), y - 30, f"{v:.2f}", 12, MUTED, anchor="middle"))
    b.append(text(40, y, f"Illustration. Grids use one scale per block set by its largest weight, {amax}.", 13,
                  MUTED, italic=True))
    b.append(text(40, y + 18, "q4_k_m's real grid is finer than plain 4-bit; the direction of the effect is the same.",
                  13, MUTED, italic=True))
    return "fig7-rounding", y + 36, svg(y + 36, "".join(b))


def gguf_header(path: Path):
    f = path.open("rb")

    def u32():
        return struct.unpack("<I", f.read(4))[0]

    def u64():
        return struct.unpack("<Q", f.read(8))[0]

    def s():
        return f.read(u64()).decode("utf-8", "replace")

    fmts = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}

    def val(t):
        if t == 8:
            return s()
        if t == 9:
            et, n = u32(), u64()
            for _ in range(n):
                val(et)
            return ("array", n)
        return struct.unpack(fmts[t], f.read(struct.calcsize(fmts[t])))[0]

    assert f.read(4) == b"GGUF"
    version, n_tensors, n_kv = u32(), u64(), u64()
    kv = {}
    for _ in range(n_kv):
        k = s()
        kv[k] = val(u32())
    types = {}
    for _ in range(n_tensors):
        s()
        nd = u32()
        [u64() for _ in range(nd)]
        t = u32()
        u64()
        types[t] = types.get(t, 0) + 1
    return version, n_tensors, kv, types, f.tell(), path.stat().st_size


def fig_gguf():
    path = ROOT / "models" / "qwen3-1.7b-triage-16bit-q8_0.gguf"
    version, n_tensors, kv, types, header_end, size = gguf_header(path)
    b = []
    b.append(text(40, 34, f"{path.name}, {size / 2**30:.2f} GiB, one file", 15, INK, 700, mono=True))
    y = 56
    b.append(rect(40, y, 620, 44, BLUE_L, BLUE, 1.5))
    b.append(text(56, y + 28, f"GGUF  version {version}  ·  {n_tensors} tensors  ·  {len(kv)} metadata keys", 15,
                  BLUE_D, 600, mono=True))
    y += 56
    shown = [("general.architecture", kv["general.architecture"]),
             ("qwen3.block_count", kv["qwen3.block_count"]),
             ("qwen3.embedding_length", kv["qwen3.embedding_length"]),
             ("qwen3.context_length", f"{kv['qwen3.context_length']:,}"),
             ("general.file_type", f"{kv['general.file_type']}  (Q8_0)"),
             ("tokenizer.ggml.tokens", f"[{kv['tokenizer.ggml.tokens'][1]:,} strings]"),
             ("tokenizer.ggml.merges", f"[{kv['tokenizer.ggml.merges'][1]:,} pairs]"),
             ("tokenizer.chat_template", f"{len(kv['tokenizer.chat_template']):,} characters of Jinja")]
    h = 40 + 24 * len(shown)
    b.append(rect(40, y, 620, h, PURPLE_L, PURPLE, 1.5))
    b.append(text(56, y + 26, "metadata: what the model is and how to talk to it", 14, PURPLE, 700))
    for i, (k, v) in enumerate(shown):
        yy = y + 52 + i * 24
        b.append(text(56, yy, k, 14, INK, 400, mono=True))
        b.append(text(330, yy, f"= {v}", 14, INK, 600, mono=True))
    y += h + 12
    b.append(rect(40, y, 620, 72, SOFT, RULE, 1.5))
    b.append(text(56, y + 26, "tensor index: name, shape, type, offset", 14, MUTED, 700))
    for x, cell in ((56, "blk.0.attn_q.weight"), (290, "2048 × 2048"), (420, "Q8_0"), (500, "… 309 more")):
        b.append(text(x, y + 52, cell, 14, INK, mono=True))
    y += 84
    q8, f32 = types.get(8, 0), types.get(0, 0)
    b.append(rect(40, y, 620, 120, GREEN_L, GREEN, 1.5))
    b.append(text(56, y + 30, f"tensor data: {q8} tensors in Q8_0, {f32} small norm vectors in F32", 14, GREEN_D, 700))
    b.append(text(56, y + 58, f"{(size - header_end) / 2**30:.2f} GiB of the file", 22, GREEN_D, 700))
    b.append(text(56, y + 88, f"Everything above it is {header_end / 1e6:.1f} MB, most of it the tokenizer.", 14, INK))
    y += 140
    b.append(text(40, y, "No config.json, no tokenizer.json: llama-server needs only this file.", 15, INK, 600))
    return "fig8-gguf", y + 20, svg(y + 20, "".join(b))


def fig_waterfall():
    t = load("training-repeat.json")
    m = load("merge-test.json")
    q = load("quantizations.json")
    other = load("training.json")["tuned"]["accuracy"]
    opus = load("headtohead/anthropic_claude-opus-5.json")["accuracy"]
    bars = [("adapter, loaded", "16-bit, 3.2 GiB", m["adapter_as_loaded"]["accuracy"], GREEN),
            ("merged", "16-bit, 3.2 GiB", m["merged_16bit"]["accuracy"], GREEN),
            ("GGUF q8_0", "8-bit, 1.71 GiB", q["q8_0"]["accuracy"], GREEN),
            ("GGUF q4_k_m", "4-bit, 1.03 GiB", q["q4_k_m"]["accuracy"], RED)]
    assert abs(t["tuned"]["accuracy"] - bars[0][2]) < 1e-9
    top, base = 50, 370

    def Y(v):
        return base - (base - top) * v

    b = []
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        b.append(line(70, Y(v), 670, Y(v), stroke=RULE, sw=1))
        b.append(text(60, Y(v) + 5, f"{v:.0%}", 13, MUTED, anchor="end"))
    lo, hi = sorted((other, bars[0][2]))
    b.append(rect(70, Y(hi), 600, Y(lo) - Y(hi), GREEN_L, GREEN_L, 0, 0))
    b.append(text(668, Y(hi) - 24, "two identical runs:", 12, GREEN_D, 600, "end"))
    b.append(text(668, Y(hi) - 8, f"{lo:.1%} and {hi:.1%}", 12, GREEN_D, 600, "end"))
    b.append(line(70, Y(opus), 670, Y(opus), stroke=ORANGE, sw=2, dash="7 5"))
    b.append(text(668, Y(opus) - 7, f"Opus {opus:.1%}", 13, ORANGE_D, 600, "end"))
    bw, gap = 100, 45
    for i, (name, sub, acc, c) in enumerate(bars):
        x = 100 + i * (bw + gap)
        b.append(rect(x, Y(acc), bw, base - Y(acc), c, c, 0, 2))
        b.append(text(x + bw / 2, Y(acc) - 10, f"{acc:.1%}", 18, INK, 700, "middle"))
        b.append(text(x + bw / 2, base + 24, name, 14, INK, 600, "middle"))
        b.append(text(x + bw / 2, base + 44, sub, 12, MUTED, anchor="middle"))
    b.append(line(70, base, 670, base, stroke=INK, sw=1.2))
    b.append(text(40, base + 80, "One adapter, 124 held-out alerts, four ways of running it.", 14, INK, 600))
    return "fig9-where-tuning-goes", base + 100, svg(base + 100, "".join(b))


FIGURES = [fig_architecture, fig_windows, fig_records, fig_lora, fig_split, fig_per_class, fig_rounding,
           fig_gguf, fig_waterfall]


def render(name: str, h: int, body: str, profile: Path) -> None:
    (SRC / f"{name}.svg").write_text(body)
    page = profile.parent / f"{name}.html"
    page.write_text(f'<html><body style="margin:0;background:#fff">{body}</body></html>')
    png = OUT / f"{name}.png"
    png.unlink(missing_ok=True)
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                             f"--user-data-dir={profile}", "--force-device-scale-factor=2",
                             f"--window-size={W},{h}", f"--screenshot={png}", page.as_uri()],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        if png.exists() and png.stat().st_size > 0:
            time.sleep(0.5)
            break
        time.sleep(0.1)
    proc.terminate()
    proc.wait(timeout=10)
    if not png.exists():
        raise SystemExit(f"no screenshot for {name}")


def main() -> None:
    SRC.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    try:
        for fn in FIGURES:
            name, h, body = fn()
            render(name, h, body, tmp / "profile")
            print(f"{name}  {W}×{h}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
