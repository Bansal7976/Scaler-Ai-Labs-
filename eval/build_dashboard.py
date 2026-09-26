"""Render ``evaluation_dashboard.html`` from the metrics and the run summary.

The page is a single self-contained file that opens straight from disk: the
numbers are injected as JSON rather than hand-typed, so the dashboard and
``EVALUATION_REPORT.md`` can never disagree with ``eval/results/metrics.json``.

Run after ``eval/evaluate.py``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = HERE / "dashboard_template.html"
METRICS = HERE / "results" / "metrics.json"
RUN_SUMMARY = ROOT / "output" / "run_summary.json"
MAPPING = ROOT / "output" / "mapping.csv"
OUTPUT = ROOT / "evaluation_dashboard.html"

#: Rows per PII type kept in the page. The full table ships as mapping.csv; the
#: page only needs enough to show that the surrogates are consistent and
#: format-preserving.
ROWS_PER_TYPE = 14


def build_payload() -> dict:
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    run = json.loads(RUN_SUMMARY.read_text(encoding="utf-8"))

    by_type: dict[str, list[dict]] = {}
    with MAPPING.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            by_type.setdefault(row["pii_type"], []).append(row)

    sample: list[dict] = []
    for pii_type, rows in sorted(by_type.items()):
        # Longest originals first: they show format preservation most clearly.
        rows = sorted(rows, key=lambda r: -len(r["original"]))
        sample.extend(rows[:ROWS_PER_TYPE])

    keep = (
        "segments",
        "gold_spans",
        "predicted_spans",
        "strict",
        "relaxed",
        "character_level",
        "segment_level",
    )
    return {
        "config": metrics["config"],
        "document": {
            **{k: metrics["document"][k] for k in keep},
            "by_stratum": metrics["document"]["by_stratum"],
            "errors": metrics["document"]["errors"],
        },
        "synthetic": {k: metrics["synthetic"][k] for k in keep},
        "ablations": metrics["ablations"],
        "run": {
            "input_document": run["input_document"],
            "detection": run["detection"],
            "replacement": run["replacement"],
            "distinct_surrogates": run["distinct_surrogates"],
            "integrity": run["integrity"],
            "timings": run["timings_seconds"],
        },
        "mapping_counts": {t: len(v) for t, v in sorted(by_type.items())},
        "mapping_sample": sample,
    }


def main() -> int:
    payload = build_payload()
    template = TEMPLATE.read_text(encoding="utf-8")
    if "__DATA__" not in template:
        raise SystemExit("template is missing the __DATA__ placeholder")
    # ``</script>`` inside the JSON would close the host script element early.
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    OUTPUT.write_text(template.replace("__DATA__", blob), encoding="utf-8")
    size = OUTPUT.stat().st_size
    print(f"wrote {OUTPUT} ({size / 1024:.0f} KB)")
    print(f"  {len(payload['mapping_sample'])} mapping rows across "
          f"{len(payload['mapping_counts'])} types")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
