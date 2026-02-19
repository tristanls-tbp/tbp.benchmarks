# tbp.benchmarks

This is a helper tool for comparing [tbp.monty](https://github.com/thousandbrainsproject/tbp.monty) benchmark run results.

- [Installation](#installation)
- [Usage](#usage)

The tool supports comparing a baseline against a proposed set of results, producing Markdown tables or plots (WIP), and optionally writing updated results back into baseline CSVs.


> **Disclaimer**
>
> This repository contains code written with the assistance of LLMs and reviewed by humans (myself). It is provided as-is. Please review the code and outputs carefully and use it at your own risk.

## Installation

To use the tool, clone this repository.

```zsh
git clone https://github.com/ramyamounir/tbp.benchmarks.git
```

### Install `uv`

The project is managed using [uv](https://docs.astral.sh/uv/), which handles virtual environments, dependency installation, and running console scripts.

On macOS:

```bash
brew install uv
```

For other platforms, see the [uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

### Install dependencies

From the repository root, run:

```zsh
uv sync
```

This creates a virtual environment in `.venv/`, installs all required dependencies, and registers the compare console script defined in pyproject.toml.


## Usage

After installation, you can invoke the comparison tool using the compare command via uv.

To see the full help menu and available options:

```zsh
uv run compare --help

usage: compare.py [-h] --baseline BASELINE --proposed PROPOSED [--baseline-label BASELINE_LABEL] [--proposed-label PROPOSED_LABEL]
                  [--config CONFIG] [--configs-dir CONFIGS_DIR] [--mode {table,plot}] [--out OUT] [--modify-in-place] [--dry-run]
                  [--backup-suffix BACKUP_SUFFIX]

Compare benchmark runs using YAML configs.

options:
  -h, --help            show this help message and exit
  --baseline BASELINE   Baseline selection: a folder (repo) or a .csv file (wandb).
  --proposed PROPOSED   Proposed selection: a folder (repo) or a .csv file (wandb).
  --baseline-label BASELINE_LABEL
                        Header label for the Baseline column in markdown tables.
  --proposed-label PROPOSED_LABEL
                        Header label for the Proposed column in markdown tables.
  --config CONFIG       Optional explicit config YAML path. Overrides automatic selection.
  --configs-dir CONFIGS_DIR
                        Directory containing the 4 template configs.
  --mode {table,plot}   Override output.mode from YAML.
  --out OUT             If mode=table, optional Markdown output path.
  --modify-in-place     Write proposed values into baseline repo CSVs in-place.
  --dry-run             With --modify-in-place, show changes but do not write files.
  --backup-suffix BACKUP_SUFFIX
                        With --modify-in-place, create a backup next to each modified CSV.
```

This will display the command-line interface, including required arguments such as `--baseline` and `--proposed`, optional output path, and the modify-in-place options.

At a minimum, you must provide a baseline and a proposed input. Each can be either a repository directory (e.g., `~/tbp/tbp.monty`) or a CSV file (`wandb_files/export.csv`).

For a full benchmark WandB exports, make sure you include the following columns:
```txt
Runtime
overall/percent_correct
overall/percent_correct_child_or_parent
overall/avg_rotation_error
overall/avg_num_monty_matching_steps
overall/percent_used_mlh_after_timeout
overall/avg_episode_run_time
overall/avg_prediction_error
```

Examples:

```zsh
uv run compare \
  --baseline /path/to/baseline_repo \
  --proposed /path/to/proposed_repo
```

Or when comparing against a wandb export:

```zsh
uv run compare \
  --baseline /path/to/baseline_repo \
  --proposed /path/to/wandb_export.csv
```

By default, the tool prints a Markdown table to stdout. You can redirect this output or write it to a file using `--out`.

```zsh
uv run compare \
  --baseline /path/to/baseline_repo \
  --proposed /path/to/proposed_repo \
  --out summary.md
```

In addition to comparing results, the tool can update baseline CSV files in place by writing proposed values into the baseline columns. This is useful once you decide that the proposed results should replace the existing baseline.
Write-back is only supported when the baseline input is a repository directory (e.g., `tbp.monty`).

Before modifying any files, you should always run a dry run to see exactly what would change.
```zsh
uv run compare \
  --baseline /path/to/baseline_repo \
  --proposed /path/to/proposed_repo \
  --modify-in-place \
  [--dry-run]
```

This prints a summary of all changes and only writes it to disk if you did not specify the `--dry-run` option. For example:

```zsh
=== Modify: benchmarks/summary.csv ===
randrot_noise_10distinctobj_dist_agent | Correct (%) | 82.50 -> 84.10
randrot_noise_10distinctobj_dist_agent | Run Time (mins) | 12 -> 11
```

For details on how comparisons are configured, including metric definitions and file mappings, see the configuration documentation in the [configs README.md](configs/README.md).
