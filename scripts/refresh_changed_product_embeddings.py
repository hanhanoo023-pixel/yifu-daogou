from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.c_recommender.semantic import ProjectedE5Encoder, product_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--previous-database", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--old-embeddings", type=Path, required=True)
    parser.add_argument("--old-product-ids", type=Path, required=True)
    parser.add_argument("--old-metadata", type=Path, required=True)
    parser.add_argument("--embeddings", type=Path, required=True)
    parser.add_argument("--product-ids", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    for output in (args.embeddings, args.product_ids, args.metadata):
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)

    old_metadata = json.loads(args.old_metadata.read_text(encoding="utf-8"))
    expected_hashes = (
        (args.old_embeddings, "embeddings_sha256"),
        (args.old_product_ids, "product_ids_sha256"),
        (args.projection, "projection_sha256"),
    )
    for path, key in expected_hashes:
        actual = sha256(path)
        if actual != old_metadata[key]:
            raise ValueError(f"{path} SHA-256 mismatch: {actual} != {old_metadata[key]}")

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("ATTACH DATABASE ? AS previous", (str(args.previous_database),))
    count, minimum_id, maximum_id = connection.execute(
        "SELECT COUNT(*), MIN(id), MAX(id) FROM main.products"
    ).fetchone()
    previous_count, previous_minimum_id, previous_maximum_id = connection.execute(
        "SELECT COUNT(*), MIN(id), MAX(id) FROM previous.products"
    ).fetchone()
    if (count, minimum_id, maximum_id) != (
        previous_count,
        previous_minimum_id,
        previous_maximum_id,
    ):
        raise RuntimeError("current and previous databases do not have identical ID ranges")

    old_ids = args.old_product_ids.read_text(encoding="utf-8").splitlines()
    database_ids = [
        str(row[0])
        for row in connection.execute("SELECT parent_asin FROM main.products ORDER BY id")
    ]
    if old_ids != database_ids:
        raise RuntimeError("product IDs changed; an incremental vector refresh is unsafe")

    changed_rows = connection.execute(
        """
        SELECT current.id, current.parent_asin, current.title, current.category_norm,
               current.brand, current.color_raw, current.material,
               current.features_text, current.description_text
        FROM main.products AS current
        JOIN previous.products AS old ON old.id = current.id
        WHERE current.category_norm != old.category_norm
        ORDER BY current.id
        """
    ).fetchall()
    inactive_records = int(
        connection.execute(
            "SELECT COUNT(*) FROM main.product_moderation WHERE status='FAIL'"
        ).fetchone()[0]
    )
    old_embeddings = np.load(args.old_embeddings, mmap_mode="r")
    expected_shape = (count, int(old_metadata["shape"][1]))
    if old_embeddings.shape != expected_shape:
        raise RuntimeError(f"unexpected old embedding shape: {old_embeddings.shape}")

    encoder = ProjectedE5Encoder(args.base_model, args.projection, args.device)
    embeddings = np.lib.format.open_memmap(
        args.embeddings,
        mode="w+",
        dtype=np.float32,
        shape=expected_shape,
    )
    embeddings[:] = old_embeddings

    started = time.perf_counter()
    completed = 0
    for start in range(0, len(changed_rows), args.batch_size):
        batch = changed_rows[start : start + args.batch_size]
        texts = [product_text(dict(row)) for row in batch]
        vectors = encoder.encode_items(texts, batch_size=args.batch_size)
        indices = np.asarray([int(row["id"]) - 1 for row in batch], dtype=np.int64)
        embeddings[indices] = vectors
        completed += len(batch)
        if completed % (args.batch_size * 10) == 0 or completed == len(changed_rows):
            print(
                json.dumps(
                    {
                        "completed": completed,
                        "total": len(changed_rows),
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                ),
                flush=True,
            )
    embeddings.flush()
    args.product_ids.write_text("\n".join(database_ids) + "\n", encoding="utf-8")
    connection.close()

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(args.database),
        "database_sha256": sha256(args.database),
        "previous_database": str(args.previous_database),
        "previous_database_sha256": sha256(args.previous_database),
        "projection": str(args.projection),
        "projection_sha256": sha256(args.projection),
        "embeddings_sha256": sha256(args.embeddings),
        "product_ids_sha256": sha256(args.product_ids),
        "base_model": str(args.base_model),
        "records": count,
        "shape": list(expected_shape),
        "dtype": "float32",
        "ordering": "products.id ascending; embedding_row = products.id - 1",
        "batch_size": args.batch_size,
        "device": args.device,
        "refresh_reason": "reviewed_hybrid_catalog_v1",
        "refreshed_vectors": len(changed_rows),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "active_records": count - inactive_records,
        "moderated_inactive_records": inactive_records,
    }
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
