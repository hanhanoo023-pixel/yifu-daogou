from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend.c_recommender.semantic import ProjectedE5Encoder, product_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build catalog-variant embeddings by reusing unchanged Amazon vectors."
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--source-embeddings", type=Path, required=True)
    parser.add_argument("--source-product-ids", type=Path, required=True)
    parser.add_argument("--source-metadata", type=Path, required=True)
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
    if args.batch_size < 1:
        raise ValueError("batch-size must be positive")

    source_metadata = json.loads(args.source_metadata.read_text(encoding="utf-8"))
    for path, key in (
        (args.source_embeddings, "embeddings_sha256"),
        (args.source_product_ids, "product_ids_sha256"),
        (args.projection, "projection_sha256"),
    ):
        actual = sha256(path)
        if actual != source_metadata[key]:
            raise ValueError(f"{path} SHA-256 mismatch: {actual} != {source_metadata[key]}")

    source_ids = args.source_product_ids.read_text(encoding="utf-8").splitlines()
    source_row_by_id = {product_id: row for row, product_id in enumerate(source_ids)}
    source_embeddings = np.load(args.source_embeddings, mmap_mode="r")
    if source_embeddings.shape[0] != len(source_ids):
        raise ValueError("source embedding rows and product IDs do not match")

    connection = sqlite3.connect(f"file:{args.database}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    count, minimum_id, maximum_id = connection.execute(
        "SELECT COUNT(*), MIN(id), MAX(id) FROM products"
    ).fetchone()
    if (minimum_id, maximum_id) != (1, count):
        raise ValueError(
            f"variant database IDs are not contiguous: count={count}, "
            f"minimum={minimum_id}, maximum={maximum_id}"
        )
    rows = connection.execute(
        """
        SELECT id, parent_asin, title, category_norm, brand, color_raw, material,
               features_text, description_text
        FROM products
        ORDER BY id
        """
    ).fetchall()
    target_ids = [str(row["parent_asin"]) for row in rows]
    missing = [product_id for product_id in target_ids if product_id not in source_row_by_id]
    if missing:
        raise ValueError(f"variant products missing from source embeddings: {missing[:10]}")

    output_dim = int(source_embeddings.shape[1])
    embeddings = np.lib.format.open_memmap(
        args.embeddings,
        mode="w+",
        dtype=np.float32,
        shape=(count, output_dim),
    )
    refreshed_rows = [row for row in rows if str(row["parent_asin"]).startswith("F200K_")]
    reused_rows = [row for row in rows if not str(row["parent_asin"]).startswith("F200K_")]
    for start in range(0, len(reused_rows), 10_000):
        batch = reused_rows[start : start + 10_000]
        source_indices = np.asarray(
            [source_row_by_id[str(row["parent_asin"])] for row in batch],
            dtype=np.int64,
        )
        target_indices = np.asarray([int(row["id"]) - 1 for row in batch], dtype=np.int64)
        embeddings[target_indices] = source_embeddings[source_indices]

    started = time.perf_counter()
    if refreshed_rows:
        encoder = ProjectedE5Encoder(args.base_model, args.projection, args.device)
        if encoder.output_dim != output_dim:
            raise ValueError(
                f"encoder output dimension {encoder.output_dim} != source {output_dim}"
            )
        for start in range(0, len(refreshed_rows), args.batch_size):
            batch = refreshed_rows[start : start + args.batch_size]
            vectors = encoder.encode_items(
                [product_text(dict(row)) for row in batch],
                batch_size=args.batch_size,
            )
            target_indices = np.asarray(
                [int(row["id"]) - 1 for row in batch], dtype=np.int64
            )
            embeddings[target_indices] = vectors
            completed = min(start + args.batch_size, len(refreshed_rows))
            if completed % (args.batch_size * 10) == 0 or completed == len(
                refreshed_rows
            ):
                print(
                    json.dumps(
                        {
                            "refreshed": completed,
                            "refresh_total": len(refreshed_rows),
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                        }
                    ),
                    flush=True,
                )
    embeddings.flush()
    args.product_ids.write_text("\n".join(target_ids) + "\n", encoding="utf-8")
    connection.close()

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(args.database),
        "database_sha256": sha256(args.database),
        "projection": str(args.projection),
        "projection_sha256": sha256(args.projection),
        "embeddings_sha256": sha256(args.embeddings),
        "product_ids_sha256": sha256(args.product_ids),
        "base_model": str(args.base_model),
        "records": count,
        "shape": [count, output_dim],
        "dtype": "float32",
        "ordering": "products.id ascending; embedding_row = products.id - 1",
        "batch_size": args.batch_size,
        "device": args.device,
        "source_embeddings": str(args.source_embeddings),
        "source_embeddings_sha256": sha256(args.source_embeddings),
        "reused_vectors": len(reused_rows),
        "refreshed_vectors": len(refreshed_rows),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "active_records": count,
        "moderated_inactive_records": 0,
    }
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
