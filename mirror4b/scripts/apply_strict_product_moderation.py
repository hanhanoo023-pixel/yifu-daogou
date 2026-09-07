from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--database-backup", type=Path, required=True)
    parser.add_argument("--semantic-metadata", type=Path, required=True)
    parser.add_argument("--metadata-backup", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--audit-version", required=True)
    parser.add_argument("--expected-existing", type=int, required=True)
    parser.add_argument("--expected-failures", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for path in (args.database_backup, args.metadata_backup, args.report):
        if path.exists():
            raise FileExistsError(path)

    failures = [
        json.loads(line)
        for line in args.audit.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["status"] == "FAIL"
    ]
    if len(failures) != args.expected_failures:
        raise RuntimeError(
            f"expected {args.expected_failures} FAIL records, got {len(failures)}"
        )
    product_ids = [str(row["product_id"]) for row in failures]
    if len(product_ids) != len(set(product_ids)):
        raise RuntimeError("duplicate FAIL product IDs")

    connection = sqlite3.connect(args.database)
    existing_moderations = connection.execute(
        "SELECT COUNT(*) FROM product_moderation WHERE status = 'FAIL'"
    ).fetchone()[0]
    matching_products = connection.execute(
        "SELECT COUNT(*) FROM products WHERE parent_asin IN ({})".format(
            ",".join("?" for _ in product_ids)
        ),
        product_ids,
    ).fetchone()[0]
    connection.close()
    if existing_moderations != args.expected_existing:
        raise RuntimeError(
            f"expected {args.expected_existing} existing moderations, "
            f"got {existing_moderations}"
        )
    if matching_products != len(product_ids):
        raise RuntimeError(
            f"expected {len(product_ids)} products in database, found {matching_products}"
        )

    shutil.copy2(args.database, args.database_backup)
    shutil.copy2(args.semantic_metadata, args.metadata_backup)

    audited_at = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(args.database)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executemany(
        """
        INSERT INTO product_moderation (
            product_id, audit_version, status, reason, confidence, audited_at
        ) VALUES (?, ?, 'FAIL', ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            audit_version = excluded.audit_version,
            status = excluded.status,
            reason = excluded.reason,
            confidence = excluded.confidence,
            audited_at = excluded.audited_at
        """,
        [
            (
                str(row["product_id"]),
                args.audit_version,
                str(row["reason"]),
                float(row["confidence"]),
                audited_at,
            )
            for row in failures
        ],
    )
    connection.commit()
    total_products = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    inactive_products = connection.execute(
        "SELECT COUNT(*) FROM product_moderation WHERE status = 'FAIL'"
    ).fetchone()[0]
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    connection.close()
    if inactive_products != args.expected_failures:
        raise RuntimeError(
            f"expected {args.expected_failures} final moderations, got {inactive_products}"
        )
    if integrity != "ok":
        raise RuntimeError(f"database integrity check failed: {integrity}")

    metadata = json.loads(args.semantic_metadata.read_text(encoding="utf-8"))
    metadata["database_sha256"] = sha256(args.database)
    metadata["active_records"] = total_products - inactive_products
    metadata["moderated_inactive_records"] = inactive_products
    args.semantic_metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = {
        "database": str(args.database),
        "database_backup": str(args.database_backup),
        "semantic_metadata": str(args.semantic_metadata),
        "metadata_backup": str(args.metadata_backup),
        "audit": str(args.audit),
        "audit_version": args.audit_version,
        "previously_inactive_products": existing_moderations,
        "total_products": total_products,
        "inactive_products": inactive_products,
        "newly_inactive_products": inactive_products - existing_moderations,
        "active_products": total_products - inactive_products,
        "database_sha256": metadata["database_sha256"],
        "integrity_check": integrity,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
