from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "experiments" / "catalog_hybrid.db"
DEFAULT_AUDIT = (
    PROJECT_ROOT / "data" / "reports" / "amazon_catalog_quality_dry_run_v1.json"
)
DEFAULT_OVERRIDES = (
    PROJECT_ROOT / "data" / "curated" / "amazon_quality_review_overrides_v1.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data" / "experiments" / "catalog_hybrid_clean_v1.db"
)
DEFAULT_DECISIONS = (
    PROJECT_ROOT / "data" / "curated" / "amazon_quality_decisions_v1.jsonl"
)
DEFAULT_REPORT = (
    PROJECT_ROOT / "data" / "reports" / "catalog_hybrid_clean_v1.json"
)
ALLOWED_DECISIONS = {"KEEP", "EXCLUDE_NON_FASHION", "RECLASSIFY"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an isolated reviewed hybrid catalog without changing row IDs."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_reviewed_decisions(
    audit_path: Path,
    overrides_path: Path,
) -> tuple[str, list[dict[str, object]]]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    overrides_payload = json.loads(overrides_path.read_text(encoding="utf-8"))
    review_version = str(overrides_payload["review_version"])
    overrides = {
        str(item["product_id"]): item for item in overrides_payload["decisions"]
    }
    if len(overrides) != len(overrides_payload["decisions"]):
        raise ValueError("duplicate product IDs in review overrides")

    candidate_ids = {str(item["product_id"]) for item in audit["candidates"]}
    unknown_overrides = sorted(set(overrides) - candidate_ids)
    if unknown_overrides:
        raise ValueError(f"review overrides not present in audit: {unknown_overrides}")

    decisions: list[dict[str, object]] = []
    for candidate in audit["candidates"]:
        product_id = str(candidate["product_id"])
        override = overrides.get(product_id)
        decision = (
            str(override["decision"])
            if override is not None
            else str(candidate["proposed_action"])
        )
        if decision not in ALLOWED_DECISIONS:
            raise ValueError(f"unsupported decision for {product_id}: {decision}")
        proposed_category = (
            str(candidate["proposed_category"])
            if candidate["proposed_category"] is not None
            else None
        )
        if decision == "RECLASSIFY" and proposed_category is None:
            raise ValueError(f"reclassification missing category for {product_id}")
        decisions.append(
            {
                **candidate,
                "decision": decision,
                "review_reason": (
                    str(override["reason"])
                    if override is not None
                    else "人工复核原标题与原始分类路径后接受审计建议"
                ),
                "review_version": review_version,
            }
        )
    return review_version, decisions


def build_clean_catalog(
    *,
    source_path: Path,
    audit_path: Path,
    overrides_path: Path,
    output_path: Path,
    decisions_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    for path in (output_path, decisions_path, report_path):
        if path.exists():
            raise FileExistsError(path)
    review_version, decisions = load_reviewed_decisions(audit_path, overrides_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, output_path)

    reviewed_at = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(output_path)
    connection.execute("PRAGMA foreign_keys=ON")
    source_count = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
    product_ids = [str(item["product_id"]) for item in decisions]
    matching_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM products WHERE parent_asin IN ({})".format(
                ",".join("?" for _ in product_ids)
            ),
            product_ids,
        ).fetchone()[0]
    )
    if matching_count != len(product_ids):
        raise ValueError(
            f"reviewed products missing from source database: {matching_count}/{len(product_ids)}"
        )

    exclusions = [item for item in decisions if item["decision"] == "EXCLUDE_NON_FASHION"]
    reclassifications = [item for item in decisions if item["decision"] == "RECLASSIFY"]
    with connection:
        connection.executemany(
            """
            INSERT INTO product_moderation(
                product_id, audit_version, status, reason, confidence, audited_at
            ) VALUES (?, ?, 'FAIL', ?, 1.0, ?)
            """,
            [
                (
                    str(item["product_id"]),
                    review_version,
                    f"{item['rule_id']}: {item['evidence']}",
                    reviewed_at,
                )
                for item in exclusions
            ],
        )
        connection.executemany(
            """
            UPDATE products
            SET category_norm = ?, category_source = ?
            WHERE parent_asin = ? AND category_norm = ?
            """,
            [
                (
                    str(item["proposed_category"]),
                    review_version,
                    str(item["product_id"]),
                    str(item["current_category"]),
                )
                for item in reclassifications
            ],
        )
        changed_categories = connection.total_changes - len(exclusions)
        if changed_categories != len(reclassifications):
            raise RuntimeError(
                "reclassified product count mismatch: "
                f"{changed_categories} != {len(reclassifications)}"
            )
        metadata = {
            "quality_review_version": review_version,
            "quality_excluded_products": len(exclusions),
            "quality_reclassified_products": len(reclassifications),
        }
        connection.executemany(
            """
            INSERT INTO catalog_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            [
                (key, json.dumps(value, ensure_ascii=False))
                for key, value in metadata.items()
            ],
        )

    inactive_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM product_moderation WHERE status='FAIL'"
        ).fetchone()[0]
    )
    integrity_check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    connection.close()
    if inactive_count != len(exclusions):
        raise RuntimeError(
            f"inactive product count mismatch: {inactive_count} != {len(exclusions)}"
        )
    if integrity_check != "ok":
        raise RuntimeError(f"database integrity check failed: {integrity_check}")

    with decisions_path.open("x", encoding="utf-8", newline="\n") as target:
        for item in decisions:
            target.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    decision_counts = Counter(str(item["decision"]) for item in decisions)
    report = {
        "created_at": reviewed_at,
        "source_database": str(source_path),
        "source_database_sha256": sha256(source_path),
        "output_database": str(output_path),
        "output_database_sha256": sha256(output_path),
        "review_version": review_version,
        "source_products": source_count,
        "output_products": source_count,
        "active_products": source_count - inactive_count,
        "decision_counts": dict(sorted(decision_counts.items())),
        "row_ids_preserved": True,
        "physical_deletions": 0,
        "integrity_check": integrity_check,
        "decisions": str(decisions_path),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    args = parse_args()
    report = build_clean_catalog(
        source_path=args.source,
        audit_path=args.audit,
        overrides_path=args.overrides,
        output_path=args.output,
        decisions_path=args.decisions,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
