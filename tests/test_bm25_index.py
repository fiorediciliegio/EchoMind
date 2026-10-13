import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace

from app.services.bm25_index import Bm25Hit, LocalBm25Index
from app.services.knowledge import KnowledgeService, tokenize


def chunk(chunk_id: int, source: str, source_index: int, content: str):
    return SimpleNamespace(id=chunk_id, source=source, source_index=source_index, content=content)


class LocalBm25IndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_root = Path("target")
        cls.test_root.mkdir(exist_ok=True)

    def test_saved_index_can_be_loaded_without_rebuilding(self):
        chunks = [
            chunk(1, "sleep.md", 0, "失眠时可以先记录睡眠时间和白天精神状态。"),
            chunk(2, "study.md", 0, "Python 列表推导式可以简化列表构造。"),
        ]

        with tempfile.TemporaryDirectory(dir=self.test_root) as directory:
            index_dir = Path(directory) / "bm25"
            first = LocalBm25Index(index_dir, tokenize)
            self.assertEqual(first.ensure(chunks), "rebuilt")

            second = LocalBm25Index(index_dir, tokenize)
            self.assertEqual(second.ensure(chunks), "loaded")
            hits = second.search("Python 列表", top_k=1)

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk_id, 2)
        self.assertGreater(hits[0].score, 0)

    def test_changed_corpus_rebuilds_the_persisted_index(self):
        initial = [chunk(1, "guide.md", 0, "考试压力可以通过拆分复习计划缓解。")]
        changed = [chunk(1, "guide.md", 0, "Python 字典用于保存键值对。")]

        with tempfile.TemporaryDirectory(dir=self.test_root) as directory:
            index_dir = Path(directory) / "bm25"
            index = LocalBm25Index(index_dir, tokenize)
            self.assertEqual(index.ensure(initial), "rebuilt")

            reloaded = LocalBm25Index(index_dir, tokenize)
            self.assertEqual(reloaded.ensure(changed), "rebuilt")
            hits = reloaded.search("Python 字典", top_k=1)

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].content, changed[0].content)

    def test_corrupt_persisted_index_is_rebuilt(self):
        chunks = [chunk(1, "guide.md", 0, "焦虑时可以先进行缓慢呼吸。")]

        with tempfile.TemporaryDirectory(dir=self.test_root) as directory:
            index_dir = Path(directory) / "bm25"
            LocalBm25Index(index_dir, tokenize).ensure(chunks)
            manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
            (index_dir / manifest["generation"] / "data.csc.index.npy").unlink()

            recovered = LocalBm25Index(index_dir, tokenize)
            self.assertEqual(recovered.ensure(chunks), "rebuilt")
            hits = recovered.search("焦虑 呼吸", top_k=1)

        self.assertEqual(len(hits), 1)

    def test_rebuild_keeps_the_index_root_directory(self):
        initial = [chunk(1, "guide.md", 0, "考试压力管理")]
        changed = [chunk(1, "guide.md", 0, "Python 作业辅导")]

        with tempfile.TemporaryDirectory(dir=self.test_root) as directory:
            index_dir = Path(directory) / "bm25"
            index = LocalBm25Index(index_dir, tokenize)
            index.ensure(initial)
            marker = index_dir / "root-marker.txt"
            marker.write_text("keep", encoding="utf-8")

            index.rebuild(changed)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


class KnowledgeServiceBm25Tests(unittest.TestCase):
    def test_ready_index_query_does_not_scan_database_chunks(self):
        class DatabaseThatMustNotBeQueried:
            def query(self, model):
                raise AssertionError("BM25 query unexpectedly scanned the database")

        class ReadyIndex:
            ready = True

            def search(self, query, top_k):
                return [Bm25Hit(7, "python.md", "Python 列表推导式", 1.25)]

        service = KnowledgeService.__new__(KnowledgeService)
        service.db = DatabaseThatMustNotBeQueried()
        service.settings = SimpleNamespace(
            knowledge_bm25_enabled=True,
            knowledge_bm25_required=False,
        )
        service.bm25_index = ReadyIndex()

        results = service._retrieve_bm25("Python", 4)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk_id, 7)
        self.assertEqual(results[0].score, 1.25)


if __name__ == "__main__":
    unittest.main()
