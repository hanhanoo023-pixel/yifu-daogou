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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    parser.add_argument("--old-records", type=int, required=True)
    parser.add_argument("--expected-records", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for output in (args.embeddings, args.product_ids, args.metadata):
        if output.exists():
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)

    old_metadata = json.loads(args.old_metadata.read_text(encoding="utf-8"))
    expected_old_hashes = (
        (args.previous_database, "database_sha256"),
        (args.old_embeddings, "embeddings_sha256"),
        (args.old_product_ids, "product_ids_sha256"),
        (args.projection, "projection_sha256"),
    )
    for path, key in expected_old_hashes:
        actual = sha256(path)
        if actual != old_metadata[key]:
            raise ValueError(f"{path} SHA-256 mismatch: {actual} != {old_metadata[key]}")

    old_embeddings = np.load(args.old_embeddings, mmap_mode="r")
    if old_embeddings.shape != (args.old_records, int(old_metadata["shape"][1])):
        raise ValueError(f"unexpected old embedding shape: {old_embeddings.shape}")

    connection = sqlite3.connect(f"file:{args.database}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    count, minimum_id, maximum_id = connection.execute(
        "SELECT COUNT(*), MIN(id), MAX(id) FROM products"
    ).fetchone()
    if (count, minimum_id, maximum_id) != (
        args.expected_records,
        1,
        args.expected_records,
    ):
        raise RuntimeError(
            f"expected contiguous ids 1..{args.expected_records}, got count={count}, "
            f"min={minimum_id}, max={maximum_id}"
        )

    old_ids = args.old_product_ids.read_text(encoding="utf-8").splitlines()
    database_old_ids = [
        str(row[0])
        for row in connection.execute(
            "SELECT parent_asin FROM products WHERE id <= ? ORDER BY id",
            (args.old_records,),
        )
    ]
    if old_ids != database_old_ids:
        raise RuntimeError("existing product IDs do not match the first database rows")

    encoder = ProjectedE5Encoder(args.base_model, args.projection, args.device)
    embeddings = np.lib.format.open_memmap(
        args.embeddings,
        mode="w+",
        dtype=np.float32,
        shape=(args.expected_records, encoder.output_dim),
    )
    embeddings[: args.old_records] = old_embeddings

    cursor = connection.execute(
        """
        SELECT id, parent_asin, title, category_norm, brand, color_raw, material,
               features_text, description_text
        FROM products
        WHERE id > ?
        ORDER BY id
        """,
        (args.old_records,),
    )
    started = time.perf_counter()
    completed = args.old_records
    while batch := cursor.fetchmany(args.batch_size):
        texts = [product_text(dict(row)) for row in batch]
        vectors = encoder.encode_items(texts, batch_size=args.batch_size)
        start = int(batch[0]["id"]) - 1
        stop = start + len(batch)
        embeddings[start:stop] = vectors
        completed = stop
        if completed % (args.batch_size * 10) == 0 or completed == count:
            print(
                json.dumps(
                    {
                        "completed": completed,
                        "total": count,
                        "new_vectors": completed - args.old_records,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                ),
                flush=True,
            )
    embeddings.flush()

    with args.product_ids.open("w", encoding="utf-8", newline="\n") as target:
        for row in connection.execute("SELECT parent_asin FROM products ORDER BY id"):
            target.write(str(row[0]) + "\n")
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
        "shape": [count, encoder.output_dim],
        "dtype": "float32",
        "ordering": "products.id ascending; embedding_row = products.id - 1",
        "batch_size": args.batch_size,
        "device": args.device,
        "append_from_records": args.old_records,
        "new_vectors": count - args.old_records,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
