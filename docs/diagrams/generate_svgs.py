#!/usr/bin/env python3
"""Generate SVG flowcharts for pm_agent_stage_graph.md (stdlib only)."""

from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).parent


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def svg_wrap(body: str, w: int, h: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" font-family="system-ui,sans-serif" '
        f'font-size="13">\n'
        f'<rect width="100%" height="100%" fill="#fafafa"/>\n{body}</svg>'
    )


def box(x: int, y: int, w: int, h: int, text: str, kind: str = "rect") -> str:
    t = esc(text)
    if kind == "diamond":
        cx, cy = x + w // 2, y + h // 2
        pts = f"{cx},{y} {x + w},{cy} {cx},{y + h} {x},{cy}"
        shape = f'<polygon points="{pts}" fill="#fff" stroke="#333" stroke-width="1.5"/>'
    elif kind == "terminal":
        shape = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" ry="18" '
            f'fill="#d4edda" stroke="#333" stroke-width="1.5"/>'
        )
    else:
        shape = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" ry="6" '
            f'fill="#fff" stroke="#333" stroke-width="1.5"/>'
        )
    lines = t.split("\n")
    ty = y + h // 2 - (len(lines) - 1) * 8
    labels = "".join(
        f'<text x="{x + w // 2}" y="{ty + i * 16}" text-anchor="middle" fill="#111">{line}</text>'
        for i, line in enumerate(lines)
    )
    return shape + labels


def arrow(x1: int, y1: int, x2: int, y2: int, label: str = "") -> str:
    mid_x, mid_y = (x1 + x2) // 2, (y1 + y2) // 2
    lbl = ""
    if label:
        lbl = (
            f'<text x="{mid_x}" y="{mid_y - 4}" text-anchor="middle" '
            f'fill="#555" font-size="11">{esc(label)}</text>'
        )
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#444" marker-end="url(#arr)"/>{lbl}'
    )


def defs() -> str:
    return (
        '<defs><marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="3" '
        'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#444"/></marker></defs>'
    )


def arch() -> str:
    b = [defs()]
    b.append(box(120, 20, 140, 40, "User chat/RPC"))
    b.append(box(320, 20, 140, 40, "Cron scheduler"))
    b.append(box(200, 90, 180, 44, "detect_stage R1-R8"))
    b.append(arrow(190, 60, 240, 90))
    b.append(arrow(390, 60, 320, 90))
    b.append(box(200, 160, 180, 40, "freeze _stage"))
    b.append(box(430, 130, 130, 36, "LLM classifier"))
    b.append(box(430, 180, 130, 36, "QUERY default"))
    b.append(arrow(290, 134, 430, 148, "no match"))
    b.append(arrow(470, 166, 290, 180))
    b.append(arrow(500, 180, 350, 200))
    b.append(arrow(290, 110, 290, 160, "match"))
    b.append(arrow(470, 148, 350, 170, "ok"))
    b.append(box(200, 230, 180, 40, "Stage graph loop"))
    b.append(arrow(290, 200, 290, 230))
    b.append(box(200, 300, 180, 36, "tool call"))
    b.append(arrow(290, 270, 290, 300))
    b.append(box(230, 360, 120, 44, "GATE?", "diamond"))
    b.append(arrow(290, 336, 290, 360))
    b.append(box(60, 430, 100, 36, "execute"))
    b.append(box(320, 430, 130, 36, "pending_confirm"))
    b.append(arrow(250, 382, 110, 430, "auto"))
    b.append(arrow(330, 382, 385, 430, "confirm"))
    b.append(box(60, 500, 100, 36, "terminal?"))
    b.append(arrow(110, 466, 110, 500))
    b.append(box(200, 500, 140, 36, "end + report", "terminal"))
    b.append(arrow(160, 518, 200, 518))
    return svg_wrap("\n".join(b), 580, 560)


def router() -> str:
    stages = [
        ("R1 status", "STATUS"),
        ("R2 backlog", "BOARD"),
        ("R3 reorg", "REORG"),
        ("R4 transition", "TRANSITION"),
        ("R5 proactive", "PROACTIVE"),
        ("R6 hygiene", "HYGIENE"),
        ("R7 create", "INTAKE"),
        ("R8 query", "QUERY"),
    ]
    b = [defs(), box(250, 10, 100, 32, "message")]
    y = 60
    for rule, stage in stages:
        b.append(box(220, y, 160, 36, rule, "diamond"))
        b.append(box(430, y + 4, 110, 28, stage))
        b.append(arrow(300, 42, 300, y))
        b.append(arrow(380, y + 18, 430, y + 18, "yes"))
        if y > 60:
            b.append(arrow(300, y - 24, 300, y, "no"))
        y += 52
    b.append(box(220, y, 160, 36, "R9 LLM classifier"))
    b.append(arrow(300, y - 16, 300, y))
    b.append(box(220, y + 50, 160, 36, "R10 invalid?", "diamond"))
    b.append(arrow(300, y + 36, 300, y + 50))
    b.append(box(430, y + 54, 110, 28, "QUERY"))
    b.append(arrow(380, y + 68, 430, y + 68, "no"))
    return svg_wrap("\n".join(b), 580, y + 110)


def stage_chain(title: str, steps: list[str]) -> str:
    b = [defs(), f'<text x="20" y="24" font-weight="600" fill="#111">{esc(title)}</text>']
    y = 40
    prev_cx, prev_bottom = 200, 0
    for i, step in enumerate(steps):
        h = 36 if "\n" not in step else 48
        b.append(box(80, y, 240, h, step))
        cx = 200
        if i:
            b.append(arrow(prev_cx, prev_bottom, cx, y))
        prev_cx, prev_bottom = cx, y + h
        y += h + 28
    b.append(box(80, y, 240, 36, "terminal", "terminal"))
    b.append(arrow(prev_cx, prev_bottom, 200, y))
    return svg_wrap("\n".join(b), 400, y + 60)


def write(name: str, content: str) -> None:
    path = OUT / name
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path}")


def main() -> None:
    write("01_architecture.svg", arch())
    write("02_router.svg", router())
    write(
        "03_intake.svg",
        stage_chain(
            "INTAKE",
            ["resolve_assignee?", "SELF-CHECK", "create_issue GATE"],
        ),
    )
    write(
        "04_status.svg",
        stage_chain(
            "STATUS",
            ["find_issues", "patch? (opt)", "summarizer", "comment", "blocker/done branches"],
        ),
    )
    write(
        "05_board.svg",
        stage_chain(
            "BOARD",
            ["backlog_plan", "FORCED apply", "SELF-CHECK", "patch gaps?"],
        ),
    )
    write(
        "06_transition.svg",
        stage_chain(
            "TRANSITION",
            ["find_issues", "list_transitions", "transition / close"],
        ),
    )
    write(
        "07_query.svg",
        stage_chain(
            "QUERY (read-only)",
            ["board_snapshot / find / search", "format (opt)", "reply"],
        ),
    )
    write(
        "08_reorg.svg",
        stage_chain(
            "REORG",
            ["find or search", "patch / link / create / close", "bulk loop"],
        ),
    )
    write(
        "09_proactive.svg",
        stage_chain(
            "PROACTIVE (cron)",
            ["board_snapshot", "sweep: overdue / unassigned / digest"],
        ),
    )
    write(
        "10_hygiene.svg",
        stage_chain(
            "HYGIENE",
            ["board_snapshot", "SELF-CHECK board", "patch / comment"],
        ),
    )
    write(
        "11_lifecycle.svg",
        stage_chain(
            "Turn lifecycle",
            ["Running", "Terminal / Parked / SafetyStop"],
        ),
    )


if __name__ == "__main__":
    main()
