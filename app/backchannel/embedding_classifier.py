from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from app.backchannel.prototypes import (
    IntentPrototype,
    load_intent_prototypes,
    prototypes_by_intent,
)
from app.core.debug_log import debug_log

DEFAULT_BACKCHANNEL_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_INTENT_THRESHOLD = 0.86
DEFAULT_INTENT_MARGIN = 0.08


class TextEncoder(Protocol):
    def encode(self, sentences: Sequence[str], **kwargs: Any) -> Any:
        """Return one vector per sentence."""


EncoderFactory = Callable[[], TextEncoder]


class EmbeddingIntentClassifier:
    """基于句向量相似度的轻量意图 prototype classifier。

    运行原则是保守采信:最高分低于 threshold 或第一/第二名差距低于
    margin 时返回 None,让接话层落 fallback 或不接话。
    """

    def __init__(
        self,
        *,
        prototypes: Iterable[IntentPrototype] | None = None,
        encoder: TextEncoder | None = None,
        encoder_factory: EncoderFactory | None = None,
        model_name: str = DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
        model_kwargs: dict[str, Any] | None = None,
        threshold: float = DEFAULT_INTENT_THRESHOLD,
        margin: float = DEFAULT_INTENT_MARGIN,
    ) -> None:
        self._prototype_texts = prototypes_by_intent(
            prototypes if prototypes is not None else load_intent_prototypes()
        )
        self._encoder = encoder
        self._encoder_factory = encoder_factory
        self._model_name = model_name
        self._model_kwargs = dict(model_kwargs or {"local_files_only": True})
        self._threshold = max(0.0, min(1.0, float(threshold)))
        self._margin = max(0.0, min(1.0, float(margin)))
        self._prototype_vectors: dict[str, tuple[tuple[float, ...], ...]] | None = None
        self._load_failed = False
        # 控制器单飞,但被取代的旧 runnable 可能与新 runnable 并发跑到懒加载;
        # 用锁保护 check-then-set,避免模型/原型重复初始化。
        # RLock:_ensure_prototype_vectors 持锁后会经 _encode_many 重入 _encoder_instance。
        self._init_lock = threading.RLock()

    @property
    def available(self) -> bool:
        return bool(self._prototype_texts) and not self._load_failed

    def classify_intent(self, text: str) -> tuple[str, float] | None:
        content = (text or "").strip()
        if not content or not self._prototype_texts:
            return None

        prototype_vectors = self._ensure_prototype_vectors()
        if not prototype_vectors:
            return None
        input_vector = self._encode_one(content)
        if input_vector is None:
            return None

        ranked: list[tuple[str, float]] = []
        for intent, vectors in prototype_vectors.items():
            best = max((_cosine(input_vector, vector) for vector in vectors), default=-1.0)
            ranked.append((intent, best))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[1], reverse=True)
        best_intent, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else -1.0
        if best_score < self._threshold:
            return None
        if best_score - second_score < self._margin:
            return None
        return best_intent, best_score

    def _ensure_prototype_vectors(self) -> dict[str, tuple[tuple[float, ...], ...]]:
        if self._prototype_vectors is not None:
            return self._prototype_vectors
        with self._init_lock:
            if self._prototype_vectors is not None:
                return self._prototype_vectors
            vectors: dict[str, tuple[tuple[float, ...], ...]] = {}
            for intent, texts in self._prototype_texts.items():
                encoded = self._encode_many(texts)
                if encoded:
                    vectors[intent] = tuple(encoded)
            self._prototype_vectors = vectors
            return vectors

    def _encoder_instance(self) -> TextEncoder | None:
        if self._encoder is not None:
            return self._encoder
        if self._load_failed:
            return None
        with self._init_lock:
            if self._encoder is not None:
                return self._encoder
            if self._load_failed:
                return None
            try:
                if self._encoder_factory is not None:
                    self._encoder = self._encoder_factory()
                else:
                    from sentence_transformers import SentenceTransformer

                    self._encoder = SentenceTransformer(self._model_name, **self._model_kwargs)
            except Exception as exc:  # noqa: BLE001
                self._load_failed = True
                debug_log(
                    "Backchannel",
                    "接话意图模型加载失败,降级为规则分类",
                    {"model": self._model_name, "error": str(exc)},
                )
                return None
        return self._encoder

    def _encode_one(self, text: str) -> tuple[float, ...] | None:
        vectors = self._encode_many((text,))
        return vectors[0] if vectors else None

    def _encode_many(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        encoder = self._encoder_instance()
        if encoder is None:
            return ()
        try:
            raw_vectors = encoder.encode(
                list(texts),
                convert_to_numpy=False,
                show_progress_bar=False,
            )
        except TypeError:
            raw_vectors = encoder.encode(list(texts))
        except Exception as exc:  # noqa: BLE001
            self._load_failed = True
            debug_log(
                "Backchannel",
                "接话意图向量编码失败,降级为规则分类",
                {"model": self._model_name, "error": str(exc)},
            )
            return ()
        return tuple(
            vector
            for vector in (_normalize(_as_float_tuple(raw)) for raw in _iter_vectors(raw_vectors))
            if vector
        )


def _iter_vectors(raw_vectors: Any) -> Iterable[Any]:
    if hasattr(raw_vectors, "tolist"):
        raw_vectors = raw_vectors.tolist()
    if isinstance(raw_vectors, Sequence) and raw_vectors and not isinstance(raw_vectors[0], (int, float)):
        return raw_vectors
    return (raw_vectors,)


def _as_float_tuple(raw_vector: Any) -> tuple[float, ...]:
    if hasattr(raw_vector, "tolist"):
        raw_vector = raw_vector.tolist()
    try:
        return tuple(float(value) for value in raw_vector)
    except TypeError:
        return ()


def _normalize(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return ()
    return tuple(value / norm for value in vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    length = min(len(left), len(right))
    if length <= 0:
        return -1.0
    return sum(left[index] * right[index] for index in range(length))
