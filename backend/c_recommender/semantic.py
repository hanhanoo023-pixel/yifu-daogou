from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Literal

import numpy as np
import torch
from torch import nn
from torch.nn import functional
from transformers import AutoModel, AutoTokenizer

from backend.schemas import ProductCard


Tower = Literal["query", "item"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class TwoTowerProjection(nn.Module):
    def __init__(self, input_dim: int, output_dim: int):
        super().__init__()
        self.query = nn.Linear(input_dim, output_dim, bias=False)
        self.item = nn.Linear(input_dim, output_dim, bias=False)


def _mean_pool(
    token_embeddings: torch.Tensor,
    attention_mask: torch.Tensor,
) -> torch.Tensor:
    expanded_mask = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    return (token_embeddings * expanded_mask).sum(dim=1) / expanded_mask.sum(dim=1)


class ProjectedE5Encoder:
    def __init__(
        self,
        base_model_path: Path,
        projection_path: Path,
        device: str,
    ):
        checkpoint = torch.load(projection_path, map_location="cpu", weights_only=True)
        self.query_prefix = str(checkpoint["query_prefix"])
        self.item_prefix = str(checkpoint["item_prefix"])
        self.max_length = 128
        self.device = torch.device(device)
        self.tokenizer = AutoTokenizer.from_pretrained(
            base_model_path,
            local_files_only=True,
        )
        self.base_model = AutoModel.from_pretrained(
            base_model_path,
            local_files_only=True,
        ).to(self.device)
        self.base_model.eval()
        self.projection = TwoTowerProjection(
            int(checkpoint["input_dim"]),
            int(checkpoint["output_dim"]),
        ).to(self.device)
        self.projection.load_state_dict(checkpoint["state_dict"])
        self.projection.eval()
        self.output_dim = int(checkpoint["output_dim"])

    def encode(
        self,
        texts: list[str],
        tower: Tower,
        batch_size: int,
    ) -> np.ndarray:
        prefix = self.query_prefix if tower == "query" else self.item_prefix
        projection = self.projection.query if tower == "query" else self.projection.item
        batches: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = [prefix + text for text in texts[start : start + batch_size]]
            tokens = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            tokens = {key: value.to(self.device) for key, value in tokens.items()}
            with torch.inference_mode():
                output = self.base_model(**tokens)
                pooled = _mean_pool(output.last_hidden_state, tokens["attention_mask"])
                pooled = functional.normalize(pooled, dim=-1)
                projected = functional.normalize(projection(pooled), dim=-1)
            batches.append(projected.cpu().numpy().astype(np.float32, copy=False))
        return np.concatenate(batches, axis=0)

    def encode_query(self, text: str) -> np.ndarray:
        return self.encode([text], tower="query", batch_size=1)[0]

    def encode_items(self, texts: list[str], batch_size: int) -> np.ndarray:
        return self.encode(texts, tower="item", batch_size=batch_size)


def product_text(row: dict[str, object]) -> str:
    values: Iterable[object] = (
        row["title"],
        row["category_norm"],
        row["brand"],
        row["color_raw"],
        row["material"],
        row["features_text"],
        row["description_text"],
    )
    return "; ".join(str(value).strip() for value in values if value)


def query_text(raw_query: str, filters: dict[str, object]) -> str:
    structured = "; ".join(
        f"{key}={value}"
        for key, value in filters.items()
        if value is not None and value != []
    )
    return f"用户原始需求：{raw_query}；结构化条件：{structured}"


class SemanticIndex:
    def __init__(
        self,
        embeddings_path: Path,
        metadata_path: Path,
        product_ids_path: Path,
        database_path: Path,
        projection_path: Path,
        encoder: ProjectedE5Encoder,
    ):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        checksums = (
            (database_path, "database_sha256"),
            (embeddings_path, "embeddings_sha256"),
            (product_ids_path, "product_ids_sha256"),
            (projection_path, "projection_sha256"),
        )
        for path, key in checksums:
            actual = sha256(path)
            if actual != metadata[key]:
                raise ValueError(f"{path} SHA-256 mismatch: {actual} != {metadata[key]}")

        self.embeddings = np.load(embeddings_path, mmap_mode="r")
        expected_shape = tuple(metadata["shape"])
        if self.embeddings.shape != expected_shape:
            raise ValueError(
                f"embedding shape mismatch: {self.embeddings.shape} != {expected_shape}"
            )
        if metadata["records"] != self.embeddings.shape[0]:
            raise ValueError(
                f"embedding count mismatch: {self.embeddings.shape[0]} != "
                f"{metadata['records']}"
            )
        if self.embeddings.dtype != np.dtype(metadata["dtype"]):
            raise ValueError(
                f"embedding dtype mismatch: {self.embeddings.dtype} != {metadata['dtype']}"
            )
        with product_ids_path.open("r", encoding="utf-8") as product_ids:
            product_id_count = sum(1 for _ in product_ids)
        if product_id_count != self.embeddings.shape[0]:
            raise ValueError(
                f"product ID count mismatch: {product_id_count} != {self.embeddings.shape[0]}"
            )
        if self.embeddings.shape[1] != encoder.output_dim:
            raise ValueError(
                f"embedding dimension mismatch: {self.embeddings.shape[1]} != "
                f"{encoder.output_dim}"
            )
        self.encoder = encoder

    def score(
        self,
        raw_query: str,
        filters: dict[str, object],
        products: list[ProductCard],
    ) -> list[float]:
        vector = self.encoder.encode_query(query_text(raw_query, filters))
        rows = np.asarray([product.embedding_row for product in products], dtype=np.int64)
        candidate_vectors = np.asarray(self.embeddings[rows], dtype=np.float32)
        return (candidate_vectors @ vector).tolist()

    def top_rows(
        self,
        raw_query: str,
        filters: dict[str, object],
        embedding_rows: list[int],
        limit: int,
    ) -> list[tuple[int, float]]:
        if not embedding_rows:
            return []
        vector = self.encoder.encode_query(query_text(raw_query, filters))
        best_rows = np.empty(0, dtype=np.int64)
        best_scores = np.empty(0, dtype=np.float32)
        for start in range(0, len(embedding_rows), 50_000):
            rows = np.asarray(embedding_rows[start : start + 50_000], dtype=np.int64)
            scores = np.asarray(self.embeddings[rows], dtype=np.float32) @ vector
            best_rows = np.concatenate((best_rows, rows))
            best_scores = np.concatenate((best_scores, scores))
            if len(best_rows) > limit:
                selected = np.argpartition(-best_scores, limit - 1)[:limit]
                best_rows = best_rows[selected]
                best_scores = best_scores[selected]
        order = np.argsort(-best_scores)
        return [
            (int(best_rows[index]), float(best_scores[index]))
            for index in order[:limit]
        ]
