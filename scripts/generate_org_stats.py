#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


LANGUAGE_COLORS = {
    "C++": "#f34b7d",
    "Python": "#3572A5",
    "TypeScript": "#3178c6",
    "CSS": "#663399",
    "PowerShell": "#012456",
    "CMake": "#2f8f4e",
    "JavaScript": "#f1e05a",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "Other": "#94a3b8",
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

IGNORED_SUFFIXES = {
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".json",
    ".lck",
    ".lock",
    ".md",
    ".pdf",
    ".png",
    ".properties",
    ".sim",
    ".svg",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass
class RepoStat:
    name: str
    visibility: str


def github_get(url: str, token: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ORC-Robotics-Stats",
        "Authorization": f"Bearer {token}",
    }
    request = Request(url, headers=headers)
    with urlopen(request) as response:
        return json.load(response)


def fetch_github_stats(org: str, token: str) -> tuple[list[RepoStat], dict[str, int]]:
    repos: list[RepoStat] = []
    language_totals: dict[str, int] = defaultdict(int)
    page = 1

    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&type=all&page={page}"
        items = github_get(url, token)
        if not items:
            break

        for item in items:
            visibility = "private" if item["private"] else "public"
            repos.append(RepoStat(name=item["name"], visibility=visibility))
            try:
                languages = github_get(item["languages_url"], token)
            except HTTPError:
                continue

            for language, size in languages.items():
                language_totals[language] += int(size)

        page += 1

    return repos, dict(language_totals)


def parse_local_repo(value: str) -> tuple[RepoStat, Path]:
    name, visibility, raw_path = value.split("|", 2)
    return RepoStat(name=name, visibility=visibility), Path(raw_path)


def detect_language(path: Path) -> str | None:
    if path.name == "CMakeLists.txt":
        return "CMake"

    suffix = path.suffix.lower()
    if suffix in IGNORED_SUFFIXES:
        return None

    return LANGUAGE_BY_EXTENSION.get(suffix)


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
            if size <= 0:
                continue
            totals[language] += size

    return dict(totals)


def merge_local_stats(repo_args: Iterable[str]) -> tuple[list[RepoStat], dict[str, int]]:
    repos: list[RepoStat] = []
    language_totals: dict[str, int] = defaultdict(int)

    for value in repo_args:
        repo, path = parse_local_repo(value)
        repos.append(repo)
        for language, size in analyze_local_repo(path).items():
            language_totals[language] += size

    return repos, dict(language_totals)


def summarize_languages(language_totals: dict[str, int]) -> list[dict[str, float | str]]:
    total = sum(language_totals.values())
    if total == 0:
        return []

    items = sorted(language_totals.items(), key=lambda item: item[1], reverse=True)
    summary = []
    for language, size in items:
        summary.append(
            {
                "name": language,
                "bytes": size,
                "percent": round(size * 100 / total, 1),
                "color": LANGUAGE_COLORS.get(language, LANGUAGE_COLORS["Other"]),
            }
        )
    return summary


def build_stats_payload(org: str, repos: list[RepoStat], language_totals: dict[str, int]) -> dict[str, object]:
    languages = summarize_languages(language_totals)
    public_count = sum(1 for repo in repos if repo.visibility == "public")
    private_count = sum(1 for repo in repos if repo.visibility == "private")

    return {
        "organization": org,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "repositories": {
            "total": len(repos),
            "public": public_count,
            "private": private_count,
            "names": [repo.name for repo in sorted(repos, key=lambda repo: repo.name.lower())],
        },
        "languages": languages,
    }


def format_stats_markdown(payload: dict[str, object]) -> str:
    repo_data = payload["repositories"]
    languages = payload["languages"]
    language_summary = ", ".join(
        f"`{language['name']} {language['percent']:.1f}%`" for language in languages[:6]
    )

    lines = [
        "<!-- stats:start -->",
        f"- Repositories analyzed: `{repo_data['total']} total` (`{repo_data['private']} private`, `{repo_data['public']} public`)",
        f"- Language distribution by code footprint: {language_summary}",
        "- This snapshot includes private repositories without exposing internal source code.",
        f"- Last updated: `{payload['generatedAt']}`",
        "<!-- stats:end -->",
    ]
    return "\n".join(lines)


def format_languages_markdown(payload: dict[str, object]) -> str:
    languages = payload["languages"]
    language_names = ", ".join(f"`{language['name']}`" for language in languages[:8])
    lines = ["<!-- languages:start -->"]
    lines.append('<p align="left">')
    lines.append('  <img src="./assets/language-badges.svg" alt="Current ORC Robotics language badges" width="860" />')
    lines.append("</p>")
    lines.append("")
    lines.append(f"Active languages currently detected across ORC Robotics: {language_names}.")
    lines.append("<!-- languages:end -->")
    return "\n".join(lines)


def replace_stats_section(readme_path: Path, new_block: str) -> None:
    content = readme_path.read_text(encoding="utf-8")
    start_marker = "<!-- stats:start -->"
    end_marker = "<!-- stats:end -->"

    start_index = content.index(start_marker)
    end_index = content.index(end_marker) + len(end_marker)
    updated = content[:start_index] + new_block + content[end_index:]
    readme_path.write_text(updated, encoding="utf-8")


def replace_languages_section(readme_path: Path, new_block: str) -> None:
    content = readme_path.read_text(encoding="utf-8")
    start_marker = "<!-- languages:start -->"
    end_marker = "<!-- languages:end -->"

    start_index = content.index(start_marker)
    end_index = content.index(end_marker) + len(end_marker)
    updated = content[:start_index] + new_block + content[end_index:]
    readme_path.write_text(updated, encoding="utf-8")


def polar_to_cartesian(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    radians = math.radians(angle)
    return cx + radius * math.cos(radians), cy + radius * math.sin(radians)


def build_arc_path(
    cx: float,
    cy: float,
    outer_radius: float,
    inner_radius: float,
    start_angle: float,
    end_angle: float,
) -> str:
    start_outer = polar_to_cartesian(cx, cy, outer_radius, start_angle)
    end_outer = polar_to_cartesian(cx, cy, outer_radius, end_angle)
    start_inner = polar_to_cartesian(cx, cy, inner_radius, end_angle)
    end_inner = polar_to_cartesian(cx, cy, inner_radius, start_angle)
    large_arc_flag = 1 if end_angle - start_angle > 180 else 0

    return (
        f"M {start_outer[0]:.2f} {start_outer[1]:.2f} "
        f"A {outer_radius:.2f} {outer_radius:.2f} 0 {large_arc_flag} 1 {end_outer[0]:.2f} {end_outer[1]:.2f} "
        f"L {start_inner[0]:.2f} {start_inner[1]:.2f} "
        f"A {inner_radius:.2f} {inner_radius:.2f} 0 {large_arc_flag} 0 {end_inner[0]:.2f} {end_inner[1]:.2f} Z"
    )


def build_svg(payload: dict[str, object]) -> str:
    repo_data = payload["repositories"]
    languages = payload["languages"]
    total_languages = sum(language["percent"] for language in languages)

    cx = 246
    cy = 285
    outer_radius = 132
    inner_radius = 82
    start_angle = -90.0

    slices = []
    legend_items = []

    display_languages = languages[:5]
    if len(languages) > 5:
        other_percent = round(max(0.0, total_languages - sum(lang["percent"] for lang in display_languages)), 1)
        if other_percent > 0:
            display_languages.append({"name": "Other", "percent": other_percent, "color": LANGUAGE_COLORS["Other"]})

    if display_languages:
        for language in display_languages:
            sweep = 360 * float(language["percent"]) / 100
            end_angle = start_angle + sweep
            path = build_arc_path(cx, cy, outer_radius, inner_radius, start_angle, end_angle)
            slices.append(
                f'<path d="{path}" fill="{language["color"]}" stroke="#0f172a" stroke-width="2" />'
            )
            start_angle = end_angle
    else:
        fallback = build_arc_path(cx, cy, outer_radius, inner_radius, -90, 270)
        slices.append(f'<path d="{fallback}" fill="#334155" stroke="#0f172a" stroke-width="2" />')

    legend_y = 218
    for language in display_languages:
        legend_items.append(
            f'''
      <rect x="500" y="{legend_y - 18}" width="320" height="34" rx="17" fill="#172036" />
      <circle cx="523" cy="{legend_y - 1}" r="7" fill="{language["color"]}" />
      <text x="542" y="{legend_y + 4}" fill="#e2e8f0" font-size="16" font-family="Segoe UI, Arial, sans-serif" font-weight="600">
        {language["name"]}
      </text>
      <text x="800" y="{legend_y + 4}" fill="#f8fafc" font-size="16" font-family="Segoe UI, Arial, sans-serif" font-weight="700" text-anchor="end">
        {float(language["percent"]):.1f}%
      </text>'''
        )
        legend_y += 42

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="920" height="540" viewBox="0 0 920 540" role="img" aria-labelledby="title desc">
  <title id="title">ORC Robotics organization snapshot</title>
  <desc id="desc">A pie style chart showing ORC Robotics repositories and language distribution.</desc>
  <rect x="20" y="20" width="880" height="500" rx="28" fill="#0f172a" />
  <rect x="42" y="108" width="836" height="380" rx="24" fill="#111a30" />
  <text x="56" y="72" fill="#f8fafc" font-size="30" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    ORC Robotics Snapshot
  </text>
  <text x="56" y="98" fill="#94a3b8" font-size="16" font-family="Segoe UI, Arial, sans-serif">
    Repository footprint and language distribution
  </text>
  <rect x="706" y="50" width="152" height="30" rx="15" fill="#123b2a" />
  <text x="782" y="69" fill="#9ae6b4" font-size="13" font-family="Segoe UI, Arial, sans-serif" text-anchor="middle" font-weight="700">
    {repo_data["private"]} private repos included
  </text>
  <line x1="458" y1="138" x2="458" y2="452" stroke="#22314f" stroke-width="1" />

  {''.join(slices)}

  <text x="{cx}" y="{cy - 8}" fill="#f8fafc" font-size="52" font-family="Segoe UI, Arial, sans-serif" font-weight="700" text-anchor="middle">
    {repo_data["total"]}
  </text>
  <text x="{cx}" y="{cy + 24}" fill="#94a3b8" font-size="18" font-family="Segoe UI, Arial, sans-serif" text-anchor="middle">
    repositories
  </text>
  <text x="{cx}" y="{cy + 50}" fill="#cbd5e1" font-size="14" font-family="Segoe UI, Arial, sans-serif" text-anchor="middle">
    {repo_data["public"]} public / {repo_data["private"]} private
  </text>

  <text x="500" y="160" fill="#f8fafc" font-size="24" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    Language Distribution
  </text>
  <text x="500" y="184" fill="#94a3b8" font-size="14" font-family="Segoe UI, Arial, sans-serif">
    Calculated from the organization code footprint
  </text>
  {''.join(legend_items)}

  <line x1="500" y1="462" x2="820" y2="462" stroke="#22314f" stroke-width="1" />
  <text x="500" y="486" fill="#94a3b8" font-size="14" font-family="Segoe UI, Arial, sans-serif">
    Updated {payload["generatedAt"]}
  </text>
</svg>
'''


def build_language_badges_svg(payload: dict[str, object]) -> str:
    languages = payload["languages"][:8]
    start_x = 8
    start_y = 8
    badge_height = 34
    x = start_x
    y = start_y
    max_width = 860
    row_gap = 12
    badge_gap = 10
    badges = []

    for language in languages:
        label = str(language["name"]).upper()
        percent = f"{float(language['percent']):.1f}%"
        label_width = max(46, len(label) * 8 + 18)
        percent_width = len(percent) * 8 + 18
        badge_width = label_width + percent_width + 18

        if x + badge_width > max_width:
            x = start_x
            y += badge_height + row_gap

        badges.append(
            f'''
  <g transform="translate({x},{y})">
    <rect width="{badge_width}" height="{badge_height}" rx="9" fill="{language["color"]}" />
    <text x="14" y="22" fill="#ffffff" font-size="13" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
      {label}
    </text>
    <rect x="{badge_width - percent_width - 8}" y="5" width="{percent_width}" height="24" rx="7" fill="#0f172a" fill-opacity="0.22" />
    <text x="{badge_width - percent_width / 2 - 8}" y="22" fill="#ffffff" font-size="12" font-family="Segoe UI, Arial, sans-serif" font-weight="700" text-anchor="middle">
      {percent}
    </text>
  </g>'''
        )
        x += badge_width + badge_gap

    height = y + badge_height + start_y
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="{height}" viewBox="0 0 860 {height}" role="img" aria-labelledby="title">
  <title id="title">Current ORC Robotics language badges</title>
  {''.join(badges)}
</svg>
'''


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_svg(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_svg(payload), encoding="utf-8")


def write_badges_svg(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_language_badges_svg(payload), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ORC Robotics organization stats.")
    parser.add_argument("--org", required=True, help="GitHub organization name.")
    parser.add_argument("--readme", required=True, help="Path to the profile README.")
    parser.add_argument("--svg", required=True, help="Path to the generated SVG card.")
    parser.add_argument("--badges-svg", required=True, help="Path to the generated language badges SVG.")
    parser.add_argument("--json", required=True, help="Path to the generated stats JSON.")
    parser.add_argument("--input-json", help="Optional existing stats JSON to reuse as input.")
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

    if args.input_json:
        payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    else:
        token = os.getenv(args.token_env, "").strip()
        if token:
            repos, language_totals = fetch_github_stats(args.org, token)
        elif args.local_repo:
            repos, language_totals = merge_local_stats(args.local_repo)
        else:
            raise SystemExit("No GitHub token, input JSON, or local repositories were provided.")
        payload = build_stats_payload(args.org, repos, language_totals)

    write_json(Path(args.json), payload)
    write_svg(Path(args.svg), payload)
    write_badges_svg(Path(args.badges_svg), payload)
    replace_stats_section(Path(args.readme), format_stats_markdown(payload))
    replace_languages_section(Path(args.readme), format_languages_markdown(payload))


if __name__ == "__main__":
    main()
