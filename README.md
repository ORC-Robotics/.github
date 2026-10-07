# ORC Robotics Organization Profile

This repository powers the public organization profile shown at [github.com/ORC-Robotics](https://github.com/ORC-Robotics).

- Profile content lives in `profile/README.md`
- Generated assets live in `profile/assets/`: `header.svg`, `snapshot.svg`, one card per public repository in `repos/`, and the `org-stats.json` they are drawn from
- The generator lives in `scripts/generate_org_stats.py`; the refresh workflow lives in `.github/workflows/refresh-org-stats.yml`

The workflow runs daily and on pushes to `main`. When nothing changed in the organization, the output is byte-identical and nothing is committed. `org-stats.json` keeps a history of every change, which feeds the "language share over time" chart.

- `ORG_STATS_TOKEN` lets the workflow include private ORC-Robotics repositories. It needs read access to the organization's repositories. Private repositories are counted and shown as anonymous activity; their names and descriptions are never written to the profile.
- To redraw the SVGs after a design change without calling the API: `python scripts/generate_org_stats.py --org ORC-Robotics --assets profile/assets --json profile/assets/org-stats.json --readme profile/README.md --render-only` (needs `pip install fonttools brotli`).

The SVGs embed subsets of [Faustina](https://github.com/Omnibus-Type/Faustina) and [Geist](https://github.com/vercel/geist-font), both under the SIL Open Font License (see `scripts/fonts/`).
