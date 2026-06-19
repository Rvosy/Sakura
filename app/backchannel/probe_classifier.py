from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.backchannel.model_cache import DEFAULT_BACKCHANNEL_EMBEDDING_MODEL
from app.core.debug_log import debug_log

# probe 头(逻辑回归)在标注数据上训练得到,随框架分发。零样本原型相似度只是检索,
# 不是分类;这里 embedding 仅产出向量,意图由训练出的线性头判定。
_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_PROBE_HEAD_PATH = _DATA_DIR / "probe_head.npz"
DEFAULT_PROBE_META_PATH = _DATA_DIR / "probe_head_meta.json"
# 弃权阈值:最高类概率低于此值(或落在 none 类)→ 返回 None,让接话层落 fallback。
# 0.5 在标定集上 P≈0.89 / OOS-FPR≈0.10;真实数据回收后可在 meta 里调。
DEFAULT_PROBE_THRESHOLD = 0.5
NONE_LABEL = "none"


class TextEncoder(Protocol):
    def encode(self, sentences: Sequence[str], **kwargs: Any) -> Any:
        """Return one vector per sentence."""


EncoderFactory = Callable[[], TextEncoder]


class ProbeIntentClassifier:
    """bge 句向量 + 训练好的逻辑回归头的意图分类器。

    替代零样本原型相似度:embedding 只编码,意图由从标注数据训练的线性头判定。
    保守采信——最高类概率低于 threshold 或落在 none 类时返回 None(弃权)。
    模型/头加载失败时静默降级(返回 None),接话层退回纯规则。
    """

    # 首次 classify 会冷加载句向量模型(数秒),必须派发到后台线程。
    prefers_background = True

    def __init__(
        self,
        *,
        head_path: Path | str | None = None,
        meta_path: Path | str | None = None,
        encoder: TextEncoder | None = None,
        encoder_factory: EncoderFactory | None = None,
        model_name: str = DEFAULT_BACKCHANNEL_EMBEDDING_MODEL,
        model_kwargs: dict[str, Any] | None = None,
        threshold: float | None = None,
    ) -> None:
        self._head_path = Path(head_path) if head_path else DEFAULT_PROBE_HEAD_PATH
        self._meta_path = Path(meta_path) if meta_path else DEFAULT_PROBE_META_PATH
        self._encoder = encoder
        self._encoder_factory = encoder_factory
        self._model_name = model_name
        self._model_kwargs = dict(model_kwargs or {"local_files_only": True})
        self._model_kwargs.setdefault("device", "cpu")
        self._threshold_override = threshold
        self._coef: np.ndarray | None = None
        self._intercept: np.ndarray | None = None
        self._labels: list[str] | None = None
        self._threshold = DEFAULT_PROBE_THRESHOLD
        self._head_loaded = False
        self._load_failed = False
        # 与被取代的旧 runnable 可能并发跑到懒加载,锁保护 check-then-set;
        # RLock:preload 持锁后会重入 _encoder_instance。
        self._init_lock = threading.RLock()

    def preload(self) -> None:
        """预加载 probe 头与句向量模型。可由启动链路异步调用。"""
        self._ensure_head()
        self._encoder_instance()

    @property
    def available(self) -> bool:
        return self._ensure_head() and not self._load_failed

    def classify_intent(self, text: str) -> tuple[str, float] | None:
        content = (text or "").strip()
        if not content or not self._ensure_head():
            return None
        vector = self._encode_one(content)
        if vector is None:
            return None
        logits = vector @ self._coef.T + self._intercept
        logits = logits - float(logits.max())
        probs = np.exp(logits)
        probs = probs / probs.sum()
        index = int(probs.argmax())
        label = self._labels[index]
        confidence = float(probs[index])
        if label == NONE_LABEL or confidence < self._threshold:
            return None
        return label, confidence

    def _ensure_head(self) -> bool:
        if self._head_loaded:
            return self._coef is not None
        with self._init_lock:
            if self._head_loaded:
                return self._coef is not None
            try:
                data = np.load(self._head_path, allow_pickle=False)
                self._coef = data["coef"].astype("float32")
                self._intercept = data["intercept"].astype("float32")
                self._labels = [str(value) for value in data["labels"].tolist()]
                threshold = self._threshold_override
                if threshold is None and self._meta_path.exists():
                    meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
                    threshold = meta.get("threshold")
                self._threshold = (
                    float(threshold) if threshold is not None else DEFAULT_PROBE_THRESHOLD
                )
            except Exception as exc:  # noqa: BLE001
                self._load_failed = True
                debug_log(
                    "Backchannel",
                    "接话 probe 头加载失败,降级为规则分类",
                    {"path": str(self._head_path), "error": str(exc)},
                )
            self._head_loaded = True
        return self._coef is not None

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

    def _encode_one(self, text: str) -> np.ndarray | None:
        encoder = self._encoder_instance()
        if encoder is None:
            return None
        try:
            raw = encoder.encode(
                [text],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except TypeError:
            # 测试桩 encoder 可能不接受这些 kwargs。
            raw = encoder.encode([text])
        except Exception as exc:  # noqa: BLE001
            self._load_failed = True
            debug_log(
                "Backchannel",
                "接话意图向量编码失败,降级为规则分类",
                {"model": self._model_name, "error": str(exc)},
            )
            return None
        vector = np.asarray(raw, dtype="float32")
        if vector.ndim == 2:
            vector = vector[0]
        if vector.ndim != 1 or vector.shape[0] != self._coef.shape[1]:
            return None
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            return None
        return vector / norm  # 与训练一致:L2 归一化(即便 encoder 已归一也无害)
