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
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--embeddings", type=Path, required=True)
    parser.add_argument("--product-ids", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    parser.add_argument("--expected-records", type=int, default=252_413)
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

    connection = sqlite3.connect(
        f"file:{args.database}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    count, minimum_id, maximum_id = connection.execute(
        "SELECT COUNT(*), MIN(id), MAX(id) FROM products"
    ).fetchone()
    if (count, minimum_id, maximum_id) != (args.expected_records, 1, args.expected_records):
        raise RuntimeError(
            f"expected contiguous ids 1..{args.expected_records}, got count={count}, "
            f"min={minimum_id}, max={maximum_id}"
        )

    encoder = ProjectedE5Encoder(args.base_model, args.projection, args.device)
    embeddings = np.lib.format.open_memmap(
        args.embeddings,
        mode="w+",
        dtype=np.float32,
        shape=(count, encoder.output_dim),
    )
    cursor = connection.execute(
        """
        SELECT id, parent_asin, title, category_norm, brand, color_raw, material,
               features_text, description_text
        FROM products
        ORDER BY id
        """
    )
    started = time.perf_counter()
    completed = 0
    with args.product_ids.open("w", encoding="utf-8", newline="\n") as id_file:
        while batch := cursor.fetchmany(args.batch_size):
            texts = [product_text(dict(row)) for row in batch]
            vectors = encoder.encode_items(texts, batch_size=args.batch_size)
            start = int(batch[0]["id"]) - 1
            stop = start + len(batch)
            embeddings[start:stop] = vectors
            for row in batch:
                id_file.write(str(row["parent_asin"]) + "\n")
            completed = stop
            if completed % (args.batch_size * 100) == 0 or completed == count:
                print(
                    json.dumps(
                        {
                            "completed": completed,
                            "total": count,
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                        }
                    ),
                    flush=True,
                )
    embeddings.flush()
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
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
