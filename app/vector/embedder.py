from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TextEmbedder(Protocol):
    """Интерфейс embedder-а для подмены в тестах и в разных runtime-провайдерах."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass(frozen=True)
class FastEmbedSettings:
    model_name: str
    batch_size: int = 32


class FastEmbedTextEmbedder:
    """
    CPU-friendly embedder на базе fastembed.

    Почему этот вариант:
    - de-facto стек для Qdrant-экосистемы;
    - меньше самописного кода (готовая модельная обертка);
    - хороший баланс качества/скорости на CPU.
    """

    def __init__(self, settings: FastEmbedSettings) -> None:
        self.settings = settings
        try:
            from fastembed import TextEmbedding
        except ModuleNotFoundError as exc:  # pragma: no cover - зависит от окружения
            raise RuntimeError(
                "fastembed is not installed. Run: pip install -r requirements.txt"
            ) from exc

        # Lazy-init модели: загрузка только когда реально нужен embedding.
        self._model = TextEmbedding(model_name=settings.model_name)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.embed(texts, batch_size=self.settings.batch_size)
        # fastembed отдает iterable numpy-векторов; приводим к обычным list[float]
        # для единообразной сериализации и тестируемости.
        return [vector.tolist() for vector in vectors]


def build_text_embedder(model_name: str, *, batch_size: int = 32) -> TextEmbedder:
    return FastEmbedTextEmbedder(FastEmbedSettings(model_name=model_name, batch_size=batch_size))

