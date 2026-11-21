#!/usr/bin/env python3
"""Builds every SVG in /assets from config.json and the GitHub API.

Usage:
    GH_TOKEN=xxxx python scripts/build.py      # real data (what the Action runs)
    python scripts/build.py --offline          # static cards + "waiting" placeholders

All numbers are computed once in fetch() and reused by every card, so the same
figure can never show two different values in two sections.
"""
import html
import json
import math
import os
import random
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

CY, VI, PK, GR, AM = "#22d3ee", "#a855f7", "#ec4899", "#4ade80", "#fbbf24"
BG, PANEL, TXT, MUT = "#0a0e1a", "#0d1117", "#e2e8f0", "#94a3b8"
PALETTE = [CY, VI, PK, GR, AM, "#64748b"]
MONO = "'JetBrains Mono','Fira Code',Consolas,monospace"
SANS = "'Segoe UI','Helvetica Neue',Arial,sans-serif"
W = 800

D = None  # filled by fetch(); None means "no live data yet"


# ----------------------------------------------------------------- helpers
def esc(s):
    return html.escape(str(s), quote=True)


def hx(c):
    return [int(c[i:i + 2], 16) for i in (1, 3, 5)]


def to_hex(v):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(x)))) for x in v)


def mix(a, b, t):
    A, B = hx(a), hx(b)
    return to_hex([A[i] + (B[i] - A[i]) * t for i in range(3)])


def ramp(t):
    t = max(0.0, min(1.0, t))
    return mix(CY, VI, t * 2) if t < 0.5 else mix(VI, PK, (t - 0.5) * 2)


def shade(c, f):
    return to_hex([x * f for x in hx(c)])


def fmt(n):
    if n is None:
        return "—"
    return str(n) if n < 10000 else f"{n / 1000:.1f}k"


def pct(count, total):
    return 0.0 if not total else count * 100.0 / total


BASE_DEFS = (
    f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{CY}"/>'
    f'<stop offset=".5" stop-color="{VI}"/><stop offset="1" stop-color="{PK}"/></linearGradient>'
    f'<linearGradient id="gl" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{W}" y2="0">'
    f'<stop offset="0" stop-color="{CY}"/><stop offset=".5" stop-color="{VI}"/>'
    f'<stop offset="1" stop-color="{PK}"/></linearGradient>'
    f'<linearGradient id="bd" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{CY}"/>'
    f'<stop offset="1" stop-color="{PK}"/></linearGradient>'
    '<filter id="glow" x="-20%" y="-60%" width="140%" height="220%">'
    '<feGaussianBlur stdDeviation="3.5" result="b"/><feMerge><feMergeNode in="b"/>'
    '<feMergeNode in="SourceGraphic"/></feMerge></filter>'
)


def svg(w, h, body, title, defs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-label="{esc(title)}"><title>{esc(title)}</title>'
        f"<defs>{BASE_DEFS}{defs}</defs>"
        f"<style>.m{{font-family:{MONO}}}.t{{font-family:{SANS};font-weight:800}}</style>"
        f"{body}</svg>"
    )


def card(x, y, w, h, r=16, fill=PANEL, op="1"):
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
        f'fill-opacity="{op}" stroke="url(#bd)" stroke-width="1.2"/>'
    )


def pulse(cx, cy, r=5, c=GR):
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{c}"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{c}">'
        f'<animate attributeName="r" values="{r};{r * 2.6:.1f};{r}" dur="2.4s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values=".8;0;.8" dur="2.4s" repeatCount="indefinite"/></circle>'
    )


def appear(content, begin):
    return (
        f'<g opacity="0">{content}<animate attributeName="opacity" from="0" to="1" '
        f'dur="0.25s" begin="{begin:.2f}s" fill="freeze"/></g>'
    )


def typed(cid, x, y, text, size, color, begin="0s", step=0.07, hold=0, cursor=False):
    """Typewriter effect: a clip rectangle grows one character at a time."""
    n, cw = len(text), size * 0.6
    vals = [round(i * cw, 1) for i in range(n + 1)]
    if hold:
        vals += [round(n * cw, 1)] * hold + [0] * 3
    dur = round(len(vals) * step, 2)
    rep = 'repeatCount="indefinite"' if hold else 'fill="freeze"'
    v = ";".join(str(a) for a in vals)
    out = (
        f'<clipPath id="{cid}"><rect x="{x}" y="{y - size * 1.2:.1f}" width="0" height="{size * 1.6:.1f}">'
        f'<animate attributeName="width" calcMode="discrete" values="{v}" dur="{dur}s" begin="{begin}" {rep}/>'
        f"</rect></clipPath>"
        f'<text class="m" x="{x}" y="{y}" font-size="{size}" fill="{color}" xml:space="preserve" '
        f'clip-path="url(#{cid})">{esc(text)}</text>'
    )
    if cursor:
        xs = ";".join(str(round(x + a, 1)) for a in vals)
        out += (
            f'<rect x="{x}" y="{y - size * 0.95:.1f}" width="{size * 0.5:.1f}" height="{size * 1.15:.1f}" fill="{color}">'
            f'<animate attributeName="x" calcMode="discrete" values="{xs}" dur="{dur}s" begin="{begin}" {rep}/>'
            f'<animate attributeName="opacity" values="1;0;1" dur="1s" repeatCount="indefinite"/></rect>'
        )
    return out


def window(w, h, tab):
    return (
        card(1, 1, w - 2, h - 2)
        + f'<path d="M1 46H{w - 1}" stroke="{CY}" stroke-opacity=".18"/>'
        + '<circle cx="28" cy="24" r="6.5" fill="#ff5f56"/><circle cx="50" cy="24" r="6.5" fill="#ffbd2e"/>'
        + '<circle cx="72" cy="24" r="6.5" fill="#27c93f"/>'
        + f'<text class="m" x="100" y="29" font-size="14" fill="{MUT}">{esc(tab)}</text>'
    )


def placeholder(w, h, title):
    body = (
        card(1, 1, w - 2, h - 2)
        + f'<text class="t" x="{w / 2}" y="{h / 2 - 4}" text-anchor="middle" font-size="22" fill="url(#g)">{esc(title)}</text>'
        + f'<text class="m" x="{w / 2}" y="{h / 2 + 24}" text-anchor="middle" font-size="14" fill="{MUT}">'
        "// live data appears after the first workflow run</text>"
    )
    return svg(w, h, body, f"{title} (waiting for first workflow run)")


# ----------------------------------------------------------------- static cards
def hero():
    name, role = CFG["name"], CFG["role"]
    rnd = random.Random(7)
    stars = ""
    for _ in range(80):
        x, y = rnd.randint(8, 792), rnd.randint(8, 292)
        r = rnd.choice([0.8, 1, 1.2, 1.6])
        d, b = rnd.uniform(3, 7), rnd.uniform(0, 4)
        stars += (
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="#fff" opacity=".5"><animate attributeName="opacity" '
            f'values=".12;.85;.12" dur="{d:.1f}s" begin="-{b:.1f}s" repeatCount="indefinite"/></circle>'
        )
    defs = "".join(
        f'<radialGradient id="n{i}"><stop offset="0" stop-color="{c}" stop-opacity=".5"/>'
        f'<stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient>'
        for i, c in enumerate([VI, CY, PK])
    )
    defs += (
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
        f'<path d="M40 0H0V40" fill="none" stroke="{CY}" stroke-opacity=".07"/></pattern>'
        f'<linearGradient id="st"><stop offset="0" stop-color="{CY}" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{CY}" stop-opacity=".7"/><stop offset="1" stop-color="{CY}" stop-opacity="0"/></linearGradient>'
        f'<linearGradient id="sp"><stop offset="0" stop-color="{PK}" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{PK}" stop-opacity=".6"/><stop offset="1" stop-color="{PK}" stop-opacity="0"/></linearGradient>'
    )
    nebula = (
        '<circle cx="170" cy="120" r="210" fill="url(#n0)"><animateTransform attributeName="transform" '
        'type="translate" values="0 0;40 18;0 0" dur="26s" repeatCount="indefinite"/></circle>'
        '<circle cx="640" cy="90" r="190" fill="url(#n1)"><animateTransform attributeName="transform" '
        'type="translate" values="0 0;-36 24;0 0" dur="32s" repeatCount="indefinite"/></circle>'
        '<circle cx="470" cy="270" r="180" fill="url(#n2)"><animateTransform attributeName="transform" '
        'type="translate" values="0 0;28 -20;0 0" dur="28s" repeatCount="indefinite"/></circle>'
    )
    streaks = ""
    for y, fill, dur, begin, ln in [(62, "st", 11, 0, 260), (238, "sp", 14, 4, 320), (284, "st", 17, 8, 220)]:
        streaks += (
            f'<rect x="-340" y="{y}" width="{ln}" height="2" fill="url(#{fill})">'
            f'<animateTransform attributeName="transform" type="translate" values="0 0;1250 0" dur="{dur}s" '
            f'begin="{begin}s" repeatCount="indefinite"/></rect>'
        )
    brackets = (
        f'<g fill="none" stroke="{CY}" stroke-width="2" stroke-opacity=".7">'
        '<path d="M14 38V14H38"/><path d="M786 38V14H762"/><path d="M14 262V286H38"/><path d="M786 262V286H762"/></g>'
    )
    tag = (
        f'<rect x="32" y="28" width="192" height="30" rx="15" fill="{PANEL}" fill-opacity=".8" stroke="{GR}" stroke-opacity=".7"/>'
        + pulse(52, 43)
        + f'<text class="m" x="68" y="48" font-size="13" font-weight="700" fill="{GR}">STATUS · BUILDING</text>'
    )
    line = f"> building {CFG['building']}"
    cw = 22 * 0.6
    x0 = round((W - len(line) * cw) / 2)
    body = (
        f'<rect width="{W}" height="300" fill="{BG}"/><rect width="{W}" height="300" fill="url(#grid)"/>'
        + nebula + stars + streaks + brackets + tag
        + f'<text class="t" x="{W / 2}" y="158" text-anchor="middle" font-size="56" fill="url(#g)" filter="url(#glow)">{esc(name)}</text>'
        + f'<text class="m" x="{W / 2}" y="204" text-anchor="middle" font-size="22" letter-spacing="2" fill="{MUT}">{esc(role)}</text>'
        + typed("hc", x0, 256, line, 22, GR, step=0.12, hold=14, cursor=True)
    )
    return svg(W, 300, body, f"{name} — {role}", defs)


def status_badge():
    body = (
        card(1, 1, 198, 34, 17, PANEL)
        + pulse(22, 18)
        + f'<text class="m" x="38" y="23" font-size="13" font-weight="700" fill="{GR}">STATUS · BUILDING</text>'
    )
    return svg(200, 36, body, "Status: building")


SECTIONS = [
    ("01", "ABOUT ME", "// who is behind the code"),
    ("02", "PROFILE HUD", "// live numbers from the github api"),
    ("03", "LIVE TERMINAL", "// run the shell"),
    ("04", "TECH STACK", "// tools i use"),
    ("05", "FEATURED PROJECTS", "// things i'm building"),
    ("06", "GITHUB STATS", "// stats and streak"),
    ("07", "PROFILE SUMMARY", "// languages and habits"),
    ("08", "PROTECTION", "// ownership"),
    ("09", "CURRENTLY", "// right now"),
]


def header(num, title, sub):
    pts = " ".join(
        f"{36 + 24 * math.cos(math.radians(60 * k - 90)):.1f},{36 + 24 * math.sin(math.radians(60 * k - 90)):.1f}"
        for k in range(6)
    )
    body = (
        f'<polygon points="{pts}" fill="{PANEL}" stroke="url(#bd)" stroke-width="1.8" filter="url(#glow)"/>'
        f'<text class="m" x="36" y="41" text-anchor="middle" font-size="15" font-weight="700" fill="{CY}">{num}</text>'
        f'<text class="t" x="76" y="40" font-size="30" fill="url(#g)" filter="url(#glow)">{esc(title)}</text>'
        f'<text class="m" x="78" y="60" font-size="14" fill="{MUT}">{esc(sub)}</text>'
        f'<line x1="76" y1="71" x2="796" y2="71" stroke="url(#gl)" stroke-opacity=".5" stroke-width="1.5"/>'
        f'<circle r="4.5" cy="71" fill="#fff" filter="url(#glow)"><animate attributeName="cx" from="76" to="796" '
        f'dur="5s" repeatCount="indefinite"/></circle>'
    )
    return svg(W, 80, body, f"{num} {title}")


def about():
    c = CFG
    items = []
    for i, s in enumerate(c["stack"]):
        items += [(f'"{s}"', CY)] + ([(", ", MUT)] if i < len(c["stack"]) - 1 else [])
    builds = []
    for i, p in enumerate(c["projects"]):
        builds += [(f'"{p["title"]}"', CY)] + ([(", ", MUT)] if i < len(c["projects"]) - 1 else [])
    lines = [
        [("class ", VI), ("Developer", AM), (":", MUT)],
        [("    handle", PK), (" = ", MUT), (f'"{c["login"]}"', CY)],
        [("    role", PK), (" = ", MUT), (f'"{c["role"]}"', CY)],
        [("    stack", PK), (" = ", MUT), ("[", MUT)] + items + [("]", MUT)],
        [("    builds", PK), (" = ", MUT), ("[", MUT)] + builds + [("]", MUT)],
        [("    focus", PK), (" = ", MUT), (f'"{c["building"]}"', GR)],
    ]
    h = 46 + 28 + len(lines) * 34 + 14
    body = window(W, h, "me.py")
    for i, parts in enumerate(lines):
        y = 46 + 44 + i * 34 - 6
        tsp = "".join(f'<tspan fill="{col}">{esc(t)}</tspan>' for t, col in parts)
        row = (
            f'<text class="m" x="30" y="{y}" font-size="16" fill="#475569">{i + 1}</text>'
            f'<text class="m" x="64" y="{y}" font-size="18" xml:space="preserve">{tsp}</text>'
        )
        body += appear(row, 0.3 + i * 0.3)
    return svg(W, h, body, "about me, shown as a Python class")


def terminal():
    c = CFG
    fs, cw, row = 17, 17 * 0.6, 28
    seq = [
        ("whoami", [c["login"]]),
        ("stack --primary", [c["stack"][0]]),
        ("stack --tools", [" · ".join(c["stack"][1:])]),
        ("now --building", [c["building"]]),
        ("ls ./projects", ["   ".join(p["title"] for p in c["projects"])]),
        ("status", ["BUILDING"]),
    ]
    out_cols = [CY, GR, TXT, AM, PK, GR]
    y, t, body = 46 + 40, 0.5, ""
    for k, (cmd, outs) in enumerate(seq):
        body += appear(f'<text class="m" x="30" y="{y}" font-size="{fs}" fill="{GR}">$</text>', t)
        body += typed(f"tc{k}", 30 + 2 * cw, y, cmd, fs, "#ffffff", begin=f"{t + 0.1:.2f}s", step=0.06)
        t += len(cmd) * 0.06 + 0.4
        for o in outs:
            y += row
            body += appear(
                f'<text class="m" x="{30 + 2 * cw}" y="{y}" font-size="{fs}" fill="{out_cols[k % 6]}" xml:space="preserve">{esc(o)}</text>', t
            )
            t += 0.35
        y += row + 6
    body += (
        f'<text class="m" x="30" y="{y}" font-size="{fs}" fill="{GR}">$</text>'
        f'<rect x="{30 + 2 * cw}" y="{y - 14}" width="9" height="18" fill="{GR}"><animate attributeName="opacity" '
        f'values="1;0;1" dur="1s" repeatCount="indefinite"/></rect>'
    )
    h = y + 36
    return svg(W, h, window(W, h, "bash") + body, "terminal showing who I am and what I use")


def pill_w(s):
    return int(len(s) * 11.5 + 44)


def marquee_row(items, y, reverse, dur):
    unit = list(items)
    while sum(pill_w(s) + 14 for s in unit) < 860:
        unit += items
    x, g = 0, ""
    for _ in range(2):
        for k, s in enumerate(unit):
            w, col = pill_w(s), PALETTE[k % 5]
            g += (
                f'<g transform="translate({x},{y})"><rect width="{w}" height="40" rx="20" fill="#111827" '
                f'stroke="{col}" stroke-opacity=".8"/><circle cx="20" cy="20" r="4" fill="{col}"/>'
                f'<text class="m" x="34" y="26" font-size="18" fill="{TXT}">{esc(s)}</text></g>'
            )
            x += w + 14
    half = x / 2
    a, b = (0, -half) if not reverse else (-half, 0)
    return (
        f'<g><animateTransform attributeName="transform" type="translate" from="{a} 0" to="{b} 0" '
        f'dur="{dur}s" repeatCount="indefinite"/>{g}</g>'
    )


def stack_marquee():
    items = CFG["stack"]
    h = 150
    defs = (
        f'<clipPath id="cl"><rect x="1" y="1" width="{W - 2}" height="{h - 2}" rx="16"/></clipPath>'
        f'<linearGradient id="fl"><stop offset="0" stop-color="{PANEL}"/><stop offset="1" stop-color="{PANEL}" stop-opacity="0"/></linearGradient>'
        f'<linearGradient id="fr"><stop offset="0" stop-color="{PANEL}" stop-opacity="0"/><stop offset="1" stop-color="{PANEL}"/></linearGradient>'
    )
    body = (
        card(1, 1, W - 2, h - 2)
        + '<g clip-path="url(#cl)">'
        + marquee_row(items, 24, False, 24)
        + marquee_row(list(reversed(items)), 84, True, 28)
        + '<rect x="1" y="1" width="60" height="148" fill="url(#fl)"/>'
        + f'<rect x="{W - 61}" y="1" width="60" height="148" fill="url(#fr)"/></g>'
    )
    return svg(W, h, body, "scrolling tags of my tech stack: " + ", ".join(items), defs)


def wrap(s, n):
    lines, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 <= n:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def project(p):
    tags = p.get("tags", [])
    lines = wrap(p["desc"], 34)[:3] if p.get("desc") else []
    btn_y = 150 if not lines else 158 + 24 * (len(lines) - 1) + 22
    h = btn_y + 38 + 28
    if p.get("icon") == "cursor":
        icon = (
            f'<g transform="translate(340,40)"><path d="M0 0L0 26L7 20L12 31L17 29L12 18L21 18Z" fill="url(#g)" filter="url(#glow)"/></g>'
        )
    else:
        icon = (
            '<g transform="translate(348,52)"><circle r="8" fill="url(#g)" filter="url(#glow)"/>'
            f'<circle r="16" fill="none" stroke="{CY}" stroke-width="1.5" stroke-dasharray="6 5">'
            '<animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="14s" repeatCount="indefinite"/></circle>'
            f'<circle r="22" fill="none" stroke="{VI}" stroke-opacity=".5"/></g>'
        )
    body = (
        card(1, 1, 398, h - 2)
        + f'<rect x="322" y="26" width="52" height="52" rx="12" fill="{BG}" stroke="url(#bd)" stroke-opacity=".7"/>'
        + icon
        + f'<text class="t" x="26" y="76" font-size="30" fill="url(#g)" filter="url(#glow)">{esc(p["title"])}</text>'
    )
    x = 26
    for tg in tags:
        w = len(tg) * 10 + 26
        body += (
            f'<rect x="{x}" y="98" width="{w}" height="28" rx="14" fill="{BG}" stroke="{CY}" stroke-opacity=".6"/>'
            f'<text class="m" x="{x + w / 2}" y="117" text-anchor="middle" font-size="14" fill="{CY}">{esc(tg)}</text>'
        )
        x += w + 8
    for i, ln in enumerate(lines):
        body += f'<text class="m" x="26" y="{158 + 24 * i}" font-size="15" fill="{MUT}">{esc(ln)}</text>'
    label = p.get("button", "VIEW REPO") + " →"
    bw = len(label) * 10 + 40
    body += (
        f'<rect x="26" y="{btn_y}" width="{bw}" height="38" rx="19" fill="url(#g)"/>'
        f'<text class="m" x="{26 + bw / 2}" y="{btn_y + 24}" text-anchor="middle" font-size="15" font-weight="700" fill="{BG}">{esc(label)}</text>'
    )
    return svg(400, h, body, f"Project card: {p['title']}")


def protection():
    owner = esc(CFG["owner"])
    shield = (
        f'<g transform="translate(78,85)" fill="none" stroke="url(#bd)" stroke-width="3" filter="url(#glow)">'
        '<path d="M0 -40L30 -27V6C30 26 13 37 0 44C-13 37 -30 26 -30 6V-27Z">'
        '<animate attributeName="stroke-opacity" values="1;.55;1" dur="4s" repeatCount="indefinite"/></path>'
        f'<path d="M-12 2L-3 11L14 -9" stroke="{GR}"/></g>'
    )
    body = (
        card(1, 1, W - 2, 168)
        + shield
        + f'<text class="t" x="140" y="72" font-size="28" fill="url(#g)" filter="url(#glow)">PROTECTED BY {owner.upper()}</text>'
        + f'<text class="m" x="140" y="104" font-size="16" fill="{TXT}">Everything here belongs to its author.</text>'
        + f'<text class="m" x="140" y="130" font-size="16" fill="{MUT}">Use it under each repo\'s license and give credit.</text>'
    )
    return svg(W, 170, body, f"Protected by {CFG['owner']}")


def currently():
    line = f"currently building: {CFG['building']}"
    body = (
        card(1, 1, W - 2, 98)
        + pulse(250, 50)
        + f'<text class="m" x="272" y="58" font-size="24" fill="{TXT}">{esc(line)}</text>'
    )
    return svg(W, 100, body, line)


def footer():
    def wave(base, amp, phase):
        pts = [f"{x},{base + amp * math.sin(x * 2 * math.pi / 400 + phase):.1f}" for x in range(0, 1621, 20)]
        return "M" + " L".join(pts) + f" L1620,150 L0,150 Z"

    body = (
        f'<rect width="{W}" height="150" fill="{BG}"/>'
        f'<path d="{wave(96, 14, 0)}" fill="url(#g)" fill-opacity=".18"><animateTransform attributeName="transform" '
        'type="translate" from="0 0" to="-400 0" dur="18s" repeatCount="indefinite"/></path>'
        f'<path d="{wave(110, 10, 2)}" fill="url(#g)" fill-opacity=".3"><animateTransform attributeName="transform" '
        'type="translate" from="-400 0" to="0 0" dur="24s" repeatCount="indefinite"/></path>'
        f'<text class="t" x="{W / 2}" y="62" text-anchor="middle" font-size="30" fill="url(#g)" filter="url(#glow)">Thanks for visiting</text>'
    )
    return svg(W, 150, body, "Thanks for visiting")


# ----------------------------------------------------------------- avatar
def halftone(size=220, n=44):
    try:
        from PIL import Image
    except ImportError:
        return ""
    p = ASSETS / "avatar.png"
    if not p.exists():
        return ""
    im = Image.open(p).convert("RGB")
    s = min(im.size)
    l, t = (im.width - s) // 2, (im.height - s) // 2
    im = im.crop((l, t, l + s, t + s)).resize((n, n), Image.Resampling.LANCZOS)
    bg, cs, out = im.getpixel((2, 2)), size / n, []
    for j in range(n):
        for i in range(n):
            cx, cy = (i + 0.5) * cs, (j + 0.5) * cs
            if math.hypot(cx - size / 2, cy - size / 2) > size / 2 - 2:
                continue
            r, g, b = im.getpixel((i, j))
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
            is_bg = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) < 40
            rad = cs * (0.12 if is_bg else 0.2 + 0.32 * lum)
            col = ramp((i + j) / (2.0 * n))
            op = ".3" if is_bg else "1"
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.2f}" fill="{col}" opacity="{op}"/>')
    return "".join(out)


# ----------------------------------------------------------------- data cards
def hud():
    c = CFG
    d = D or {}
    tiles = [("REPOS", d.get("repos")), ("STARS", d.get("stars")), ("CONTRIBS", d.get("contributions")), ("FOLLOWERS", d.get("followers"))]
    body = (
        card(1, 1, W - 2, 428)
        + f'<rect x="32" y="26" width="190" height="30" rx="15" fill="{BG}" stroke="{GR}" stroke-opacity=".7"/>'
        + pulse(52, 41)
        + f'<text class="m" x="68" y="46" font-size="13" font-weight="700" fill="{GR}">LIVE · PROFILE HUD</text>'
        + f'<text class="t" x="32" y="112" font-size="36" fill="url(#g)" filter="url(#glow)">{esc(c["name"])}</text>'
        + f'<text class="m" x="32" y="144" font-size="18" fill="{MUT}">{esc(c["role"])}</text>'
    )
    for i, (lab, val) in enumerate(tiles):
        x = 32 + i * 122
        body += (
            f'<rect x="{x}" y="172" width="112" height="92" rx="12" fill="{BG}" stroke="{CY}" stroke-opacity=".3"/>'
            f'<text class="m" x="{x + 14}" y="198" font-size="13" fill="{MUT}">{lab}</text>'
            f'<text class="t" x="{x + 14}" y="241" font-size="30" fill="#ffffff">{fmt(val)}</text>'
            f'<rect x="{x + 14}" y="252" width="40" height="3" rx="1.5" fill="url(#g)"/>'
        )
    body += f'<text class="m" x="32" y="300" font-size="12" fill="{MUT}">TOP LANGUAGES</text>'
    if d.get("lang_repo"):
        x = 32
        for k, (name, _) in enumerate(d["lang_repo"][:4]):
            w = len(name) * 10 + 28
            col = PALETTE[k]
            body += (
                f'<rect x="{x}" y="312" width="{w}" height="30" rx="15" fill="{BG}" stroke="{col}" stroke-opacity=".8"/>'
                f'<text class="m" x="{x + w / 2}" y="332" text-anchor="middle" font-size="15" fill="{col}">{esc(name)}</text>'
            )
            x += w + 8
    else:
        body += f'<text class="m" x="32" y="334" font-size="14" fill="{MUT}">// appears after the first workflow run</text>'
    stamp = f"synced from the GitHub API · {d['updated']}" if d else "// run the workflow to sync"
    body += f'<text class="m" x="32" y="404" font-size="13" fill="{MUT}">{esc(stamp)}</text>'
    # avatar
    body += (
        f'<g transform="translate(550,105)">{halftone()}</g>'
        f'<circle cx="660" cy="215" r="118" fill="none" stroke="url(#bd)" stroke-width="1.5" stroke-dasharray="3 9" stroke-linecap="round">'
        '<animateTransform attributeName="transform" type="rotate" from="0 660 215" to="360 660 215" dur="60s" repeatCount="indefinite"/></circle>'
    )
    return svg(W, 430, body, f"Profile HUD for {c['name']}")


def city():
    if not D:
        return placeholder(W, 380, "CONTRIBUTION CITY")
    weeks = D["weeks"]
    cw, ch, ox, oy, maxh = 15, 7.5, 405, 100, 76
    mx = max([v for wk in weeks for v in wk.values()] or [0])
    cells = [(i + j, i, j, wk.get(j, 0)) for i, wk in enumerate(weeks) for j in range(7)]
    cells.sort()
    a, b, towers = cw * 0.92, ch * 0.92, ""
    for _, i, j, cnt in cells:
        x, y = ox + (i - j) * cw, oy + (i + j) * ch
        if cnt == 0:
            h, col = 1.5, "#1e293b"
        else:
            t = cnt / mx
            h, col = 6 + t * maxh, ramp(t)
        top = y - h
        towers += (
            f'<polygon points="{x - a:.1f},{top + b:.1f} {x:.1f},{top + 2 * b:.1f} {x:.1f},{y + 2 * b:.1f} {x - a:.1f},{y + b:.1f}" fill="{shade(col, .6)}"/>'
            f'<polygon points="{x:.1f},{top + 2 * b:.1f} {x + a:.1f},{top + b:.1f} {x + a:.1f},{y + b:.1f} {x:.1f},{y + 2 * b:.1f}" fill="{shade(col, .4)}"/>'
            f'<polygon points="{x:.1f},{top:.1f} {x + a:.1f},{top + b:.1f} {x:.1f},{top + 2 * b:.1f} {x - a:.1f},{top + b:.1f}" fill="{col}"/>'
        )
    flat = [v for wk in weeks for v in wk.values()]
    active = sum(1 for v in flat if v > 0)
    body = (
        card(1, 1, W - 2, 378)
        + f'<text class="t" x="28" y="44" font-size="22" fill="url(#g)" filter="url(#glow)">CONTRIBUTION CITY</text>'
        + f'<text class="m" x="28" y="68" font-size="13" fill="{MUT}">last {len(weeks)} weeks · one tower per day</text>'
        + f'<text class="m" x="28" y="196" font-size="13" fill="{MUT}">BEST DAY</text>'
        + f'<text class="t" x="28" y="228" font-size="28" fill="#ffffff">{fmt(mx)}</text>'
        + f'<text class="m" x="28" y="266" font-size="13" fill="{MUT}">ACTIVE DAYS</text>'
        + f'<text class="t" x="28" y="298" font-size="28" fill="#ffffff">{active}<tspan font-size="16" fill="{MUT}"> / {len(flat)}</tspan></text>'
        + f'<text class="m" x="28" y="350" font-size="12" fill="{MUT}">less</text>'
    )
    for k in range(5):
        body += f'<rect x="{68 + k * 18}" y="339" width="14" height="14" rx="3" fill="{ramp(k / 4)}"/>'
    body += f'<text class="m" x="164" y="350" font-size="12" fill="{MUT}">more</text>'
    body += f'<g filter="url(#glow)">{towers}</g>'
    return svg(W, 380, body, f"Isometric contribution city, last {len(weeks)} weeks")


def languages():
    if not D:
        return placeholder(W, 200, "LANGUAGE STACK")
    pairs = D["lang_repo"]
    total = sum(c for _, c in pairs)
    rows = pairs[:6]
    h = 100 + max(len(rows), 1) * 50
    body = (
        card(1, 1, W - 2, h - 2)
        + f'<text class="t" x="28" y="44" font-size="22" fill="url(#g)" filter="url(#glow)">LANGUAGE STACK</text>'
        + f'<text class="m" x="28" y="68" font-size="13" fill="{MUT}">weighted by repository · {total} repos with a detected language</text>'
    )
    if not rows:
        body += f'<text class="m" x="28" y="120" font-size="16" fill="{MUT}">no public repos with a detected language yet</text>'
    for i, (name, cnt) in enumerate(rows):
        y = 92 + i * 50
        p = pct(cnt, total)
        bw = max(6.0, 440 * p / 100)
        body += (
            f'<text class="m" x="28" y="{y + 22}" font-size="17" fill="{TXT}">{esc(name)}</text>'
            f'<rect x="200" y="{y + 8}" width="440" height="16" rx="8" fill="#1e293b"/>'
            f'<rect x="200" y="{y + 8}" width="0" height="16" rx="8" fill="url(#g)" filter="url(#glow)">'
            f'<animate attributeName="width" from="0" to="{bw:.1f}" dur="1.2s" begin="{i * 0.12:.2f}s" fill="freeze"/></rect>'
            f'<text class="m" x="772" y="{y + 22}" text-anchor="end" font-size="17" font-weight="700" fill="{PALETTE[i]}">{p:.1f}%</text>'
        )
    return svg(W, h, body, "Language stack, weighted by repository")


def stats():
    if not D:
        return placeholder(W, 300, "GITHUB STATS")
    rows = [
        ("Total stars", D["stars"], AM),
        ("Repositories", D["repos"], CY),
        ("Commits · 12 mo", D["commits"], GR),
        ("Pull requests · 12 mo", D["prs"], VI),
        ("Issues · 12 mo", D["issues"], PK),
    ]
    body = card(1, 1, 388, 298) + card(411, 1, 388, 298)
    body += (
        f'<text class="t" x="24" y="44" font-size="22" fill="url(#g)" filter="url(#glow)">GITHUB STATS</text>'
        f'<text class="t" x="435" y="44" font-size="22" fill="url(#g)" filter="url(#glow)">STREAK</text>'
    )
    for i, (lab, val, col) in enumerate(rows):
        y = 88 + i * 44
        body += (
            f'<circle cx="32" cy="{y - 5}" r="5" fill="{col}"/>'
            f'<text class="m" x="48" y="{y}" font-size="16" fill="{TXT}">{lab}</text>'
            f'<text class="m" x="364" y="{y}" text-anchor="end" font-size="19" font-weight="700" fill="{col}">{fmt(val)}</text>'
        )
    longest = max(D["longest"], 1)
    rings = [
        (476, D["contributions"], "CONTRIBUTIONS", "last 12 months", 1.0, CY),
        (605, D["current"], "CURRENT STREAK", "days", min(1.0, D["current"] / longest), GR),
        (734, D["longest"], "LONGEST STREAK", "days", 1.0, PK),
    ]
    r = 38
    C = 2 * math.pi * r
    for cx, val, lab, sub, frac, col in rings:
        body += (
            f'<circle cx="{cx}" cy="150" r="{r}" fill="none" stroke="#1e293b" stroke-width="7"/>'
            f'<circle cx="{cx}" cy="150" r="{r}" fill="none" stroke="{col}" stroke-width="7" stroke-linecap="round" '
            f'stroke-dasharray="{C * frac:.1f} {C:.1f}" transform="rotate(-90 {cx} 150)" filter="url(#glow)"/>'
            f'<text class="m" x="{cx}" y="157" text-anchor="middle" font-size="21" font-weight="700" fill="#ffffff">{fmt(val)}</text>'
            f'<text class="m" x="{cx}" y="226" text-anchor="middle" font-size="12" font-weight="700" fill="{TXT}">{lab}</text>'
            f'<text class="m" x="{cx}" y="246" text-anchor="middle" font-size="12" fill="{MUT}">{sub}</text>'
        )
    return svg(W, 300, body, "GitHub stats and contribution streak")


def donut(x0, title, sub, pairs, centre):
    total = sum(c for _, c in pairs)
    top = pairs[:5]
    rest = total - sum(c for _, c in top)
    segs = [(n, pct(c, total), PALETTE[i]) for i, (n, c) in enumerate(top)]
    if rest > 0:
        segs.append(("Other", pct(rest, total), PALETTE[5]))
    cx, cy, r = x0 + 100, 190, 58
    C = 2 * math.pi * r
    s = (
        card(x0, 1, 388, 288)
        + f'<text class="t" x="{x0 + 24}" y="42" font-size="20" fill="url(#g)" filter="url(#glow)">{title}</text>'
        + f'<text class="m" x="{x0 + 24}" y="64" font-size="12" fill="{MUT}">{esc(sub)}</text>'
        + f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#1e293b" stroke-width="20"/>'
    )
    off = 0.0
    for name, p, col in segs:
        ln = C * p / 100
        dash = max(ln - 2, 0.1)
        s += (
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="20" '
            f'stroke-dasharray="{dash:.2f} {C - dash:.2f}" stroke-dashoffset="{-off:.2f}" transform="rotate(-90 {cx} {cy})"/>'
        )
        off += ln
    s += (
        f'<text class="m" x="{cx}" y="{cy + 6}" text-anchor="middle" font-size="20" font-weight="700" fill="#ffffff">{fmt(centre)}</text>'
    )
    for i, (name, p, col) in enumerate(segs):
        y = 130 + i * 34
        s += (
            f'<rect x="{x0 + 190}" y="{y - 12}" width="13" height="13" rx="3" fill="{col}"/>'
            f'<text class="m" x="{x0 + 212}" y="{y}" font-size="15" fill="{TXT}">{esc(name[:11])}</text>'
            f'<text class="m" x="{x0 + 366}" y="{y}" text-anchor="end" font-size="15" font-weight="700" fill="{col}">{p:.1f}%</text>'
        )
    return s


def summary():
    if not D:
        return placeholder(W, 290, "LANGUAGE SUMMARY")
    body = donut(1, "BY REPO", "main language of each public repo", D["lang_repo"], sum(c for _, c in D["lang_repo"]))
    body += donut(411, "BY COMMITS", "commits · 12 mo, by repo's main language", D["lang_commit"], sum(c for _, c in D["lang_commit"]))
    return svg(W, 290, body, "Languages by repository and by commits")


def hours():
    hrs = (D or {}).get("hours")
    if not hrs or not sum(hrs):
        return placeholder(W, 290, "COMMITS PER HOUR")
    tot, mx = sum(hrs), max(hrs)
    off = CFG.get("utc_offset", 0)
    tz = "UTC" if off == 0 else f"UTC{off:+g}"
    peak = hrs.index(mx)
    base, maxh = 232, 140
    body = (
        card(1, 1, W - 2, 288)
        + f'<text class="t" x="28" y="42" font-size="20" fill="url(#g)" filter="url(#glow)">COMMITS PER HOUR</text>'
        + f'<text class="m" x="28" y="64" font-size="12" fill="{MUT}">{tz} · from your last {tot} commits in recent repos · peak {peak:02d}:00</text>'
    )
    for h, v in enumerate(hrs):
        x = 44 + h * 30
        bh = max(3.0, maxh * v / mx) if v else 3.0
        col = ramp(h / 23) if v else "#1e293b"
        body += (
            f'<rect x="{x}" y="{base - bh:.1f}" width="20" height="{bh:.1f}" rx="4" fill="{col}">'
            f'<animate attributeName="height" from="0" to="{bh:.1f}" dur="1s" begin="{h * 0.03:.2f}s" fill="freeze"/>'
            f'<animate attributeName="y" from="{base}" to="{base - bh:.1f}" dur="1s" begin="{h * 0.03:.2f}s" fill="freeze"/></rect>'
        )
        if h % 3 == 0:
            body += f'<text class="m" x="{x + 10}" y="{base + 24}" text-anchor="middle" font-size="12" fill="{MUT}">{h:02d}</text>'
    return svg(W, 290, body, "Commits per hour of the day")


# ----------------------------------------------------------------- GitHub API
Q_MAIN = """query($login:String!,$cursor:String){
 user(login:$login){
  id followers{totalCount}
  repositories(first:100,after:$cursor,ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC){
    totalCount pageInfo{hasNextPage endCursor}
    nodes{name stargazerCount primaryLanguage{name}}}
  contributionsCollection{
    totalCommitContributions totalIssueContributions totalPullRequestContributions
    contributionCalendar{totalContributions weeks{contributionDays{contributionCount date weekday}}}
    commitContributionsByRepository(maxRepositories:100){repository{primaryLanguage{name}} contributions{totalCount}}
  }}}"""

Q_HIST = """query($login:String!,$uid:ID!){
 user(login:$login){
  repositories(first:25,ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC,orderBy:{field:PUSHED_AT,direction:DESC}){
   nodes{defaultBranchRef{target{... on Commit{history(first:100,author:{id:$uid}){nodes{committedDate}}}}}}}}}"""


def gql(query, variables, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json", "User-Agent": "profile-readme-builder"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


def streaks(counts):
    longest = run = 0
    for c in counts:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    i = len(counts) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1  # today may not have a contribution yet
    cur = 0
    while i >= 0 and counts[i] > 0:
        cur += 1
        i -= 1
    return cur, longest


def fetch(token):
    login, cursor, repos, first = CFG["login"], None, [], None
    while True:
        u = gql(Q_MAIN, {"login": login, "cursor": cursor}, token)["user"]
        first = first or u
        repos += u["repositories"]["nodes"]
        pi = u["repositories"]["pageInfo"]
        if not pi["hasNextPage"]:
            break
        cursor = pi["endCursor"]
    cc = first["contributionsCollection"]
    cal = cc["contributionCalendar"]
    days = [d for wk in cal["weeks"] for d in wk["contributionDays"]]
    cur, longest = streaks([d["contributionCount"] for d in days])

    by_repo = Counter(r["primaryLanguage"]["name"] for r in repos if r["primaryLanguage"])
    by_commit = Counter()
    for item in cc["commitContributionsByRepository"]:
        lang = item["repository"]["primaryLanguage"]
        if lang:
            by_commit[lang["name"]] += item["contributions"]["totalCount"]
    order = lambda c: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))

    tz = timezone(timedelta(hours=CFG.get("utc_offset", 0)))
    hrs = [0] * 24
    hist = gql(Q_HIST, {"login": login, "uid": first["id"]}, token)["user"]["repositories"]["nodes"]
    for r in hist:
        ref = r.get("defaultBranchRef")
        if not ref or not ref.get("target") or "history" not in ref["target"]:
            continue
        for n in ref["target"]["history"]["nodes"]:
            dt = datetime.fromisoformat(n["committedDate"].replace("Z", "+00:00")).astimezone(tz)
            hrs[dt.hour] += 1

    return {
        "repos": first["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "followers": first["followers"]["totalCount"],
        "contributions": cal["totalContributions"],
        "commits": cc["totalCommitContributions"],
        "prs": cc["totalPullRequestContributions"],
        "issues": cc["totalIssueContributions"],
        "lang_repo": order(by_repo),
        "lang_commit": order(by_commit),
        "hours": hrs,
        "weeks": [{d["weekday"]: d["contributionCount"] for d in wk["contributionDays"]} for wk in cal["weeks"][-24:]],
        "current": cur,
        "longest": longest,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


# ----------------------------------------------------------------- main
def build_all():
    out = {"hero.svg": hero(), "status.svg": status_badge()}
    for num, title, sub in SECTIONS:
        out[f"header-{num}.svg"] = header(num, title, sub)
    out["about.svg"] = about()
    out["hud.svg"] = hud()
    out["city.svg"] = city()
    out["languages.svg"] = languages()
    out["terminal.svg"] = terminal()
    out["stack.svg"] = stack_marquee()
    for i, p in enumerate(CFG["projects"]):
        out[f"project-{i + 1}.svg"] = project(p)
    out["stats.svg"] = stats()
    out["summary.svg"] = summary()
    out["hours.svg"] = hours()
    out["protection.svg"] = protection()
    out["currently.svg"] = currently()
    out["footer.svg"] = footer()
    return out


def main():
    global D
    ASSETS.mkdir(exist_ok=True)
    if "--offline" not in sys.argv:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("Set GH_TOKEN first, or run with --offline.")
        D = fetch(token)
    for name, content in build_all().items():
        (ASSETS / name).write_text(content, encoding="utf-8")
    print(f"wrote {len(build_all())} files to {ASSETS}")


if __name__ == "__main__":
    main()
