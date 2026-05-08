from __future__ import annotations

import argparse
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt
import pandas as pd
import yaml

# ============================== Config helpers ==============================


def get_nested(d: dict, path: Iterable[str], default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


# ============================== YAML ${...} expansion ==============================

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_vars_in_string(s: str, mapping: dict[str, str]) -> str:
    """
    Expand ${VARNAME} occurrences using mapping first, then os.environ, else leave unchanged.
    """

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in mapping:
            return mapping[key]
        if key in os.environ:
            return os.environ[key]
        return m.group(0)

    return _VAR_PATTERN.sub(repl, s)


def expand_vars_in_cfg(obj: Any, mapping: dict[str, str]) -> Any:
    """
    Recursively expand ${VARS} in all strings in a config structure.
    """
    if isinstance(obj, str):
        return _expand_vars_in_string(obj, mapping)
    if isinstance(obj, list):
        return [expand_vars_in_cfg(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: expand_vars_in_cfg(v, mapping) for k, v in obj.items()}
    return obj


def cli_var_mapping(
    b_kind: str, p_kind: str, baseline: Path, proposed: Path
) -> dict[str, str]:
    """Values injected into YAML ${...} placeholders."""
    return {
        "BASELINE_PATH": str(baseline.expanduser().resolve()),
        "PROPOSED_PATH": str(proposed.expanduser().resolve()),
    }


# ============================== Transforms (ordered) ==============================


def _normalize_transforms(transforms) -> list[dict]:
    """
    Accepts None | dict | str | list[dict|str]; returns list[dict] with {"kind": ...}.
    """
    if transforms is None:
        return []
    if isinstance(transforms, dict):
        return [transforms]
    if isinstance(transforms, str):
        return [{"kind": transforms}]
    out = []
    for t in transforms:
        if isinstance(t, dict):
            out.append(t)
        elif isinstance(t, str):
            out.append({"kind": t})
    return out


def apply_transforms(series: pd.Series, transforms) -> pd.Series:
    """
    Apply transforms IN ORDER. Supported kinds:

      String:
        lowercase, uppercase, trim, strip_prefix(value), strip_suffix(value), regex_replace(pattern, replacement)
      Numeric parse:
        to_number  # strips trailing '%', coerces to numeric
      Unit conversions:
        rad_to_deg, deg_to_rad, sec_to_min, min_to_sec, frac_to_percent, percent_to_frac
      Numeric shaping:
        round(decimals), scale(factor), clip(min, max)
    """
    s = series.copy()
    for t in _normalize_transforms(transforms):
        kind = (t.get("kind") or "").strip()

        # ---- string ops ----
        if kind == "lowercase":
            s = s.astype(str).str.lower()
        elif kind == "uppercase":
            s = s.astype(str).str.upper()
        elif kind == "trim":
            s = s.astype(str).str.strip()
        elif kind == "strip_prefix":
            pref = str(t.get("value", ""))
            s = s.astype(str).str.removeprefix(pref)
        elif kind == "strip_suffix":
            suf = str(t.get("value", ""))
            s = s.astype(str).str.removesuffix(suf)
        elif kind == "regex_replace":
            pat = t.get("pattern", "")
            repl = t.get("replacement", "")
            s = s.astype(str).str.replace(pat, repl, regex=True)

        # ---- numeric parse ----
        elif kind == "to_number":
            s = s.astype(str).str.replace("%", "", regex=False)
            s = pd.to_numeric(s, errors="coerce")

        # ---- unit conversions ----
        elif kind == "rad_to_deg":
            s = pd.to_numeric(s, errors="coerce") * (180.0 / math.pi)
        elif kind == "deg_to_rad":
            s = pd.to_numeric(s, errors="coerce") * (math.pi / 180.0)
        elif kind == "sec_to_min":
            s = pd.to_numeric(s, errors="coerce") / 60.0
        elif kind == "min_to_sec":
            s = pd.to_numeric(s, errors="coerce") * 60.0
        elif kind == "frac_to_percent":
            s = pd.to_numeric(s, errors="coerce") * 100.0
        elif kind == "percent_to_frac":
            s = pd.to_numeric(s, errors="coerce") / 100.0

        # ---- numeric shaping ----
        elif kind == "round":
            decimals = int(t.get("decimals", 0))
            s = pd.to_numeric(s, errors="coerce").round(decimals)
            if decimals == 0:
                s = s.astype("Int64")
        elif kind == "scale":
            factor = float(t.get("factor", 1.0))
            s = pd.to_numeric(s, errors="coerce") * factor
        elif kind == "clip":
            mn = t.get("min", None)
            mx = t.get("max", None)
            s = pd.to_numeric(s, errors="coerce").clip(lower=mn, upper=mx)

    return s


# ============================== Loading CSVs ==============================


@dataclass
class FileSpec:
    key: str
    path: str
    sep: str = ","
    header: Optional[int] = 0
    encoding: str = "utf-8"
    id_column: Optional[str] = None  # optional override; not required by schema


def to_filespecs(cfg: dict) -> dict[str, FileSpec]:
    res: dict[str, FileSpec] = {}
    files = cfg.get("files", {})
    for key, meta in files.items():
        csv = meta.get("csv", {}) if isinstance(meta, dict) else {}
        res[key] = FileSpec(
            key=key,
            path=os.path.expanduser(os.path.expandvars(meta.get("path", ""))),
            sep=csv.get("sep", ","),
            header=(0 if csv.get("header", True) else None),
            encoding=csv.get("encoding", "utf-8"),
            id_column=meta.get("id_column"),  # optional
        )
    return res


ID_CANDIDATES = [
    "Experiment",
    "Name",
    "experiment",
    "name",
    "Experiment Name",
    "run_name",
]


def find_id_column(df: pd.DataFrame, preferred: Optional[str] = None) -> Optional[str]:
    if preferred and preferred in df.columns:
        return preferred
    for cand in ID_CANDIDATES:
        if cand in df.columns:
            return cand
    lower_map = {c.lower(): c for c in df.columns}
    for cand in [c.lower() for c in ID_CANDIDATES]:
        if cand in lower_map:
            return lower_map[cand]
    return None


def read_csv_filespec(fs: FileSpec) -> pd.DataFrame:
    df = pd.read_csv(
        fs.path,
        sep=fs.sep,
        header=fs.header,
        encoding=fs.encoding,
        dtype=str,
    )
    return df


def find_row_by_name(
    df: pd.DataFrame,
    id_col: str,
    exp_name: str,
    *,
    allow_suffix: bool = False,
    delimiters: str = r"_\-/:\.",  # characters allowed between name and tag
) -> Optional[pd.Series]:
    """
    Return the first matching row where:
      - exact match: df[id_col] == exp_name
      - OR (if allow_suffix): df[id_col] matches ^exp_name($|[delimiters].+)
    Comparison is case-sensitive and trims surrounding whitespace on both sides.
    If multiple rows match, the first is returned.
    """
    if id_col not in df.columns:
        return None

    col = df[id_col].astype(str).str.strip()
    name = str(exp_name).strip()

    exact = df.loc[col == name]
    if not exact.empty:
        return exact.iloc[0]

    if allow_suffix:
        safe_name = re.escape(name)
        pattern = rf"^{safe_name}(?:$|[{delimiters}].+)"
        matched = df.loc[col.str.match(pattern, na=False)]
        if not matched.empty:
            return matched.iloc[0]

    return None


# ============================== Metric comparison ==============================


@dataclass
class MetricSpec:
    key: str
    output_name: str
    units: str | None
    higher_is_better: bool
    tolerance: float
    baseline_col: str
    baseline_transforms: list[dict]
    proposed_col: str
    proposed_transforms: list[dict]


def load_metric_specs(cfg: dict) -> dict[str, MetricSpec]:
    mdefs = cfg.get("metrics", {})
    res: dict[str, MetricSpec] = {}
    for key, md in mdefs.items():
        cols = md.get("columns", {})
        b = cols.get("baseline", {})
        p = cols.get("proposed", {})
        res[key] = MetricSpec(
            key=key,
            output_name=md.get("output_name", key),
            units=md.get("units"),
            higher_is_better=bool(md.get("higher_is_better", True)),
            tolerance=float(md.get("tolerance", 0.0)),
            baseline_col=b.get("column"),
            baseline_transforms=_normalize_transforms(b.get("transforms")),
            proposed_col=p.get("column"),
            proposed_transforms=_normalize_transforms(p.get("transforms")),
        )
    return res


def result_symbol(
    base: Optional[float],
    prop: Optional[float],
    higher_is_better: bool,
    tol: float,
    symbols: Dict[str, str],
) -> str:
    if base is None or prop is None or pd.isna(base) or pd.isna(prop):
        return symbols.get("na", "—")
    diff = float(prop) - float(base)
    if abs(diff) <= float(tol):
        return symbols.get("pass", "✅")
    if higher_is_better:
        return symbols.get("better", "👍") if diff > 0 else symbols.get("worse", "👎")
    else:
        return symbols.get("better", "👍") if diff < 0 else symbols.get("worse", "👎")


def fmt_num(x: Any, prec: int) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        val = round(float(x), prec)
        s = f"{val:.{prec}f}".rstrip("0").rstrip(".")
        return s
    except Exception:
        return str(x)


# ============================== Compare per experiment ==============================


def compute_experiment_rows(
    exp_name: str,
    base_df: pd.DataFrame,
    prop_df: pd.DataFrame,
    base_id_col: Optional[str],
    prop_id_col: Optional[str],
    metrics: list[MetricSpec],
) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """
    For one experiment name, extract baseline/proposed values for each metric
    (after transforms), returning dict: metric.output_name -> (baseline_value, proposed_value)
    """
    if base_id_col is None:
        base_id_col = find_id_column(base_df)
    if prop_id_col is None:
        prop_id_col = find_id_column(prop_df)

    if base_id_col is None or prop_id_col is None:
        raise ValueError(
            "Could not infer identifier columns. Consider adding 'id_column' to the file entries."
        )

    base_row = find_row_by_name(base_df, base_id_col, exp_name, allow_suffix=True)
    prop_row = find_row_by_name(prop_df, prop_id_col, exp_name, allow_suffix=True)

    if base_row is None or prop_row is None:
        values = {}
        for m in metrics:
            values[m.output_name] = (None, None)
        return values

    values: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for m in metrics:
        b_series = pd.Series([base_row.get(m.baseline_col, pd.NA)])
        b_series = apply_transforms(b_series, m.baseline_transforms)
        b_val = b_series.iloc[0]

        p_series = pd.Series([prop_row.get(m.proposed_col, pd.NA)])
        p_series = apply_transforms(p_series, m.proposed_transforms)
        p_val = p_series.iloc[0]

        values[m.output_name] = (
            None if pd.isna(b_val) else float(b_val),
            None if pd.isna(p_val) else float(p_val),
        )

    return values


# ============================== Markdown (tables) ==============================


def render_markdown_for_experiment(
    exp_display_name: str,
    metric_values: Dict[str, Tuple[Optional[float], Optional[float]]],
    metric_specs: Dict[str, MetricSpec],
    float_precision: int,
    include_units_in_headers: bool,
    show_deltas: bool,
    show_pass_fail: bool,
    symbols: Dict[str, str],
    baseline_label: str = "Baseline",
    proposed_label: str = "Proposed",
) -> str:
    headers = ["Experiment", "Metric", baseline_label, proposed_label]
    if show_deltas:
        headers.append("Δ")

    lines = []

    first = True

    for key, ms in metric_specs.items():
        oname = ms.output_name
        b, p = metric_values.get(oname, (None, None))
        delta = (p - b) if (b is not None and p is not None) else None

        header = oname
        if include_units_in_headers:
            units = metric_specs[key].units
            header += f" ({units})" if units else ""

        sym = result_symbol(b, p, ms.higher_is_better, ms.tolerance, symbols)

        row = [
            exp_display_name if first else "",
            header,
            fmt_num(b, float_precision),
            fmt_num(p, float_precision),
        ]
        first = False
        if show_deltas:
            row.append(fmt_num(delta, float_precision))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    return "\n".join(lines)


def render_markdown(
    cfg: dict,
    filespecs: Dict[str, FileSpec],
    metric_specs: Dict[str, MetricSpec],
    baseline_label: str = "Baseline",
    proposed_label: str = "Proposed",
) -> str:
    tbl_cfg = cfg.get("output", {}).get("table", {})
    float_precision = int(tbl_cfg.get("float_precision", 2))
    include_units_in_headers = bool(tbl_cfg.get("include_units_in_headers", True))
    show_deltas = bool(tbl_cfg.get("show_deltas", True))
    show_pass_fail = bool(tbl_cfg.get("show_pass_fail", True))
    symbols = tbl_cfg.get(
        "pass_fail_symbols", {"pass": "✅", "better": "👍", "worse": "👎", "na": "—"}
    )

    headers = ["Experiment", "Metric", baseline_label, proposed_label, "Change"]
    md_parts: List[str] = [
        "| " + " | ".join(headers) + " |\n",
        "| " + " | ".join(["---"] * len(headers)) + " |\n",
    ]
    for exp in cfg.get("experiments", []):
        name = exp["name"]
        base_key = exp["files"]["baseline"]
        prop_key = exp["files"]["proposed"]
        used_metric_keys = exp["metrics"]

        ms_subset = {k: metric_specs[k] for k in used_metric_keys}

        base_fs = filespecs[base_key]
        prop_fs = filespecs[prop_key]
        base_df = read_csv_filespec(base_fs)
        prop_df = read_csv_filespec(prop_fs)

        vals = compute_experiment_rows(
            exp_name=name,
            base_df=base_df,
            prop_df=prop_df,
            base_id_col=base_fs.id_column,
            prop_id_col=prop_fs.id_column,
            metrics=list(ms_subset.values()),
        )

        md_parts.append(
            render_markdown_for_experiment(
                exp_display_name=name,
                metric_values=vals,
                metric_specs=ms_subset,
                float_precision=float_precision,
                include_units_in_headers=include_units_in_headers,
                show_deltas=show_deltas,
                show_pass_fail=show_pass_fail,
                symbols=symbols,
                baseline_label=baseline_label,
                proposed_label=proposed_label,
            )
        )

    return "".join(md_parts).strip() + "\n"


# ============================== Plots (small bar plots) ==============================


def _plot_opts(cfg: dict) -> dict:
    p = cfg.get("output", {}).get("plot", {}) if isinstance(cfg, dict) else {}
    return {
        "dir": p.get("dir", "plots"),
        "dpi": int(p.get("dpi", 160)),
        "figsize": tuple(p.get("figsize", (12, 8))),
        "grid_cols": int(p.get("grid_cols", 4)),
        "bar_width": float(p.get("bar_width", 0.8)),
        "annotate_delta": bool(p.get("annotate_delta", True)),
        "annotate_symbol": bool(p.get("annotate_symbol", True)),
        "yaxis_include_zero": bool(p.get("yaxis_include_zero", False)),
        "y_padding_ratio": float(p.get("y_padding_ratio", 0.08)),
        "title_prefix_with_symbol": bool(p.get("title_prefix_with_symbol", True)),
        "colors": {
            "baseline": p.get("colors", {}).get("baseline", "#9aa0a6"),
            "proposed": p.get("colors", {}).get("proposed", "#1a73e8"),
        },
        "symbols": cfg.get("output", {})
        .get("table", {})
        .get(
            "pass_fail_symbols",
            {"pass": "✅", "better": "👍", "worse": "👎", "na": "—"},
        ),
    }


def render_plots(
    cfg: dict, filespecs: Dict[str, FileSpec], metric_specs: Dict[str, MetricSpec]
) -> None:
    plot_opts = _plot_opts(cfg)
    outdir = Path(plot_opts["dir"])
    outdir.mkdir(parents=True, exist_ok=True)

    exps = cfg.get("experiments", [])
    if not exps:
        print("No experiments to plot.")
        return

    loaded: Dict[str, pd.DataFrame] = {}

    def get_df(fskey: str) -> pd.DataFrame:
        if fskey not in loaded:
            loaded[fskey] = read_csv_filespec(filespecs[fskey])
        return loaded[fskey]

    for metric_key, ms in metric_specs.items():
        rows = []
        for exp in exps:
            if metric_key not in exp["metrics"]:
                continue
            name = exp["name"]
            base_fs = filespecs[exp["files"]["baseline"]]
            prop_fs = filespecs[exp["files"]["proposed"]]
            base_df = get_df(base_fs.key)
            prop_df = get_df(prop_fs.key)
            vals = compute_experiment_rows(
                exp_name=name,
                base_df=base_df,
                prop_df=prop_df,
                base_id_col=base_fs.id_column,
                prop_id_col=prop_fs.id_column,
                metrics=[ms],
            )
            b, p = vals[ms.output_name]
            rows.append((name, b, p))

        if not rows:
            continue

        n = len(rows)
        cols = plot_opts["grid_cols"]
        rows_n = math.ceil(n / cols)
        units = f"({ms.units})" if ms.units else ""

        fig, axes = plt.subplots(
            rows_n, cols, figsize=plot_opts["figsize"], squeeze=False
        )
        fig.suptitle(f"{ms.output_name} {units}", fontsize=14)

        for idx, (exp_name, b, p) in enumerate(rows):
            r = idx // cols
            c = idx % cols
            ax = axes[r][c]

            if (b is None or pd.isna(b)) and (p is None or pd.isna(p)):
                ax.axis("off")
                continue

            x = [0, 1]
            vals = [
                float(b) if b is not None and not pd.isna(b) else float("nan"),
                float(p) if p is not None and not pd.isna(p) else float("nan"),
            ]
            ax.bar(
                [x[0]],
                [vals[0]],
                width=plot_opts["bar_width"],
                color=plot_opts["colors"]["baseline"],
                label="baseline",
            )
            ax.bar(
                [x[1]],
                [vals[1]],
                width=plot_opts["bar_width"],
                color=plot_opts["colors"]["proposed"],
                label="proposed",
            )

            finite_vals = [v for v in vals if math.isfinite(v) and not math.isnan(v)]
            if finite_vals:
                vmin = min(finite_vals)
                vmax = max(finite_vals)
                if plot_opts["yaxis_include_zero"]:
                    vmin = min(0.0, vmin)
                    vmax = max(0.0, vmax)
                pad = max(1e-9, (vmax - vmin) * plot_opts["y_padding_ratio"])
                if vmin == vmax:
                    bump = 0.5 if abs(vmin) < 1e-9 else abs(vmin) * 0.05
                    vmin -= bump
                    vmax += bump
                ax.set_ylim(vmin - pad, vmax + pad)

            ax.set_xticks(x, ["B", "P"])
            ax.tick_params(axis="x", labelsize=8)
            ax.tick_params(axis="y", labelsize=8)
            for spine in ["top", "right"]:
                ax.spines[spine].set_visible(False)

            sym = result_symbol(
                b, p, ms.higher_is_better, ms.tolerance, plot_opts["symbols"]
            )
            title = (
                f"{sym} {exp_name}"
                if (
                    plot_opts["title_prefix_with_symbol"]
                    and sym
                    and sym
                    not in (
                        plot_opts["symbols"].get("pass"),
                        plot_opts["symbols"].get("na"),
                    )
                )
                else exp_name
            )
            ax.set_title(title, fontsize=9)

            if plot_opts["annotate_delta"] and (
                b is not None and p is not None and not (pd.isna(b) or pd.isna(p))
            ):
                delta = vals[1] - vals[0]
                ref = max(vals[0], vals[1])
                ax.text(
                    0.5, ref, f"Δ={delta:.2f}", ha="center", va="bottom", fontsize=8
                )

            if plot_opts["annotate_symbol"]:
                if sym:
                    ax.text(
                        0.98,
                        0.98,
                        sym,
                        transform=ax.transAxes,
                        ha="right",
                        va="top",
                        fontsize=10,
                    )

        total_axes = rows_n * cols
        for j in range(n, total_axes):
            r = j // cols
            c = j % cols
            axes[r][c].axis("off")

        handles = [
            plt.Rectangle(
                (0, 0), 1, 1, color=plot_opts["colors"]["baseline"], label="baseline"
            ),
            plt.Rectangle(
                (0, 0), 1, 1, color=plot_opts["colors"]["proposed"], label="proposed"
            ),
        ]
        fig.legend(handles=handles, loc="upper right")
        fig.tight_layout(rect=[0, 0, 0.98, 0.95])

        out_path = Path(plot_opts["dir"]) / f"{ms.output_name}.png"
        fig.savefig(out_path, dpi=plot_opts["dpi"])
        plt.close(fig)
        print(f"Saved: {out_path}")


# ============================== Modify-in-place helpers ==============================

_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _infer_decimals_from_baseline_cell(cell: Any) -> Optional[int]:
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return None
    s = str(cell)
    m = _NUM_RE.search(s)
    if not m:
        return None
    num = m.group(0)
    if "." in num:
        return len(num.split(".", 1)[1])
    return 0


def _decimals_from_round_transform(transforms: list[dict]) -> Optional[int]:
    for t in transforms:
        if (t.get("kind") or "").strip() == "round":
            return int(t.get("decimals", 0))
    return None


def format_value_like_baseline(
    baseline_cell: Any,
    new_value: float,
    *,
    fallback_decimals: int,
) -> str:
    """
    Replace the numeric part of baseline_cell with new_value while preserving
    surrounding formatting (for example '%' suffix and existing decimals).
    """
    if baseline_cell is None or (
        isinstance(baseline_cell, float) and pd.isna(baseline_cell)
    ):
        s = ""
    else:
        s = str(baseline_cell)

    dec = _infer_decimals_from_baseline_cell(s)
    if dec is None:
        dec = fallback_decimals

    wants_percent = "%" in s

    num_str = f"{float(new_value):.{dec}f}"
    if wants_percent:
        num_with_percent = f"{num_str}%"
    else:
        num_with_percent = num_str

    m = _NUM_RE.search(s)
    if not m:
        return num_with_percent

    prefix = s[: m.start()]
    suffix = s[m.end() :]

    if "%" in (prefix + suffix) and wants_percent:
        return f"{prefix}{num_str}{suffix}"

    return f"{prefix}{num_with_percent}{suffix}"


def update_baseline_df_in_place(
    *,
    exp_name: str,
    base_df: pd.DataFrame,
    prop_df: pd.DataFrame,
    base_id_col: str,
    prop_id_col: str,
    metrics: list[MetricSpec],
) -> list[tuple[str, Any, Any]]:
    """
    Write proposed values into baseline columns for a single experiment.

    Returns:
        A list of (column_name, old_value, new_value) for changes applied.
    """
    base_row = find_row_by_name(base_df, base_id_col, exp_name, allow_suffix=True)
    prop_row = find_row_by_name(prop_df, prop_id_col, exp_name, allow_suffix=True)

    if base_row is None or prop_row is None:
        return []

    base_idx = base_row.name
    changes: list[tuple[str, Any, Any]] = []

    for m in metrics:
        if m.baseline_col not in base_df.columns:
            continue

        p_series = pd.Series([prop_row.get(m.proposed_col, pd.NA)])
        p_series = apply_transforms(p_series, m.proposed_transforms)
        p_val = p_series.iloc[0]
        if pd.isna(p_val):
            continue

        old_cell = base_df.at[base_idx, m.baseline_col]

        dec_from_transform = _decimals_from_round_transform(m.baseline_transforms)
        fallback_decimals = 2 if dec_from_transform is None else dec_from_transform

        new_cell = format_value_like_baseline(
            old_cell, float(p_val), fallback_decimals=fallback_decimals
        )

        if str(old_cell) != str(new_cell):
            base_df.at[base_idx, m.baseline_col] = new_cell
            changes.append((m.baseline_col, old_cell, new_cell))

    return changes


# ============================== CLI ==============================


def classify_input(p: Path) -> str:
    """Return 'repo' if directory, 'wandb' if .csv file, else raise."""
    if p.is_dir():
        return "repo"
    if p.is_file() and p.suffix.lower() == ".csv":
        return "wandb"
    raise ValueError(
        f"Cannot classify '{p}'. Provide a folder (repo) or a .csv file (wandb)."
    )


def choose_config_name(b_kind: str, p_kind: str) -> str:
    mapping = {
        ("repo", "repo"): "configs_repo_repo.yaml",
        ("repo", "wandb"): "configs_repo_wandb.yaml",
        ("wandb", "repo"): "configs_wandb_repo.yaml",
        ("wandb", "wandb"): "configs_wandb_wandb.yaml",
    }
    return mapping[(b_kind, p_kind)]


def resolve_config_path(
    config_override: Optional[Path], config_name: str, configs_dir: Optional[Path]
) -> Path:
    if config_override:
        return config_override
    if configs_dir:
        return configs_dir / config_name
    return Path(config_name)


def _write_csv_with_backup(
    df: pd.DataFrame,
    path: Path,
    *,
    encoding: str,
    backup_suffix: Optional[str],
) -> None:
    if backup_suffix:
        backup_path = path.with_suffix(path.suffix + backup_suffix)
        backup_path.write_text(path.read_text(encoding=encoding), encoding=encoding)
    df.to_csv(path, index=False, encoding=encoding)


def _modify_in_place(
    cfg: dict,
    filespecs: Dict[str, FileSpec],
    metric_specs: Dict[str, MetricSpec],
    *,
    dry_run: bool,
    backup_suffix: Optional[str],
) -> None:
    """
    Update baseline repo CSVs in-place by writing proposed values into baseline columns.

    Assumptions:
      - Baseline file specs point to repo CSVs (writable).
      - Proposed file specs can be repo or wandb CSVs.
    """
    exps = cfg.get("experiments", [])
    if not exps:
        print("No experiments to modify.")
        return

    df_cache: dict[str, pd.DataFrame] = {}

    def get_df(fskey: str) -> pd.DataFrame:
        if fskey not in df_cache:
            df_cache[fskey] = read_csv_filespec(filespecs[fskey])
        return df_cache[fskey]

    baseline_keys = sorted({e["files"]["baseline"] for e in exps})

    for base_key in baseline_keys:
        base_fs = filespecs[base_key]
        base_path = Path(base_fs.path)

        if not base_path.exists():
            raise FileNotFoundError(f"Baseline CSV not found: {base_fs.path}")

        base_df = get_df(base_key)
        base_id_col = find_id_column(base_df, base_fs.id_column)
        if base_id_col is None:
            raise ValueError(
                f"Could not infer id column for baseline file: {base_fs.path}. "
                "Consider adding 'id_column' to the file entry."
            )

        all_changes_for_file: list[tuple[str, list[tuple[str, Any, Any]]]] = []

        for exp in exps:
            if exp["files"]["baseline"] != base_key:
                continue

            prop_key = exp["files"]["proposed"]
            prop_fs = filespecs[prop_key]
            prop_path = Path(prop_fs.path)

            if not prop_path.exists():
                raise FileNotFoundError(f"Proposed CSV not found: {prop_fs.path}")

            prop_df = get_df(prop_key)
            prop_id_col = find_id_column(prop_df, prop_fs.id_column)
            if prop_id_col is None:
                raise ValueError(
                    f"Could not infer id column for proposed file: {prop_fs.path}. "
                    "Consider adding 'id_column' to the file entry."
                )

            ms_subset = [metric_specs[k] for k in exp["metrics"]]
            changes = update_baseline_df_in_place(
                exp_name=exp["name"],
                base_df=base_df,
                prop_df=prop_df,
                base_id_col=base_id_col,
                prop_id_col=prop_id_col,
                metrics=ms_subset,
            )
            if changes:
                all_changes_for_file.append((exp["name"], changes))

        if not all_changes_for_file:
            continue

        print(f"\n=== Modify: {base_path} ===")
        for exp_name, changes in all_changes_for_file:
            for col, old, new in changes:
                print(f"{exp_name} | {col} | {old} -> {new}")

        if dry_run:
            continue

        _write_csv_with_backup(
            base_df, base_path, encoding=base_fs.encoding, backup_suffix=backup_suffix
        )
        print(f"Wrote updated baseline CSV: {base_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Compare benchmark runs using YAML configs."
    )
    ap.add_argument(
        "--baseline",
        required=True,
        type=Path,
        help="Baseline selection: a folder (repo) or a .csv file (wandb).",
    )
    ap.add_argument(
        "--proposed",
        required=True,
        type=Path,
        help="Proposed selection: a folder (repo) or a .csv file (wandb).",
    )
    ap.add_argument(
        "--baseline-label",
        type=str,
        default="Baseline",
        help="Header label for the Baseline column in markdown tables.",
    )
    ap.add_argument(
        "--proposed-label",
        type=str,
        default="Proposed",
        help="Header label for the Proposed column in markdown tables.",
    )
    ap.add_argument(
        "--config",
        type=Path,
        help="Optional explicit config YAML path. Overrides automatic selection.",
    )
    ap.add_argument(
        "--configs-dir",
        type=Path,
        default=Path("configs"),
        help="Directory containing the 4 template configs.",
    )
    ap.add_argument(
        "--mode", choices=["table", "plot"], help="Override output.mode from YAML."
    )
    ap.add_argument(
        "--out", type=Path, help="If mode=table, optional Markdown output path."
    )

    ap.add_argument(
        "--modify-in-place",
        action="store_true",
        help="Write proposed values into baseline repo CSVs in-place.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="With --modify-in-place, show changes but do not write files.",
    )
    ap.add_argument(
        "--backup-suffix",
        type=str,
        default="",
        help="With --modify-in-place, create a backup next to each modified CSV. ",
    )

    args = ap.parse_args()

    b_kind = classify_input(args.baseline)
    p_kind = classify_input(args.proposed)

    config_name = choose_config_name(b_kind, p_kind)
    cfg_path = resolve_config_path(args.config, config_name, args.configs_dir)

    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    mapping = cli_var_mapping(b_kind, p_kind, args.baseline, args.proposed)
    cfg = expand_vars_in_cfg(cfg, mapping)

    filespecs = to_filespecs(cfg)
    metric_specs = load_metric_specs(cfg)

    if args.modify_in_place:
        if b_kind != "repo":
            raise ValueError(
                "--modify-in-place requires --baseline to be a repo folder."
            )

        backup_suffix = args.backup_suffix if args.backup_suffix else None
        _modify_in_place(
            cfg,
            filespecs,
            metric_specs,
            dry_run=args.dry_run,
            backup_suffix=backup_suffix,
        )
        return

    mode = args.mode or get_nested(cfg, ["output", "mode"], "table")

    if mode == "table":
        md = render_markdown(
            cfg,
            filespecs,
            metric_specs,
            baseline_label=args.baseline_label,
            proposed_label=args.proposed_label,
        )
        if args.out:
            args.out.write_text(md, encoding="utf-8")
            print(f"Wrote Markdown to {args.out}")
        else:
            print(md)
    elif mode == "plot":
        render_plots(cfg, filespecs, metric_specs)


if __name__ == "__main__":
    main()
