from __future__ import annotations

import os
import threading
import uuid
from typing import Any

try:
    from .model_assets import DEFAULT_EMBEDDING_DIMS, DEFAULT_EMBEDDING_MODEL, _embedding_model_snapshot
    from .support import log_event
except ImportError:
    from model_assets import DEFAULT_EMBEDDING_DIMS, DEFAULT_EMBEDDING_MODEL, _embedding_model_snapshot
    from support import log_event


class VectorIndex:
    """向量只作索引。写入失败保留 SQLite 中的待处理版本。"""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.lock = threading.Lock()
        self.schedule_lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.client = None
        self.model = None
        self.collection = ""
        self.rebuild_requested = False
        self.status = "missing"
        self.error = ""
        self.completed = 0
        self.total = 0

    def schedule(self, *, rebuild: bool = False) -> None:
        with self.schedule_lock:
            if self.stop.is_set():
                return
            self.rebuild_requested |= rebuild
            self.wake.set()
            if self.thread is None or not self.thread.is_alive():
                if _embedding_model_snapshot(cache_dir=self.store.memory_cache_dir) is None:
                    self.status = "missing"
                    return
                self.thread = threading.Thread(target=self._run, name="sakura-memory-index", daemon=True)
                self.thread.start()

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.status, "error": self.error, "completed": self.completed,
                "total": self.total, "model": DEFAULT_EMBEDDING_MODEL, "collection": self.collection}

    def _run(self) -> None:
        try:
            from fastembed import TextEmbedding
            from qdrant_client import QdrantClient

            self.status = "loading"
            snapshot = _embedding_model_snapshot(cache_dir=self.store.memory_cache_dir)
            self.model = TextEmbedding(
                DEFAULT_EMBEDDING_MODEL, specific_model_path=str(snapshot), local_files_only=True,
                threads=min(4, os.cpu_count() or 1), providers=["CPUExecutionProvider"],
            )
            self.client = QdrantClient(path=str(self.store.memory_dir / "vectors"))
            self.collection = self.store.get_state("vector_collection", scope="")
            if self.collection and not self.client.collection_exists(self.collection):
                self.collection = ""
            while not self.stop.is_set():
                self.wake.wait()
                self.wake.clear()
                if self.stop.is_set():
                    break
                rebuild = self.rebuild_requested or not self.collection
                self.rebuild_requested = False
                with self.lock:
                    if rebuild:
                        self._rebuild()
                    self._sync_pending()
                self.status = "ready"
                self.error = ""
        except Exception as exc:
            if not self.stop.is_set():
                self.status = "failed"
                self.error = str(exc)
                log_event("Memory", "向量索引更新失败，关键词检索仍可使用",
                          {"error": str(exc)}, event="memory.index.failed", severity="warning")
        finally:
            with self.lock:
                if self.client is not None:
                    self.client.close()
                    self.client = None
                self.model = None

    def _upsert(self, collection: str, rows: list[dict]) -> None:
        from qdrant_client.models import PointStruct

        if not rows:
            return
        vectors = self.model.embed([row["content"] for row in rows])
        points = [PointStruct(id=row["point_id"], vector=vector.tolist(), payload={
            "scope": row["scope"], "memory_id": row["id"], "revision": row["revision"],
        }) for row, vector in zip(rows, vectors)]
        if not self.stop.is_set():
            self.client.upsert(collection, points=points, wait=True)

    def _rebuild(self) -> None:
        from qdrant_client.models import Distance, VectorParams

        self.status = "building"
        new_collection = "bge_zh_" + uuid.uuid4().hex
        previous = self.collection
        self.client.create_collection(
            new_collection, vectors_config=VectorParams(size=DEFAULT_EMBEDDING_DIMS, distance=Distance.COSINE),
        )
        rows = self.store.index_rows(all_rows=True)
        self.total = len(rows)
        self.completed = 0
        log_event("Memory", "开始重建中文向量索引", {"total": self.total}, event="memory.index.started")
        try:
            for offset in range(0, len(rows), 32):
                if self.stop.is_set():
                    return
                batch = rows[offset:offset + 32]
                self._upsert(new_collection, [row for row in batch if not row["deleted"]])
                self.completed += len(batch)
            if self.stop.is_set():
                return
            self.store.activate_index(new_collection, rows)
            self.collection = new_collection
            log_event("Memory", "中文向量索引已切换", {"total": self.total}, event="memory.index.completed")
        finally:
            if self.collection != new_collection:
                self.client.delete_collection(new_collection)
        if previous and previous != self.collection:
            self.client.delete_collection(previous)

    def _sync_pending(self) -> None:
        from qdrant_client.models import PointIdsList

        while not self.stop.is_set():
            rows = self.store.index_rows(limit=32)
            if not rows:
                return
            self.status = "indexing"
            self._upsert(self.collection, [row for row in rows if not row["deleted"]])
            deleted = [row["point_id"] for row in rows if row["deleted"]]
            if deleted:
                self.client.delete(self.collection, PointIdsList(points=deleted), wait=True)
            if self.stop.is_set():
                return
            self.store.mark_indexed(rows)

    def search(self, query: str, scope: str, limit: int) -> list[dict]:
        if self.status not in {"ready", "indexing"} or not self.lock.acquire(blocking=False):
            return []
        try:
            if self.client is None or self.model is None or self.stop.is_set():
                return []
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            # BGE 中文检索的 query 指令只加在查询端。
            vector = next(iter(self.model.embed(["为这个句子生成表示以用于检索相关文章：" + query]))).tolist()
            result = self.client.query_points(
                self.collection, query=vector, limit=limit, with_payload=True,
                query_filter=Filter(must=[FieldCondition(key="scope", match=MatchValue(value=scope))]),
            )
            self.error = ""
            return [{"id": point.payload["memory_id"], "revision": point.payload["revision"],
                     "score": float(point.score)} for point in result.points]
        except Exception as exc:
            self.error = str(exc)
            log_event("Memory", "向量检索失败，使用关键词结果", {"error": str(exc)},
                      event="memory.index.search_failed", severity="warning")
            return []
        finally:
            self.lock.release()

    def close(self) -> None:
        self.stop.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join()
        self.status = "stopped"
