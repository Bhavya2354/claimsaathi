"""Hybrid clause search over Qdrant: dense meaning + BM25 keywords, fused, then re-ranked.

Policy questions mix paraphrase ("is my knee surgery covered") with exact terms
("Excl03", "room rent", "co-payment"). Dense vectors catch the first, BM25 the
second; reciprocal rank fusion merges them and a cross-encoder orders the short
list. All three models run locally on CPU through FastEmbed.
"""

import asyncio
import uuid
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from qdrant_client import AsyncQdrantClient, models

from app.config import RetrievalSettings
from app.policy.chunking import Clause

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding, TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

_DENSE = "dense"
_SPARSE = "bm25"
_UPSERT_BATCH = 64


@dataclass(frozen=True, slots=True)
class ScoredClause:
    clause: Clause
    score: float


class PolicyIndex:
    def __init__(self, settings: RetrievalSettings) -> None:
        self._settings = settings
        if settings.qdrant_path is not None:
            self._client = AsyncQdrantClient(path=str(settings.qdrant_path))
        else:
            key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
            self._client = AsyncQdrantClient(url=str(settings.qdrant_url), api_key=key)
        self._ready = asyncio.Lock()
        self._collection_checked = False

    @cached_property
    def _dense(self) -> "TextEmbedding":
        from fastembed import TextEmbedding

        return TextEmbedding(self._settings.embedding_model, cache_dir=self._cache_dir)

    @cached_property
    def _sparse(self) -> "SparseTextEmbedding":
        from fastembed import SparseTextEmbedding

        return SparseTextEmbedding(self._settings.sparse_model, cache_dir=self._cache_dir)

    @cached_property
    def _reranker(self) -> "TextCrossEncoder":
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        return TextCrossEncoder(self._settings.rerank_model, cache_dir=self._cache_dir)

    @property
    def _cache_dir(self) -> str:
        return str(self._settings.model_cache_dir)

    async def warm_up(self) -> None:
        """Load the models once at startup so the first upload does not pay for it."""
        await asyncio.to_thread(lambda: (self._dense, self._sparse, self._reranker))
        await self._ensure_collection()

    async def close(self) -> None:
        await self._client.close()

    async def _ensure_collection(self) -> None:
        async with self._ready:
            if self._collection_checked:
                return
            name = self._settings.qdrant_collection
            if not await self._client.collection_exists(name):
                size = await asyncio.to_thread(lambda: len(next(iter(self._dense.embed(["x"])))))
                await self._client.create_collection(
                    name,
                    vectors_config={
                        _DENSE: models.VectorParams(size=size, distance=models.Distance.COSINE)
                    },
                    sparse_vectors_config={
                        _SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
                    },
                )
                await self._client.create_payload_index(
                    name, "case_id", field_schema=models.PayloadSchemaType.KEYWORD
                )
            self._collection_checked = True

    def _embed_passages(
        self, texts: list[str]
    ) -> tuple[list[list[float]], list[models.SparseVector]]:
        dense = [vector.tolist() for vector in self._dense.passage_embed(texts)]
        sparse = [
            models.SparseVector(indices=s.indices.tolist(), values=s.values.tolist())
            for s in self._sparse.passage_embed(texts)
        ]
        return dense, sparse

    async def add(self, case_id: str, document_id: str, clauses: list[Clause]) -> None:
        await self._ensure_collection()
        for start in range(0, len(clauses), _UPSERT_BATCH):
            batch = clauses[start : start + _UPSERT_BATCH]
            dense, sparse = await asyncio.to_thread(self._embed_passages, [c.text for c in batch])
            points = [
                models.PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}/{clause.clause_id}")),
                    vector={_DENSE: d, _SPARSE: s},
                    payload={
                        "case_id": case_id,
                        "document_id": document_id,
                        "clause_id": clause.clause_id,
                        "section": clause.section,
                        "page_start": clause.page_start,
                        "page_end": clause.page_end,
                        "text": clause.text,
                    },
                )
                for clause, d, s in zip(batch, dense, sparse, strict=True)
            ]
            await self._client.upsert(self._settings.qdrant_collection, points=points)

    async def search(
        self, case_id: str, query: str, top_k: int | None = None
    ) -> list[ScoredClause]:
        await self._ensure_collection()
        limit = self._settings.retrieval_candidates
        case_filter = _match(case_id=case_id)
        dense, sparse = await asyncio.to_thread(self._embed_query, query)
        response = await self._client.query_points(
            self._settings.qdrant_collection,
            prefetch=[
                models.Prefetch(query=dense, using=_DENSE, limit=limit, filter=case_filter),
                models.Prefetch(query=sparse, using=_SPARSE, limit=limit, filter=case_filter),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        candidates = [_to_clause(point.payload or {}) for point in response.points]
        if not candidates:
            return []
        scores = await asyncio.to_thread(
            lambda: list(self._reranker.rerank(query, [c.text for c in candidates]))
        )
        ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
        keep = top_k or self._settings.retrieval_top_k
        return [ScoredClause(clause=c, score=float(s)) for c, s in ranked[:keep]]

    def _embed_query(self, query: str) -> tuple[list[float], models.SparseVector]:
        dense = next(iter(self._dense.query_embed(query))).tolist()
        raw = next(iter(self._sparse.query_embed(query)))
        return dense, models.SparseVector(indices=raw.indices.tolist(), values=raw.values.tolist())

    async def clauses(self, case_id: str, clause_ids: set[str]) -> list[Clause]:
        """Fetch specific clauses, used by the checker to verify citations."""
        if not clause_ids:
            return []
        await self._ensure_collection()
        points, _ = await self._client.scroll(
            self._settings.qdrant_collection,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(key="case_id", match=models.MatchValue(value=case_id)),
                    models.FieldCondition(
                        key="clause_id", match=models.MatchAny(any=sorted(clause_ids))
                    ),
                ]
            ),
            limit=len(clause_ids),
            with_payload=True,
        )
        return [_to_clause(point.payload or {}) for point in points]

    async def delete_document(self, case_id: str, document_id: str) -> None:
        await self._ensure_collection()
        await self._client.delete(
            self._settings.qdrant_collection,
            points_selector=models.FilterSelector(
                filter=_match(case_id=case_id, document_id=document_id)
            ),
        )

    async def delete_case(self, case_id: str) -> None:
        await self._ensure_collection()
        await self._client.delete(
            self._settings.qdrant_collection,
            points_selector=models.FilterSelector(filter=_match(case_id=case_id)),
        )


def _match(**fields: str) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in fields.items()
        ]
    )


def _to_clause(payload: dict[str, object]) -> Clause:
    return Clause(
        clause_id=str(payload["clause_id"]),
        section=str(payload["section"]),
        page_start=int(str(payload["page_start"])),
        page_end=int(str(payload["page_end"])),
        text=str(payload["text"]),
    )
