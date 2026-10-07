#!/usr/bin/env python3
"""Generate the ORC Robotics organization profile assets.

Writes a stats JSON plus three kinds of SVG for profile/README.md:
a masthead (header.svg), a dashboard (snapshot.svg) and one card per
public repository (repos/<name>.svg). The SVGs embed subset Faustina and
Geist fonts, because GitHub renders README images without network access.

When the fetched data matches the previous JSON, the previous timestamp
is kept, so the output is byte-identical and the workflow has nothing
to commit.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    from fontTools import subset as ft_subset
    from fontTools.ttLib import TTFont
except ImportError:  # pragma: no cover - the workflow installs fonttools
    ft_subset = None
    TTFont = None


FONT_DIR = Path(__file__).resolve().parent / "fonts"
PROFILE_REPO = ".github"

# Palette taken from a warm, near-black "election night" dashboard look.
BG = "#0f0e0d"
PANEL = "#151412"
RAISED = "#1f1d1a"
LINE = "#fafaf9"  # used with low opacity for hairlines
TEXT = "#fafaf9"
TEXT_2 = "#d6d4cf"
MUTED = "#a6a39c"
DIM = "#85827c"
BLUE = "#2f6fe4"
RED = "#f14242"
GOLD = "#d9b47c"
GREEN = "#84c65c"

LANGUAGE_COLORS = {
    "C++": RED,
    "Python": BLUE,
    "TypeScript": GOLD,
    "JavaScript": "#cea608",
    "PowerShell": "#04a9b7",
    "Shell": GREEN,
    "C": "#c9c6bf",
    "CMake": "#e07a5f",
    "CSS": "#a78bfa",
    "HTML": "#f0855b",
    "Java": "#c08a52",
}
OTHER_COLOR = DIM

LANGUAGE_SHORT = {
    "C++": "C++",
    "Python": "Py",
    "TypeScript": "TS",
    "JavaScript": "JS",
    "PowerShell": "PS",
    "Shell": "Sh",
    "CMake": "CM",
    "Batchfile": "Bat",
    "Other": "+",
}

LANGUAGE_BY_EXTENSION = {
    ".c": "C++",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".h": "C++",
    ".hh": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".css": "CSS",
    ".ps1": "PowerShell",
    ".cmake": "CMake",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".html": "HTML",
    ".java": "Java",
}

IGNORED_DIRECTORIES = {
    ".git",
    ".github",
    ".gradle",
    ".gradle-user-home",
    ".idea",
    ".next",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "docs",
    "gradle",
    "node_modules",
    "public",
    "shuffleboard",
    "vendordeps",
}


def language_color(name: str) -> str:
    return LANGUAGE_COLORS.get(name, OTHER_COLOR)


# --------------------------------------------------------------------------
# Data collection
# --------------------------------------------------------------------------


def github_get(url: str, token: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ORC-Robotics-Stats",
        "Authorization": f"Bearer {token}",
    }
    request = Request(url, headers=headers)
    with urlopen(request) as response:
        return json.load(response)


def fetch_github_stats(org: str, token: str) -> list[dict]:
    repos: list[dict] = []
    page = 1

    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&type=all&page={page}"
        items = github_get(url, token)
        if not items:
            break

        for item in items:
            try:
                languages = github_get(item["languages_url"], token)
            except HTTPError:
                languages = {}
            repos.append(
                {
                    "name": item["name"],
                    "private": bool(item["private"]),
                    "archived": bool(item.get("archived")),
                    "description": item.get("description") or "",
                    "language": item.get("language") or "",
                    "stars": int(item.get("stargazers_count") or 0),
                    "url": item.get("html_url") or "",
                    "pushedAt": (item.get("pushed_at") or "")[:10],
                    "languages": {language: int(size) for language, size in languages.items()},
                }
            )

        page += 1

    return repos


def annotate(level: str, title: str, message: str) -> None:
    """Print a GitHub Actions annotation and add it to the run summary."""
    print(f"::{level} title={title}::{message}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"**{title}**: {message}\n\n")


def check_token_coverage(org: str, token: str, repos: list[dict]) -> None:
    """Warn when the token sees fewer private repositories than the organization owns.

    A fine-grained token limited to selected repositories silently misses
    every repository created or transferred after it was issued.
    """
    seen = sum(1 for repo in repos if repo["private"])
    try:
        details = github_get(f"https://api.github.com/orgs/{org}", token)
    except HTTPError as error:
        annotate("notice", "Token coverage not checked", f"Could not read the organization ({error.code}).")
        return

    owned = details.get("owned_private_repos", details.get("total_private_repos"))
    if owned is None:
        annotate(
            "notice",
            "Token coverage not checked",
            "The token cannot read the organization's private repository count (needs an organization owner).",
        )
    elif int(owned) > seen:
        annotate(
            "warning",
            "ORG_STATS_TOKEN is missing repositories",
            f"{org} has {owned} private repositories but the token can only see {seen}. "
            "Give the token access to all repositories so every project is counted.",
        )
    else:
        print(f"Token coverage OK: {seen} of {owned} private repositories visible.")


def detect_language(path: Path) -> str | None:
    if path.name == "CMakeLists.txt":
        return "CMake"
    return LANGUAGE_BY_EXTENSION.get(path.suffix.lower())


def analyze_local_repo(path: Path) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)

    for root, dirs, files in os.walk(path):
        dirs[:] = [directory for directory in dirs if directory not in IGNORED_DIRECTORIES]
        root_path = Path(root)

        for file_name in files:
            file_path = root_path / file_name
            language = detect_language(file_path)
            if not language:
                continue
            size = file_path.stat().st_size
            if size > 0:
                totals[language] += size

    return dict(totals)


def collect_local_repos(repo_args: Iterable[str]) -> list[dict]:
    repos: list[dict] = []
    for value in repo_args:
        name, visibility, raw_path = value.split("|", 2)
        repos.append(
            {"name": name, "private": visibility == "private", "languages": analyze_local_repo(Path(raw_path))}
        )
    return repos


# A language below this share of a single project is treated as incidental
# (a build script, a helper file) and left out of that project's mix.
MIN_PROJECT_SHARE = 2.0


def project_mix(language_bytes: dict[str, int]) -> dict[str, float]:
    total = sum(language_bytes.values())
    if total == 0:
        return {}
    kept = {language: size for language, size in language_bytes.items() if size * 100 / total >= MIN_PROJECT_SHARE}
    kept_total = sum(kept.values())
    mix = {language: round(size * 100 / kept_total, 1) for language, size in kept.items()}
    return dict(sorted(mix.items(), key=lambda item: (-item[1], item[0])))


def summarize_languages(projects: list[dict]) -> list[dict[str, object]]:
    """Every project gets one equal vote, split across the languages it uses.

    Weighting by bytes let one large C++ codebase hide whole projects
    written in other languages; this shows what the team actually works in.
    """
    if not projects:
        return []

    shares: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    sizes: dict[str, int] = defaultdict(int)
    for project in projects:
        for language, percent in project["languages"].items():
            shares[language] += percent / len(projects)
            counts[language] += 1
        for language, size in project["bytes"].items():
            sizes[language] += size

    ranked = sorted(shares.items(), key=lambda item: (-item[1], -counts[item[0]], item[0]))
    return [
        {"name": language, "percent": round(share, 1), "projects": counts[language], "bytes": sizes[language]}
        for language, share in ranked
    ]


def build_data(repos: list[dict]) -> dict[str, object]:
    """Everything that is compared between runs (no timestamp)."""
    visible = [repo for repo in repos if repo["name"] != PROFILE_REPO]
    recent = sorted(
        (repo for repo in visible if repo.get("pushedAt")),
        key=lambda repo: (repo["pushedAt"], repo["name"].lower()),
        reverse=True,
    )

    # Private repositories are counted and shown anonymously only:
    # no names or descriptions leave the organization.
    activity = []
    for repo in recent[:6]:
        entry = {"private": repo["private"], "language": repo.get("language", ""), "pushedAt": repo["pushedAt"]}
        if not repo["private"]:
            entry["name"] = repo["name"]
            entry["description"] = repo.get("description", "")
        activity.append(entry)

    projects = []
    for repo in sorted(visible, key=lambda repo: (repo.get("pushedAt", ""), repo["name"].lower()), reverse=True):
        mix = project_mix(repo.get("languages", {}))
        if not mix:
            continue
        project = {"private": repo["private"], "languages": mix}
        if not repo["private"]:
            project["name"] = repo["name"]
        project["bytes"] = {language: size for language, size in repo["languages"].items() if language in mix}
        projects.append(project)

    public_repos = [
        {
            "name": repo["name"],
            "description": repo.get("description", ""),
            "language": repo.get("language", ""),
            "stars": repo.get("stars", 0),
            "url": repo.get("url", ""),
            "pushedAt": repo.get("pushedAt", ""),
        }
        for repo in sorted(visible, key=lambda repo: (-repo.get("stars", 0), repo["name"].lower()))
        if not repo["private"] and not repo.get("archived")
    ]

    return {
        "repositories": {
            "total": len(repos),
            "public": sum(1 for repo in repos if not repo["private"]),
            "private": sum(1 for repo in repos if repo["private"]),
        },
        "languages": summarize_languages(projects),
        "projects": [{key: value for key, value in project.items() if key != "bytes"} for project in projects],
        "activity": activity,
        "publicRepos": public_repos,
    }


COMPARED_KEYS = ("repositories", "languages", "projects", "activity", "publicRepos")


def merge_with_previous(org: str, data: dict[str, object], previous: dict[str, object] | None) -> dict[str, object]:
    if previous and all(previous.get(key) == data[key] for key in COMPARED_KEYS):
        return previous

    return {
        "organization": org,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        **data,
    }


# --------------------------------------------------------------------------
# Fonts: metrics for layout and per-SVG subsets for embedding
# --------------------------------------------------------------------------

FONT_FILES = {
    ("serif", 500): "faustina-500.woff2",
    ("sans", 400): "geist-400.woff2",
    ("sans", 500): "geist-500.woff2",
    ("sans", 600): "geist-600.woff2",
    ("mono", 500): "geistmono-500.woff2",
}
FONT_FAMILIES = {
    "serif": ("ORCSerif", "Georgia, 'Times New Roman', serif"),
    "sans": ("ORCSans", "'Helvetica Neue', Helvetica, Arial, sans-serif"),
    "mono": ("ORCMono", "ui-monospace, Menlo, Consolas, monospace"),
}
FALLBACK_EM = {"serif": 0.5, "sans": 0.55, "mono": 0.6}
_METRICS: dict[tuple[str, int], tuple[int, dict, list]] = {}


def _load_metrics(key: tuple[str, int]):
    if key not in _METRICS:
        font = TTFont(FONT_DIR / FONT_FILES[key], recalcTimestamp=False)
        _METRICS[key] = (font["head"].unitsPerEm, font.getBestCmap(), font["hmtx"].metrics)
    return _METRICS[key]


def text_width(text: str, size: float, font: str = "sans", weight: int = 400) -> float:
    if TTFont is None:
        return len(text) * size * FALLBACK_EM[font]
    upm, cmap, hmtx = _load_metrics((font, weight))
    units = sum(hmtx[cmap.get(ord(char), ".notdef")][0] for char in text)
    return units * size / upm


def wrap(text: str, width: float, size: float, font: str = "sans", weight: int = 400, max_lines: int = 3) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and text_width(candidate, size, font, weight) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and text_width(last + "…", size, font, weight) > width:
            last = last[:-1]
        lines[-1] = last.rstrip(" ,.;:") + "…"
    return lines


def font_face_css(used: dict[tuple[str, int], set[str]]) -> str:
    rules = []
    for key in sorted(used):
        chars = used[key]
        path = FONT_DIR / FONT_FILES[key]
        if ft_subset is not None:
            font = TTFont(path, recalcTimestamp=False)
            options = ft_subset.Options()
            options.flavor = "woff2"
            options.layout_features = ["kern", "liga", "tnum", "lnum"]
            options.name_IDs = [1, 2]
            options.notdef_outline = True
            subsetter = ft_subset.Subsetter(options)
            subsetter.populate(text="".join(sorted(chars)))
            subsetter.subset(font)
            buffer = io.BytesIO()
            ft_subset.save_font(font, buffer, options)
            data = buffer.getvalue()
        else:
            data = path.read_bytes()
        family = FONT_FAMILIES[key[0]][0]
        encoded = base64.b64encode(data).decode("ascii")
        rules.append(
            f"@font-face{{font-family:{family};font-weight:{key[1]};"
            f"src:url(data:font/woff2;base64,{encoded}) format('woff2');}}"
        )
    return "\n".join(rules)


# --------------------------------------------------------------------------
# SVG building
# --------------------------------------------------------------------------


def fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


class Svg:
    def __init__(self, width: int, height: int, title: str):
        self.width = width
        self.height = height
        self.title = title
        self.parts: list[str] = []
        self.used: dict[tuple[str, int], set[str]] = defaultdict(set)

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def rect(self, x, y, w, h, fill, rx=0, **attrs) -> None:
        extra = "".join(f' {key.replace("_", "-")}="{value}"' for key, value in attrs.items())
        self.add(f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" rx="{fmt(rx)}" fill="{fill}"{extra}/>')

    def panel(self, x, y, w, h) -> None:
        self.rect(x + 0.5, y + 0.5, w - 1, h - 1, PANEL, rx=12, stroke=LINE, stroke_opacity="0.08")

    def hline(self, x1, x2, y, opacity=0.08) -> None:
        self.add(f'<path d="M{fmt(x1)} {fmt(y)}H{fmt(x2)}" stroke="{LINE}" stroke-opacity="{opacity}"/>')

    def text(self, x, y, runs, size=12, font="sans", weight=400, fill=TEXT, anchor="start", cls="") -> None:
        """runs: a string or a list of (text, overrides) with fill/weight/font/size/dy keys."""
        if isinstance(runs, str):
            runs = [(runs, {})]
        family, fallback = FONT_FAMILIES[font]
        attrs = (
            f'x="{fmt(x)}" y="{fmt(y)}" font-family="{family}, {fallback}" '
            f'font-size="{fmt(size)}" font-weight="{weight}" fill="{fill}"'
        )
        if anchor != "start":
            attrs += f' text-anchor="{anchor}"'
        if cls:
            attrs += f' class="{cls}"'
        spans = []
        for content, over in runs:
            run_font = over.get("font", font)
            run_weight = over.get("weight", weight)
            self.used[(run_font, run_weight)].update(content)
            span_attrs = ""
            if "fill" in over:
                span_attrs += f' fill="{over["fill"]}"'
            if run_weight != weight:
                span_attrs += f' font-weight="{run_weight}"'
            if run_font != font:
                run_family, run_fallback = FONT_FAMILIES[run_font]
                span_attrs += f' font-family="{run_family}, {run_fallback}"'
            if "size" in over:
                span_attrs += f' font-size="{fmt(over["size"])}"'
            if "dx" in over:
                span_attrs += f' dx="{fmt(over["dx"])}"'
            if "dy" in over:
                span_attrs += f' dy="{fmt(over["dy"])}"'
            if span_attrs:
                spans.append(f"<tspan{span_attrs}>{escape(content, quote=False)}</tspan>")
            else:
                spans.append(escape(content, quote=False))
        self.add(f'<text {attrs}>{"".join(spans)}</text>')

    def render(self) -> str:
        css = font_face_css(self.used)
        body = "\n".join(self.parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img" aria-label="{escape(self.title)}">\n'
            f"<title>{escape(self.title)}</title>\n"
            f"<style>\n{css}\n.n{{font-variant-numeric:tabular-nums}}\n</style>\n"
            f"{body}\n</svg>\n"
        )


def short_date(value: str) -> str:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%b %d")
    except ValueError:
        return value


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def human_bytes(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.2f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.0f} KB"
    return f"{size} B"


def pill(svg: Svg, x_end: float, y: float, label: str, dot: str | None = None) -> None:
    size = 10.5
    width = text_width(label, size, "sans", 500) + 20 + (12 if dot else 0)
    x = x_end - width
    svg.rect(x, y, width, 20, RAISED, rx=10, stroke=LINE, stroke_opacity="0.08")
    tx = x + 10
    if dot:
        svg.add(f'<circle cx="{fmt(x + 13)}" cy="{fmt(y + 10)}" r="3" fill="{dot}"/>')
        tx += 12
    svg.text(tx, y + 13.8, label, size=size, weight=500, fill=TEXT_2)


# --------------------------------------------------------------------------
# header.svg
# --------------------------------------------------------------------------


def build_header_svg(payload: dict[str, object]) -> str:
    W, H = 900, 300
    svg = Svg(W, H, "ORC Robotics — autonomous mobile robots engineered end to end at SENAI ORC: mechanics, electronics and software")
    svg.rect(0.5, 0.5, W - 1, H - 1, BG, rx=14, stroke=LINE, stroke_opacity="0.1")

    # Top strip
    svg.add(f'<path d="M1 14a13 13 0 0 1 13-13H{W - 14}a13 13 0 0 1 13 13V32H1Z" fill="{PANEL}"/>')
    svg.hline(1, W - 1, 32.5)
    svg.text(
        W / 2,
        21,
        [
            ("Built at ", {}),
            ("SENAI ORC", {"fill": TEXT, "weight": 500}),
            (", Brazil — mechanical, electrical and software engineering for autonomous robots.  ", {}),
            ("Repositories below ↓", {"fill": TEXT, "weight": 500}),
        ],
        size=11.5,
        fill=MUTED,
        anchor="middle",
    )

    # Masthead: one text element, so "by SENAI ORC" always follows the
    # wordmark at a fixed gap whatever font the viewer ends up rendering.
    svg.text(
        24,
        72,
        [
            ("ORC Robotics", {}),
            ("by SENAI ORC", {"font": "sans", "weight": 400, "size": 11, "fill": DIM, "dx": 10, "dy": -1}),
        ],
        size=21,
        font="serif",
        weight=500,
    )

    repo_total = payload["repositories"]["total"]
    updated = datetime.strptime(payload["generatedAt"], "%Y-%m-%d %H:%M UTC")
    status = f"Updated {updated.strftime('%b %d, %H:%M')} UTC · "
    status_value = f"{repo_total} repos"
    status_width = text_width(status, 11) + text_width(status_value, 11, "sans", 600)
    svg.add(f'<circle cx="{fmt(W - 24 - status_width - 9)}" cy="67" r="3.5" fill="{GREEN}"/>')
    svg.text(W - 24, 71, [(status, {}), (status_value, {"fill": TEXT, "weight": 600})], size=11, fill=MUTED, anchor="end", cls="n")

    svg.hline(0, W, 98.5)

    # Headline
    svg.text(24, 152, [("Autonomous mobile robots", {"fill": BLUE}), (",", {})], size=40, font="serif", weight=500)
    svg.text(24, 198, [("engineered ", {}), ("end to end", {"fill": RED}), (".", {})], size=40, font="serif", weight=500)
    subtitle = wrap(
        "Mechanical CAD, electronics and custom PCBs, firmware and robot software — every layer designed "
        "and integrated in-house by a two-person team at SENAI ORC, with open-source contributors.",
        540,
        13.5,
    )
    for index, line in enumerate(subtitle):
        svg.text(24, 232 + index * 19, line, size=13.5, fill=MUTED)

    # Engineering stack card: one bar per layer, top of the robot to the bottom
    cx, cy, cw = 596, 112, 280
    layers = [
        ("Tooling & telemetry", "TypeScript", GOLD),
        ("Perception", "Python · OpenCV", RED),
        ("Robot software", "ROS 2 · C++ · Java", BLUE),
        ("Firmware", "ESP32 · Raspberry Pi", "#04a9b7"),
        ("Electronics", "Power · custom PCBs", GREEN),
        ("Mechanical", "CAD · fabrication", "#c9c6bf"),
    ]
    ch = 42 + len(layers) * 20 + 10
    svg.panel(cx, cy, cw, ch)
    svg.text(cx + 14, cy + 22, "Every layer, in-house", size=10.5, fill=MUTED)
    pill(svg, cx + cw - 12, cy + 9, "full stack", dot=GREEN)
    for index, (name, tools, color) in enumerate(layers):
        ry = cy + 40 + index * 20
        svg.rect(cx + 14, ry, cw - 28, 16, color, rx=4, fill_opacity="0.09")
        svg.rect(cx + 14, ry, 3, 16, color, rx=1.5)
        svg.text(cx + 25, ry + 11.5, name, size=10.5, weight=500, fill=TEXT)
        svg.text(cx + cw - 22, ry + 11.5, tools, size=10, fill=MUTED, anchor="end")

    return svg.render()


# --------------------------------------------------------------------------
# snapshot.svg
# --------------------------------------------------------------------------


def big_percent(svg: Svg, x: float, y: float, value: float, anchor: str = "start") -> None:
    svg.text(
        x,
        y,
        [(f"{value:.1f}", {}), ("%", {"size": 14, "dy": -16, "fill": MUTED})],
        size=38,
        weight=500,
        anchor=anchor,
        cls="n",
    )


def build_language_rows(languages: list[dict], limit: int = 8) -> list[dict]:
    rows = [dict(language) for language in languages[:limit]]
    rest = languages[limit:]
    if rest:
        rows.append(
            {
                "name": "Other",
                "bytes": sum(int(language["bytes"]) for language in rest),
                "percent": round(sum(float(language["percent"]) for language in rest), 1),
                "count": len(rest),
            }
        )
    return rows


def draw_visibility_panel(svg: Svg, x: float, y: float, w: float, h: float, payload: dict) -> None:
    repos = payload["repositories"]
    languages = payload["languages"]
    total = max(1, int(repos["total"]))
    public, private = int(repos["public"]), int(repos["private"])

    svg.panel(x, y, w, h)
    svg.text(x + 16, y + 26, "Repositories · ORC-Robotics", size=10.5, fill=MUTED)
    pill(svg, x + w - 12, y + 12, "private incl.", dot=GREEN)

    svg.text(x + 16, y + 62, [(f"{public} public", {"fill": BLUE}), (" and", {})], size=22, font="serif", weight=500)
    svg.text(x + 16, y + 88, [(f"{private} private", {"fill": RED}), (" repositories", {})], size=22, font="serif", weight=500)

    left, right = x + 16, x + w - 16
    svg.text(left, y + 118, "Public", size=12.5, weight=500)
    svg.text(left, y + 133, "open source", size=10.5, fill=BLUE)
    svg.text(right, y + 118, "Private", size=12.5, weight=500, anchor="end")
    svg.text(right, y + 133, "internal", size=10.5, fill=RED, anchor="end")

    big_percent(svg, left, y + 176, public * 100 / total)
    big_percent(svg, right, y + 176, private * 100 / total, anchor="end")
    svg.text(left, y + 193, f"{public} repositories", size=10.5, fill=DIM, cls="n")
    svg.text(right, y + 193, f"{private} repositories", size=10.5, fill=DIM, anchor="end", cls="n")

    bar_w = right - left
    split = bar_w * public / total
    svg.rect(left, y + 205, max(0, split - 2), 6, BLUE, rx=3)
    svg.rect(left + split + 2, y + 205, max(0, bar_w - split - 2), 6, RED, rx=3)
    svg.rect(left + bar_w / 2 - 1, y + 201, 2, 14, TEXT, rx=1)

    top = languages[0] if languages else {"name": "—", "percent": 0}
    total_bytes = sum(int(language["bytes"]) for language in languages)
    svg.text(left, y + 238, "Primary language", size=11, fill=MUTED)
    svg.text(right, y + 238, f"{top['name']} · {float(top['percent']):.1f}%", size=11, weight=500, anchor="end", cls="n")
    svg.text(left, y + 258, "Code analyzed", size=11, fill=MUTED)
    svg.text(right, y + 258, f"{human_bytes(total_bytes)} · {len(languages)} languages", size=11, weight=500, anchor="end", cls="n")


def truncate(text: str, width: float, size: float, weight: int = 400) -> str:
    if text_width(text, size, "sans", weight) <= width:
        return text
    while text and text_width(text + "…", size, "sans", weight) > width:
        text = text[:-1]
    return text.rstrip(" -_.") + "…"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def draw_projects_panel(svg: Svg, x: float, y: float, w: float, h: float, payload: dict) -> None:
    svg.panel(x, y, w, h)
    svg.text(x + 16, y + 26, "Project mix", size=12.5, weight=500)
    svg.text(x + w - 16, y + 26, "languages per project", size=10.5, fill=DIM, anchor="end")

    projects = payload.get("projects", [])
    if not projects:
        svg.text(x + w / 2, y + h / 2, "No projects with code yet", size=11, fill=DIM, anchor="middle")
        return

    label_w = 88
    bar_x = x + 16 + label_w + 8
    bar_w = x + w - 16 - bar_x
    row_h = min(26.0, (h - 52) / len(projects))
    shown = projects[: int((h - 52) // row_h)]
    for index, project in enumerate(shown):
        ry = y + 46 + index * row_h
        if project.get("private"):
            svg.text(x + 16, ry + 10, "Private project", size=11, fill=DIM)
        else:
            svg.text(x + 16, ry + 10, truncate(str(project["name"]), label_w, 11, 500), size=11, weight=500, fill=TEXT_2)

        cursor = bar_x
        segments = list(project["languages"].items())
        gaps = 2 * (len(segments) - 1)
        for language, percent in segments:
            seg_w = max(2.0, (bar_w - gaps) * float(percent) / 100)
            color = language_color(language)
            svg.rect(cursor, ry, seg_w, 13, color, rx=3)
            short = LANGUAGE_SHORT.get(language, language[:2])
            if seg_w >= text_width(short, 8.5, "sans", 600) + 8:
                svg.text(cursor + seg_w / 2, ry + 9.5, short, size=8.5, weight=600, fill=BG, anchor="middle")
            cursor += seg_w + 2


def draw_languages_panel(svg: Svg, x: float, y: float, w: float, h: float, payload: dict) -> None:
    svg.panel(x, y, w, h)
    svg.text(x + 16, y + 26, "By language", size=12.5, weight=500)
    svg.text(x + w - 16, y + 26, "each project counts equally", size=10.5, fill=DIM, anchor="end")

    rows = build_language_rows(payload["languages"])
    row_h = min(48.0, (h - 48) / max(1, len(rows)))
    for index, language in enumerate(rows):
        ry = y + 46 + index * row_h
        name = str(language["name"])
        color = language_color(name)
        percent = float(language["percent"])

        if index:
            svg.hline(x + 16, x + w - 16, ry - 6, opacity=0.05)
        svg.rect(x + 16, ry + 2, 26, 26, color, rx=7, fill_opacity="0.16")
        svg.text(x + 29, ry + 19, LANGUAGE_SHORT.get(name, name[:2]), size=9, weight=600, fill=color, anchor="middle")

        svg.text(x + 52, ry + 13, name, size=12.5, weight=500)
        if name == "Other":
            sub = f"{language['count']} languages · {human_bytes(int(language['bytes']))}"
        else:
            sub = f"{plural(int(language['projects']), 'project')} · {human_bytes(int(language['bytes']))}"
        svg.text(x + 52, ry + 28, sub, size=10.5, fill=DIM, cls="n")

        svg.text(x + w - 16, ry + 14, f"{percent:.1f}%", size=13, weight=600, anchor="end", cls="n")

        bar_x, bar_w = x + w - 132, 116
        svg.rect(bar_x, ry + 23, bar_w, 4, LINE, rx=2, fill_opacity="0.07")
        svg.rect(bar_x, ry + 23, max(3.0, bar_w * percent / 100), 4, color, rx=2)


def draw_activity_panel(svg: Svg, x: float, y: float, w: float, h: float, payload: dict) -> None:
    svg.panel(x, y, w, h)
    svg.text(x + 16, y + 26, "Latest activity", size=12.5, weight=500)
    svg.text(x + w - 16, y + 26, "last push", size=10.5, fill=DIM, anchor="end")

    text_x = x + 92
    text_w = x + w - 16 - text_x
    cursor = y + 54
    activity = payload.get("activity", [])
    if not activity:
        svg.text(x + w / 2, y + h / 2, "No recent pushes", size=11, fill=DIM, anchor="middle")
        return

    for index, entry in enumerate(activity):
        if entry.get("private"):
            title = "Private repository"
            body = "Internal work — source not public."
            title_fill = TEXT_2
        else:
            title = entry.get("name", "")
            body = capitalize(entry.get("description") or "No description yet.")
            title_fill = TEXT
        language = entry.get("language") or ""
        lines = wrap(body, text_w, 11.5, max_lines=3)
        block_h = 18 + len(lines) * 16 + (16 if language else 0)
        if cursor + block_h > y + h - 10:
            break
        if index:
            svg.hline(x + 16, x + w - 16, cursor - 14, opacity=0.05)

        svg.text(x + 16, cursor + 4, short_date(entry["pushedAt"]), size=10.5, font="mono", weight=500, fill=DIM)
        color = language_color(language) if language else DIM
        if entry.get("private"):
            svg.add(f'<circle cx="{fmt(x + 75)}" cy="{fmt(cursor)}" r="7.5" fill="none" stroke="{TEXT_2}" stroke-width="1.5"/>')
        else:
            svg.add(f'<circle cx="{fmt(x + 75)}" cy="{fmt(cursor)}" r="8" fill="{color}" fill-opacity="0.2"/>')
            svg.add(f'<circle cx="{fmt(x + 75)}" cy="{fmt(cursor)}" r="3.5" fill="{color}"/>')

        svg.text(text_x, cursor + 4, title, size=12.5, weight=600, fill=title_fill)
        for line_index, line in enumerate(lines):
            svg.text(text_x, cursor + 22 + line_index * 16, line, size=11.5, fill=MUTED)
        if language:
            svg.text(text_x, cursor + 22 + len(lines) * 16, language, size=10.5, weight=500, fill=color)
        cursor += block_h + 22


def build_snapshot_svg(payload: dict[str, object]) -> str:
    W, H = 900, 500
    svg = Svg(W, H, "ORC Robotics snapshot — repositories, language share and latest activity")
    col_w, gap = 292, 12
    c1, c2, c3 = 0, col_w + gap, 2 * (col_w + gap)
    draw_visibility_panel(svg, c1, 0, col_w, 272, payload)
    draw_projects_panel(svg, c1, 284, col_w, H - 284, payload)
    draw_languages_panel(svg, c2, 0, col_w, H, payload)
    draw_activity_panel(svg, c3, 0, col_w, H, payload)
    return svg.render()


# --------------------------------------------------------------------------
# repos/<name>.svg
# --------------------------------------------------------------------------


def build_repo_card_svg(repo: dict[str, object]) -> str:
    W, H = 440, 132
    svg = Svg(W, H, f"{repo['name']} — {repo.get('description') or 'ORC Robotics repository'}")
    svg.panel(0, 0, W, H)

    svg.text(16, 25, "Public repository · ORC-Robotics", size=10.5, fill=MUTED)
    pill(svg, W - 12, 11, "Open source", dot=GREEN)

    svg.text(16, 58, str(repo["name"]), size=22, font="serif", weight=500)
    description = capitalize(str(repo.get("description") or "No description yet."))
    lines = wrap(description, W - 32, 12, max_lines=2)
    for index, line in enumerate(lines):
        svg.text(16, 80 + index * 16, line, size=12, fill=MUTED)

    language = str(repo.get("language") or "")
    if language:
        color = language_color(language)
        svg.add(f'<circle cx="21" cy="{H - 20}" r="4" fill="{color}"/>')
        svg.text(31, H - 16, language, size=11, weight=500, fill=TEXT_2)
    if repo.get("pushedAt"):
        svg.text(W - 16, H - 16, f"Updated {short_date(str(repo['pushedAt']))}", size=11, fill=DIM, anchor="end", cls="n")
    return svg.render()


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()


def format_repos_markdown(payload: dict[str, object]) -> str:
    lines = ["<!-- repos:start -->", '<p align="center">']
    for repo in payload.get("publicRepos", []):
        alt = escape(f"{repo['name']}: {repo.get('description') or 'ORC Robotics repository'}")
        lines.append(
            f'  <a href="{escape(str(repo["url"]))}"><img src="./assets/repos/{slug(str(repo["name"]))}.svg" '
            f'width="49%" alt="{alt}" /></a>'
        )
    lines.append("</p>")
    lines.append("<!-- repos:end -->")
    return "\n".join(lines)


def replace_block(readme_path: Path, name: str, new_block: str) -> None:
    content = readme_path.read_text(encoding="utf-8")
    start_marker = f"<!-- {name}:start -->"
    end_marker = f"<!-- {name}:end -->"
    if start_marker not in content or end_marker not in content:
        return
    start_index = content.index(start_marker)
    end_index = content.index(end_marker) + len(end_marker)
    readme_path.write_text(content[:start_index] + new_block + content[end_index:], encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ORC Robotics organization profile assets.")
    parser.add_argument("--org", required=True, help="GitHub organization name.")
    parser.add_argument("--assets", required=True, help="Directory for the generated SVG files.")
    parser.add_argument("--json", required=True, help="Path to the stats JSON (read as previous state, then written).")
    parser.add_argument("--readme", help="Profile README whose <!-- repos --> block lists the public repositories.")
    parser.add_argument("--render-only", action="store_true", help="Re-render the SVGs from the existing JSON.")
    parser.add_argument(
        "--local-repo",
        action="append",
        default=[],
        help="Fallback local repository in the format name|visibility|path.",
    )
    parser.add_argument(
        "--token-env",
        default="ORG_STATS_TOKEN",
        help="Environment variable that stores the GitHub token.",
    )
    args = parser.parse_args()

    json_path = Path(args.json)
    previous = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else None

    if args.render_only:
        if previous is None:
            raise SystemExit(f"{json_path} does not exist.")
        payload = previous
    else:
        token = os.getenv(args.token_env, "").strip()
        if token:
            repos = fetch_github_stats(args.org, token)
            check_token_coverage(args.org, token, repos)
        elif args.local_repo:
            repos = collect_local_repos(args.local_repo)
        else:
            raise SystemExit("No GitHub token or local repositories were provided.")
        payload = merge_with_previous(args.org, build_data(repos), previous)
        write_text(json_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    assets = Path(args.assets)
    write_text(assets / "header.svg", build_header_svg(payload))
    write_text(assets / "snapshot.svg", build_snapshot_svg(payload))

    repo_dir = assets / "repos"
    wanted = {f"{slug(str(repo['name']))}.svg" for repo in payload.get("publicRepos", [])}
    if repo_dir.exists():
        for stale in repo_dir.glob("*.svg"):
            if stale.name not in wanted:
                stale.unlink()
    for repo in payload.get("publicRepos", []):
        write_text(repo_dir / f"{slug(str(repo['name']))}.svg", build_repo_card_svg(repo))

    if args.readme:
        replace_block(Path(args.readme), "repos", format_repos_markdown(payload))


if __name__ == "__main__":
    main()
