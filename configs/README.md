# Benchmark configuration files

This directory contains the YAML configuration files that define how benchmark comparisons are performed.
The comparison script itself is intentionally generic.
All domain-specific logic such as which CSV files to read, which metrics to compare, and how values should be transformed lives here.

If you want to change how benchmarks are compared, this is the place to do it.

## What these configs do

Each config file describes:

- which CSV files should be read
- how to extract values from those files
- how to transform those values into comparable numbers
- which experiments and metrics should be compared
- how results should be presented

The script loads one config per run and follows it exactly.
No comparison logic is hardcoded in Python.

## Config selection

There are four config templates provided, one for each combination of baseline and proposed inputs:

- `configs_repo_repo.yaml`
  Both baseline and proposed are repository directories.

- `configs_repo_wandb.yaml`
  Baseline is a repository directory, proposed is a wandb CSV export.

- `configs_wandb_repo.yaml`
  Baseline is a wandb CSV export, proposed is a repository directory.

- `configs_wandb_wandb.yaml`
  Both baseline and proposed are wandb CSV exports.

The comparison script selects the appropriate config automatically based on the command-line arguments.
You can override this behavior with `--config` if needed, but this is usually not necessary.

## Path variables

All file paths in the configs should be written using full-path variables provided by the CLI:

- `${BASELINE_PATH}`
- `${PROPOSED_PATH}`

These variables are expanded before any CSVs are loaded.

Example:

```yaml
files:
  ycb10_base:
    path: "${BASELINE_PATH}/benchmarks/ycb_10objs.csv"
```


## Top-level structure

Each config follows the same top-level structure:

```yaml
files:
metrics:
experiments:
output:
```

Each section serves a specific purpose.

### Files

The files section defines named CSV sources. These names are later referenced by experiments. For example:

```yaml
files:
  ycb10_base:
    path: "${BASELINE_PATH}/benchmarks/ycb_10objs.csv"
    csv:
      sep: ","
      header: true
      encoding: "utf-8"

  wandb_prop:
    path: "${PROPOSED_PATH}"
    csv:
      sep: ","
      header: true
      encoding: "utf-8"
```


### Metrics

Metrics describe how values are extracted and transformed from CSV columns. They are defined once and reused across experiments.

```yaml
metrics:
  percent_correct:
    output_name: percent_correct
    units: "%"
    higher_is_better: true
    tolerance: 0.10
    columns:
      baseline:
        column: "Correct (%)|align right"
        transforms:
          - kind: to_number
          - kind: clip
            min: 0
            max: 100
          - kind: round
            decimals: 2
      proposed:
        column: "overall/percent_correct"
        transforms:
          - kind: to_number
          - kind: clip
            min: 0
            max: 100
          - kind: round
            decimals: 2
```
The above metric (`percent_correct`) provides information about how to extract the accuracy scores from baseline and proposed csv files.
For example, in the baseline csv, it will look for the column `Correct (%)|align right`, and apply a set of transformations to it (i.e., convert to number, clip and round the value).
The column and transformations can vary based on where the data is coming from.

Metrics should be written to represent a single conceptual quantity. If two quantities differ meaningfully, they should be separate metrics.

### Experiments

The experiments section ties files and metrics together. Each entry specifies:
- the experiment name
- which files contain the baseline and proposed results
- which metrics should be computed

For example:

```yaml
experiments:
  - name: "base_config_10distinctobj_dist_agent"
    files:
      baseline: ycb10_base
      proposed: wandb_prop
    metrics:
      - percent_correct
      - used_mlh
      - match_steps
      - rotation_error
      - runtime
      - episode_runtime
```

This will find the specified experiment name in the baseline and proposed csv files, apply the specified metrics (including transformations).
Different experiment benchmarks can have different metrics (e.g., `unsupervised_inference` vs. `logos_on_objects`)

### Output

The output section controls how results are presented.

For example:

```yaml
output:
  mode: table # table | plot
  table:
    float_precision: 2
    include_units_in_headers: true
    show_deltas: true
    show_pass_fail: true
    pass_fail_symbols:
      pass: "⬜"
      better: "✅"
      worse: "❌"
      na: "—"
```

Other configs may be provided for the plot mode.


