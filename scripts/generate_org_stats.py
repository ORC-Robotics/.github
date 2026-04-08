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
    lines = ["<!-- languages:start -->"]
    for language in languages[:8]:
        lines.append(
            f"- `{language['name']}` - {language['percent']:.1f}% of the current organization code footprint"
        )
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

    cx = 230
    cy = 240
    outer_radius = 126
    inner_radius = 72
    start_angle = -90.0

    slices = []
    legend_items = []

    display_languages = languages[:6]
    if len(languages) > 6:
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

    legend_y = 122
    for language in display_languages:
        legend_items.append(
            f'''
      <circle cx="520" cy="{legend_y}" r="8" fill="{language["color"]}" />
      <text x="540" y="{legend_y + 5}" fill="#e2e8f0" font-size="18" font-family="Segoe UI, Arial, sans-serif">
        {language["name"]}
      </text>
      <text x="810" y="{legend_y + 5}" fill="#f8fafc" font-size="18" font-family="Segoe UI, Arial, sans-serif" text-anchor="end">
        {float(language["percent"]):.1f}%
      </text>'''
        )
        legend_y += 42

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="480" viewBox="0 0 900 480" role="img" aria-labelledby="title desc">
  <title id="title">ORC Robotics organization snapshot</title>
  <desc id="desc">A pie style chart showing ORC Robotics repositories and language distribution.</desc>
  <rect x="20" y="20" width="860" height="440" rx="28" fill="#0f172a" />
  <text x="52" y="78" fill="#f8fafc" font-size="30" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    ORC Robotics Snapshot
  </text>
  <text x="52" y="108" fill="#94a3b8" font-size="16" font-family="Segoe UI, Arial, sans-serif">
    Repository footprint and language distribution
  </text>
  <rect x="690" y="48" width="148" height="34" rx="17" fill="#123b2a" />
  <text x="764" y="70" fill="#9ae6b4" font-size="14" font-family="Segoe UI, Arial, sans-serif" text-anchor="middle">
    {repo_data["private"]} private repos included
  </text>

  {''.join(slices)}

  <text x="{cx}" y="{cy - 8}" fill="#f8fafc" font-size="52" font-family="Segoe UI, Arial, sans-serif" font-weight="700" text-anchor="middle">
    {repo_data["total"]}
  </text>
  <text x="{cx}" y="{cy + 24}" fill="#94a3b8" font-size="18" font-family="Segoe UI, Arial, sans-serif" text-anchor="middle">
    repositories
  </text>
  <text x="{cx}" y="{cy + 52}" fill="#cbd5e1" font-size="14" font-family="Segoe UI, Arial, sans-serif" text-anchor="middle">
    {repo_data["public"]} public / {repo_data["private"]} private
  </text>

  <text x="520" y="88" fill="#f8fafc" font-size="24" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    Language Distribution
  </text>
  <text x="520" y="110" fill="#94a3b8" font-size="15" font-family="Segoe UI, Arial, sans-serif">
    Calculated from the organization code footprint
  </text>
  {''.join(legend_items)}

  <line x1="520" y1="390" x2="838" y2="390" stroke="#1e293b" stroke-width="1" />
  <text x="520" y="420" fill="#94a3b8" font-size="14" font-family="Segoe UI, Arial, sans-serif">
    Updated {payload["generatedAt"]}
  </text>
</svg>
'''


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_svg(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_svg(payload), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ORC Robotics organization stats.")
    parser.add_argument("--org", required=True, help="GitHub organization name.")
    parser.add_argument("--readme", required=True, help="Path to the profile README.")
    parser.add_argument("--svg", required=True, help="Path to the generated SVG card.")
    parser.add_argument("--json", required=True, help="Path to the generated stats JSON.")
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

    token = os.getenv(args.token_env, "").strip()
    if token:
        repos, language_totals = fetch_github_stats(args.org, token)
    elif args.local_repo:
        repos, language_totals = merge_local_stats(args.local_repo)
    else:
        raise SystemExit("No GitHub token or local repositories were provided.")

    payload = build_stats_payload(args.org, repos, language_totals)
    write_json(Path(args.json), payload)
    write_svg(Path(args.svg), payload)
    replace_stats_section(Path(args.readme), format_stats_markdown(payload))
    replace_languages_section(Path(args.readme), format_languages_markdown(payload))


if __name__ == "__main__":
    main()
