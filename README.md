# ORC Robotics Organization Profile

This repository powers the public organization profile shown at [github.com/ORC-Robotics](https://github.com/ORC-Robotics).

- Profile content lives in `profile/README.md`
- Generated assets live in `profile/assets/`
- The refresh workflow lives in `.github/workflows/refresh-org-stats.yml`

No local clone is required for normal updates. The workflow refreshes the profile automatically through the GitHub API once `ORG_STATS_TOKEN` is configured.

- `ORG_STATS_TOKEN` lets the workflow include private ORC-Robotics repositories in the generated snapshot.
- For private repository stats, the token should have enough read access to the ORC-Robotics repositories.
