#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
    "JavaScript": "#6b6426",
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


def normalize_payload_colors(payload: dict[str, object]) -> dict[str, object]:
    normalized_languages = []
    for language in payload.get("languages", []):
        normalized = dict(language)
        name = str(normalized.get("name", "Other"))
        normalized["color"] = LANGUAGE_COLORS.get(name, LANGUAGE_COLORS["Other"])
        normalized_languages.append(normalized)

    normalized_payload = dict(payload)
    normalized_payload["languages"] = normalized_languages
    return normalized_payload


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


def build_svg(payload: dict[str, object]) -> str:
    repo_data = payload["repositories"]
    languages = payload["languages"]
    display_languages = languages[:5]
    if len(languages) > 5:
        other_percent = round(
            max(0.0, 100 - sum(float(language["percent"]) for language in display_languages)),
            1,
        )
        if other_percent > 0:
            display_languages.append(
                {"name": "Other", "percent": other_percent, "color": LANGUAGE_COLORS["Other"]}
            )

    max_percent = max((float(language["percent"]) for language in display_languages), default=1.0)
    language_rows = []
    row_y = 288
    for language in display_languages:
        width = max(14.0, 284.0 * float(language["percent"]) / max_percent)
        language_rows.append(
            f'''
      <g transform="translate(0,{row_y})">
        <text x="500" y="0" fill="#dce9ff" font-size="16" font-family="Segoe UI, Arial, sans-serif" font-weight="600">
        {language["name"]}
        </text>
        <text x="860" y="0" fill="#ffffff" font-size="16" font-family="Segoe UI, Arial, sans-serif" font-weight="700" text-anchor="end">
        {float(language["percent"]):.1f}%
        </text>
        <rect x="500" y="14" width="294" height="12" rx="6" fill="#1b2848" />
        <rect x="500" y="14" width="{width:.2f}" height="12" rx="6" fill="{language["color"]}" />
        <circle cx="814" cy="20" r="4" fill="{language["color"]}" opacity="0.95" />
      </g>'''
        )
        row_y += 48

    total = int(repo_data["total"])
    public_count = int(repo_data["public"])
    private_count = int(repo_data["private"])
    private_ratio = private_count / total if total else 0
    public_width = 336.0 * (1 - private_ratio)
    private_width = 336.0 * private_ratio

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="700" viewBox="0 0 1040 700" role="img" aria-labelledby="title desc">
  <title id="title">ORC Robotics organization snapshot</title>
  <desc id="desc">A telemetry style chart showing ORC Robotics repositories and language distribution.</desc>
  <defs>
    <pattern id="grid" width="28" height="28" patternUnits="userSpaceOnUse">
      <path d="M 28 0 L 0 0 0 28" fill="none" stroke="#12213d" stroke-width="1" />
    </pattern>
    <linearGradient id="surface" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0f1b34" />
      <stop offset="100%" stop-color="#091224" />
    </linearGradient>
    <linearGradient id="publicBar" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#55d6ff" />
      <stop offset="100%" stop-color="#4b8cff" />
    </linearGradient>
  </defs>

  <rect width="1040" height="700" fill="#060f1d" />
  <rect x="18" y="18" width="1004" height="664" rx="34" fill="url(#grid)" />
  <rect x="18" y="18" width="1004" height="664" rx="34" fill="#081427" fill-opacity="0.9" />

  <text x="58" y="86" fill="#f8fbff" font-size="32" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    ORC Robotics Snapshot
  </text>
  <text x="58" y="118" fill="#8fa6cb" font-size="16" font-family="Segoe UI, Arial, sans-serif">
    Telemetry view for repository footprint and language usage across the organization
  </text>

  <rect x="806" y="58" width="178" height="32" rx="16" fill="#133926" />
  <text x="895" y="78" fill="#9ae6b4" font-size="13" font-family="Segoe UI, Arial, sans-serif" font-weight="700" text-anchor="middle">
    private repositories included
  </text>

  <rect x="42" y="148" width="956" height="504" rx="30" fill="#0a1730" />
  <rect x="64" y="176" width="374" height="448" rx="24" fill="url(#surface)" />
  <rect x="456" y="176" width="520" height="448" rx="24" fill="url(#surface)" />

  <text x="88" y="214" fill="#8fa6cb" font-size="13" font-family="Segoe UI, Arial, sans-serif" font-weight="700" letter-spacing="1.2">
    REPOSITORY TELEMETRY
  </text>
  <text x="88" y="308" fill="#ffffff" font-size="118" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    {total}
  </text>
  <text x="92" y="350" fill="#9fb1cf" font-size="18" font-family="Segoe UI, Arial, sans-serif">
    repositories tracked
  </text>
  <text x="92" y="376" fill="#667a9e" font-size="14" font-family="Segoe UI, Arial, sans-serif">
    live organization footprint from the current GitHub API snapshot
  </text>

  <rect x="88" y="416" width="152" height="92" rx="18" fill="#0d2242" />
  <text x="110" y="450" fill="#8fa6cb" font-size="12" font-family="Segoe UI, Arial, sans-serif" font-weight="700" letter-spacing="1.1">
    PUBLIC
  </text>
  <text x="110" y="492" fill="#55d6ff" font-size="36" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    {public_count}
  </text>

  <rect x="254" y="416" width="152" height="92" rx="18" fill="#0d2242" />
  <text x="276" y="450" fill="#8fa6cb" font-size="12" font-family="Segoe UI, Arial, sans-serif" font-weight="700" letter-spacing="1.1">
    PRIVATE
  </text>
  <text x="276" y="492" fill="#ff4f8b" font-size="36" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    {private_count}
  </text>

  <text x="88" y="542" fill="#8fa6cb" font-size="12" font-family="Segoe UI, Arial, sans-serif" font-weight="700" letter-spacing="1.1">
    VISIBILITY SPLIT
  </text>
  <rect x="88" y="556" width="336" height="16" rx="8" fill="#142340" />
  <rect x="88" y="556" width="{public_width:.2f}" height="16" rx="8" fill="url(#publicBar)" />
  <rect x="{88 + public_width:.2f}" y="556" width="{private_width:.2f}" height="16" rx="8" fill="#ff4f8b" />

  <text x="88" y="598" fill="#55d6ff" font-size="13" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    public
  </text>
  <text x="370" y="598" fill="#ff4f8b" font-size="13" font-family="Segoe UI, Arial, sans-serif" font-weight="700" text-anchor="end">
    private
  </text>

  <text x="484" y="214" fill="#8fa6cb" font-size="13" font-family="Segoe UI, Arial, sans-serif" font-weight="700" letter-spacing="1.2">
    LANGUAGE FOOTPRINT
  </text>
  <text x="484" y="244" fill="#ffffff" font-size="30" font-family="Segoe UI, Arial, sans-serif" font-weight="700">
    Current Language Distribution
  </text>
  <text x="484" y="270" fill="#7f96ba" font-size="14" font-family="Segoe UI, Arial, sans-serif">
    Scaled bars highlight dominant languages without crowding the panel.
  </text>

  {''.join(language_rows)}

  <line x1="484" y1="582" x2="862" y2="582" stroke="#22314f" stroke-width="1" />
  <text x="484" y="610" fill="#8fa6cb" font-size="14" font-family="Segoe UI, Arial, sans-serif">
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

    payload = normalize_payload_colors(payload)

    write_json(Path(args.json), payload)
    write_svg(Path(args.svg), payload)
    write_badges_svg(Path(args.badges_svg), payload)
    replace_stats_section(Path(args.readme), format_stats_markdown(payload))
    replace_languages_section(Path(args.readme), format_languages_markdown(payload))


if __name__ == "__main__":
    main()
