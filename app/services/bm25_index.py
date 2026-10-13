from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Protocol

try:
    import bm25s
except ImportError:  # pragma: no cover - exercised through KnowledgeService fallback
    bm25s = None


class KnowledgeChunkLike(Protocol):
    id: int | None
    source: str
    source_index: int
    content: str


@dataclass(frozen=True)
class Bm25Hit:
    chunk_id: int
    source: str
    content: str
    score: float


class LocalBm25Index:
    schema_version = 1

    def __init__(
        self,
        index_dir: Path,
        tokenizer: Callable[[str], list[str]],
        *,
        mmap: bool = False,
        tokenizer_version: str = "echomind-char-bigram-v1",
    ):
        self.index_dir = Path(index_dir)
        self.tokenizer = tokenizer
        self.mmap = mmap
        self.tokenizer_version = tokenizer_version
        self._lock = threading.RLock()
        self._retriever = None
        self._fingerprint = ""
        self._corpus_size = 0
        self.error = ""

    @property
    def ready(self) -> bool:
        return bool(self._fingerprint)

    @property
    def corpus_size(self) -> int:
        return self._corpus_size

    def ensure(self, chunks: Iterable[KnowledgeChunkLike]) -> str:
        corpus = self._prepare_corpus(chunks)
        fingerprint = self._fingerprint_for(corpus)
        with self._lock:
            if self._fingerprint == fingerprint:
                return "ready"
            if self._can_load(fingerprint):
                try:
                    self._load(fingerprint)
                    return "loaded"
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
            self._build(corpus, fingerprint)
            return "rebuilt"

    def rebuild(self, chunks: Iterable[KnowledgeChunkLike]) -> int:
        corpus = self._prepare_corpus(chunks)
        fingerprint = self._fingerprint_for(corpus)
        with self._lock:
            self._build(corpus, fingerprint)
        return len(corpus)

    def search(self, query: str, top_k: int) -> list[Bm25Hit]:
        tokens = self.tokenizer(query)
        with self._lock:
            if not tokens or self._retriever is None or self._corpus_size == 0 or top_k <= 0:
                return []
            documents, scores = self._retriever.retrieve(
                [tokens],
                k=min(top_k, self._corpus_size),
                show_progress=False,
            )
            hits = []
            for document, score in zip(documents[0], scores[0]):
                value = float(score)
                if value <= 0:
                    continue
                hits.append(
                    Bm25Hit(
                        chunk_id=int(document["chunk_id"]),
                        source=str(document["source"]),
                        content=str(document["content"]),
                        score=value,
                    )
                )
            return hits

    def _build(self, corpus: list[dict], fingerprint: str) -> None:
        if bm25s is None:
            raise RuntimeError("bm25s is not installed")

        self.index_dir.mkdir(parents=True, exist_ok=True)
        generation = f"generation-{fingerprint[:12]}-{uuid.uuid4().hex}"
        generation_dir = self.index_dir / generation
        manifest_tmp = self.index_dir / f".manifest.tmp-{uuid.uuid4().hex}.json"
        generation_dir.mkdir(exist_ok=False)
        retriever = None
        try:
            if corpus:
                retriever = bm25s.BM25(k1=1.5, b=0.75, method="lucene", corpus=corpus)
                retriever.index([self.tokenizer(item["content"]) for item in corpus], show_progress=False)
                retriever.save(generation_dir, corpus=corpus, show_progress=False)
            self._write_manifest(manifest_tmp, fingerprint, len(corpus), generation)
            os.replace(manifest_tmp, self.index_dir / "manifest.json")
        except Exception:
            manifest_tmp.unlink(missing_ok=True)
            shutil.rmtree(generation_dir, ignore_errors=True)
            raise

        self._retriever = retriever
        self._fingerprint = fingerprint
        self._corpus_size = len(corpus)
        self.error = ""
        self._cleanup_generations(generation)

    def _load(self, fingerprint: str) -> None:
        if bm25s is None:
            raise RuntimeError("bm25s is not installed")
        manifest = self._read_manifest()
        corpus_size = int(manifest.get("corpusSize", 0))
        generation_dir = self.index_dir / str(manifest["generation"])
        retriever = None
        if corpus_size:
            retriever = bm25s.BM25.load(
                generation_dir,
                load_corpus=True,
                mmap=self.mmap,
                show_progress=False,
            )
        self._retriever = retriever
        self._fingerprint = fingerprint
        self._corpus_size = corpus_size
        self.error = ""

    def _can_load(self, fingerprint: str) -> bool:
        try:
            manifest = self._read_manifest()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        generation = manifest.get("generation")
        return (
            manifest.get("schemaVersion") == self.schema_version
            and manifest.get("tokenizerVersion") == self.tokenizer_version
            and manifest.get("fingerprint") == fingerprint
            and isinstance(generation, str)
            and generation.startswith("generation-")
            and (self.index_dir / generation).is_dir()
        )

    def _read_manifest(self) -> dict:
        return json.loads((self.index_dir / "manifest.json").read_text(encoding="utf-8"))

    def _write_manifest(self, path: Path, fingerprint: str, corpus_size: int, generation: str) -> None:
        payload = {
            "schemaVersion": self.schema_version,
            "tokenizerVersion": self.tokenizer_version,
            "fingerprint": fingerprint,
            "corpusSize": corpus_size,
            "generation": generation,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _cleanup_generations(self, active_generation: str) -> None:
        for candidate in self.index_dir.glob("generation-*"):
            if candidate.name != active_generation:
                shutil.rmtree(candidate, ignore_errors=True)

    @staticmethod
    def _prepare_corpus(chunks: Iterable[KnowledgeChunkLike]) -> list[dict]:
        corpus = [
            {
                "chunk_id": int(chunk.id),
                "source": chunk.source,
                "source_index": int(chunk.source_index),
                "content": chunk.content,
            }
            for chunk in chunks
            if chunk.id is not None
        ]
        return sorted(corpus, key=lambda item: (item["source"], item["source_index"], item["chunk_id"]))

    def _fingerprint_for(self, corpus: list[dict]) -> str:
        payload = {
            "schemaVersion": self.schema_version,
            "tokenizerVersion": self.tokenizer_version,
            "corpus": corpus,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=16)
def get_local_bm25_index(
    index_dir: str,
    tokenizer: Callable[[str], list[str]],
    mmap: bool = False,
) -> LocalBm25Index:
    return LocalBm25Index(Path(index_dir), tokenizer, mmap=mmap)
