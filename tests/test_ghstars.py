"""单元测试（标准库 unittest，零依赖，任何人 clone 即可跑）。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ghstars import index as index_mod
from ghstars import store


PROJECT = {
    "full_name": "Owner/Demo",
    "description": "A demo tool for testing",
    "stargazers_count": 42,
    "language": "Python",
    "topics": ["cli"],
    "html_url": "https://github.com/Owner/Demo",
    "starred_at": "2026-01-02T03:04:05Z",
}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_render_matches_legacy_format(self):
        filename, content = store.render_markdown(
            PROJECT, "tools-utilities", ["命令行工具", "测试"], "一个用于测试的示例工具。"
        )
        self.assertEqual(filename, "Owner-Demo.md")
        self.assertTrue(content.startswith("---\ntitle: Demo\ntype: github-star\n"))
        self.assertIn("repo: Owner/Demo", content)
        self.assertIn("tags: [命令行工具, 测试]", content)
        self.assertIn("\n# Demo\n", content)
        self.assertIn("一个用于测试的示例工具。", content)
        self.assertIn("🔗 [GitHub](https://github.com/Owner/Demo)", content)
        self.assertTrue(content.endswith("\n"))

    def test_frontmatter_roundtrip(self):
        _, content = store.render_markdown(PROJECT, "tools-utilities", ["标签A"], "中文描述")
        meta, body = store.parse_frontmatter(content)
        self.assertEqual(meta["repo"], "Owner/Demo")
        self.assertEqual(meta["tags"], ["标签A"])
        self.assertEqual(meta["stars"], "42")
        self.assertIn("# Demo", body)

    def test_scan_is_case_insensitive(self):
        store.write_project(self.root, PROJECT, "tools-utilities", ["x"], "")
        found = store.scan_existing(self.root)
        self.assertIn("owner/demo", found)
        self.assertNotIn("Owner/Demo", found)

    def test_archive_idempotent_and_normalized(self):
        path = store.write_project(self.root, PROJECT, "tools-utilities", ["x"], "")
        existing = store.scan_existing(self.root)

        first = store.archive_removed(existing, {"owner/demo"})
        self.assertEqual(first["archived"], 1)
        self.assertEqual(path.read_text(encoding="utf-8").count("github-star-archived"), 1)

        # 再跑一次：已是规范归档态 → 必须跳过，不能叠加
        second = store.archive_removed(existing, {"owner/demo"})
        self.assertEqual(second["archived"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(path.read_text(encoding="utf-8").count("github-star-archived"), 1)

        # 历史脏数据（叠到 ×2）→ 归一为规范值
        text = path.read_text(encoding="utf-8").replace(
            "type: github-star-archived", "type: github-star-archived-archived"
        )
        path.write_text(text, encoding="utf-8")
        third = store.archive_removed(existing, {"owner/demo"})
        self.assertEqual(third["normalized"], 1)
        self.assertIn("type: github-star-archived\n", path.read_text(encoding="utf-8"))

    def test_write_index_counts(self):
        store.write_project(self.root, PROJECT, "tools-utilities", ["x"], "")
        store.write_index(self.root)
        text = (self.root / "_index.md").read_text(encoding="utf-8")
        self.assertIn("tools-utilities/", text)
        self.assertIn("合计：1", text)


class IndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store_dir = self.root / "stars"
        self.db = self.root / "index.db"

        store.write_project(
            self.store_dir, PROJECT, "tools-utilities", ["命令行", "自动化"], "批量处理文件的命令行工具"
        )
        archived = dict(PROJECT)
        archived["full_name"] = "Owner/Old"
        archived["html_url"] = "https://github.com/Owner/Old"
        store.write_project(self.store_dir, archived, "tools-utilities", ["归档项目"], "")
        path = self.store_dir / "tools-utilities" / "Owner-Old.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("type: github-star", "type: github-star-archived"),
            encoding="utf-8",
        )
        self.info = index_mod.build(self.store_dir, self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_counts(self):
        self.assertEqual(self.info["indexed"], 2)
        self.assertEqual(self.info["tokenizer"], "trigram")

    def test_search_long_chinese_term(self):
        rows = index_mod.search(self.db, "命令行工具")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["repo"], "Owner/Demo")

    def test_search_short_chinese_term_uses_like(self):
        rows = index_mod.search(self.db, "自动化")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["repo"], "Owner/Demo")

    def test_archived_excluded_by_default(self):
        # 默认不出现已归档项目
        self.assertEqual(index_mod.search(self.db, "归档项目"), [])
        self.assertTrue(index_mod.search(self.db, "归档项目", include_archived=True))
        # 回收站项目：默认搜不到，显式要求才出现
        self.assertEqual(index_mod.search(self.db, "Owner/Old"), [])
        with_archived = index_mod.search(self.db, "Owner/Old", include_archived=True)
        self.assertTrue(with_archived)
        self.assertTrue(with_archived[0]["archived"])

    def test_get_repo_case_insensitive(self):
        row = index_mod.get_repo(self.db, "owner/demo")
        self.assertIsNotNone(row)
        self.assertEqual(row["repo"], "Owner/Demo")

    def test_stats(self):
        data = index_mod.stats(self.db)
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["archived"], 1)


class RankingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.store_dir = root / "stars"
        self.db = root / "index.db"
        tagged = dict(PROJECT, full_name="Owner/Tagged", html_url="https://github.com/Owner/Tagged")
        body_only = dict(PROJECT, full_name="Owner/BodyOnly", html_url="https://github.com/Owner/BodyOnly")
        store.write_project(self.store_dir, tagged, "tools-utilities", ["视频制作"], "")
        store.write_project(self.store_dir, body_only, "tools-utilities", [], "支持视频制作流程的脚本")
        index_mod.build(self.store_dir, self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tag_hit_beats_body_hit(self):
        rows = index_mod.search(self.db, "视频制作")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["repo"], "Owner/Tagged")
        self.assertGreater(rows[0]["relevance"], rows[1]["relevance"])

    def test_exact_repo_query_ranks_first(self):
        rows = index_mod.search(self.db, "Owner/BodyOnly")
        self.assertEqual(rows[0]["repo"], "Owner/BodyOnly")

    # ── 2026-10-30 检索重构的回归用例 ──────────────────────────────

    def test_match_labels_layer_evidence(self):
        """整串命中标签 = phrase 层；只在描述里出现 = full 层。"""
        rows = index_mod.search(self.db, "视频制作")
        self.assertEqual(rows[0]["repo"], "Owner/Tagged")
        self.assertEqual(rows[0]["match"], "phrase")
        self.assertEqual(rows[1]["match"], "full")

    def test_reordered_query_still_recalls(self):
        """词序不同也要能召回（旧版整串匹配在这里返回 0 条）。"""
        rows = index_mod.search(self.db, "制作视频")
        self.assertTrue(rows, "「制作视频」应通过 bigram 召回含「视频制作」的项目")
        repos = [r["repo"] for r in rows]
        self.assertIn("Owner/BodyOnly", repos)
        # 标签命中仍然强于描述命中
        self.assertEqual(rows[0]["repo"], "Owner/Tagged")

    def test_no_contiguous_phrase_but_full_bigrams(self):
        """「视频制作流程」不是整串命中，但 bigram 全覆盖 → full 层。"""
        rows = index_mod.search(self.db, "视频制作流程")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["repo"], "Owner/BodyOnly")
        self.assertEqual(rows[0]["match"], "full")

    def test_unrelated_query_returns_empty(self):
        """库里没有对应内容时返回空，而不是硬凑（旧版/大语料下会凑出噪声）。"""
        self.assertEqual(index_mod.search(self.db, "读书笔记"), [])


class CategoryTests(unittest.TestCase):
    def test_topics_win_over_description(self):
        from ghstars.classify import classify

        categories = {"mcp-tools": ["mcp"], "tools-utilities": ["cli"]}
        project = {"topics": ["mcp"], "description": "a cli tool"}
        self.assertEqual(classify(project, categories), "mcp-tools")

    def test_ai_keyword_not_used_for_ai_agents(self):
        from ghstars.classify import classify

        categories = {"ai-agents": ["agent"]}
        project = {"topics": [], "description": "an ai powered note app"}
        self.assertEqual(classify(project, categories), "other")


class LlmGuardTests(unittest.TestCase):
    """LLM 静默失败的护栏：计数器 + report.llm.failed_all。

    回归背景（2026-10-30）：网关模型改名后 chat/completions 稳定 400，
    标签全变空、脚本仍 exit 0，10 个项目静默入库无中文标签。
    """

    def setUp(self):
        from ghstars import llm as llm_mod

        llm_mod.reset_stats()
        self.llm = llm_mod
        self.settings = {
            "base": "http://127.0.0.1:9/v1",
            "key": "k",
            "model": "m",
            "timeout": 2,
            "headers": {},
        }

    def test_ok_counter(self):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                import json as _json

                return _json.dumps({"choices": [{"message": {"content": "你好"}}]}).encode()

        import unittest.mock as mock

        with mock.patch("urllib.request.urlopen", return_value=_Resp()):
            self.assertEqual(self.llm.call_llm("hi", self.settings, max_tokens=5), "你好")
        st = self.llm.stats()
        self.assertEqual((st["calls"], st["ok"], st["failed"]), (1, 1, 0))

    def test_failed_counter_and_last_error(self):
        import unittest.mock as mock
        import urllib.error
        from email.message import Message

        err = urllib.error.HTTPError(
            "http://127.0.0.1:9/v1/chat/completions",
            400,
            "Bad Request",
            Message(),
            __import__("io").BytesIO(b'{"error":"Model is unavailable"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(self.llm.call_llm("hi", self.settings, max_tokens=5), "")
        st = self.llm.stats()
        self.assertEqual((st["calls"], st["ok"], st["failed"]), (1, 0, 1))
        self.assertIn("400", st["last_error"])
        self.assertIn("Model is unavailable", st["last_error"])

    def test_reset_stats(self):
        self.llm._stats.update({"calls": 3, "ok": 1, "failed": 2})
        self.llm.reset_stats()
        st = self.llm.stats()
        self.assertEqual((st["calls"], st["ok"], st["failed"]), (0, 0, 0))

    def test_sync_report_flags_failed_all(self):
        """LLM 全挂时 report['llm']['failed_all'] 为真（CLI 据此 exit 3）。"""
        import unittest.mock as mock

        from ghstars import sync as sync_mod

        cfg = {
            "categories": {"other": []},
            "llm": {"base": "http://127.0.0.1:9/v1", "model": "m", "api_key": "k", "timeout": 2},
        }
        project = {
            "full_name": "acme/thing",
            "name": "thing",
            "owner": "acme",
            "description": "a thing",
            "topics": [],
            "stars": 1,
            "language": "Python",
            "html_url": "https://github.com/acme/thing",
            "starred_at": "2026-10-30",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sync_mod.github, "fetch_stars", return_value=[project]), \
                 mock.patch.object(sync_mod.github, "get_token", return_value=""), \
                 mock.patch.object(sync_mod.github, "fetch_readme", return_value="readme"):
                report = sync_mod.sync(
                    cfg,
                    store_override=tmp,
                    username="acme",
                    rebuild_index=False,
                    quiet=True,
                )
        self.assertEqual(report["added"], 1)
        self.assertTrue(report["llm"]["enabled"])
        # 每个项目会调两次 LLM（分类兜底 + 标签生成），两次都失败
        self.assertGreaterEqual(report["llm"]["calls"], 1)
        self.assertEqual(report["llm"]["failed"], report["llm"]["calls"])
        self.assertEqual(report["llm"]["tag_failed"], 1)
        self.assertTrue(report["llm"]["failed_all"])
        self.assertIn("LLM", report["llm"]["note"])


if __name__ == "__main__":
    unittest.main()
