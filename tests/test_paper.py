from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from platform_core.common import ROOT
from platform_core.dataset import PAPER_DATASET, build_default, validate_dataset
from platform_core.manifest import create_run
from platform_core.paper import keyword_match, overview, run_time_queries
from platform_core.paper_dataset import build_paper
from platform_core.scoring import rank_query


class PaperTests(unittest.TestCase):
    def test_frozen_protocol_is_separate_from_development_data(self) -> None:
        data = json.loads(PAPER_DATASET.read_text(encoding="utf-8"))
        validate_dataset(data)
        self.assertEqual(data, build_paper())
        self.assertEqual((len(data["cases"]), len(data["queries"])), (60, 30))
        self.assertEqual(sum(q["task"] == "topic" for q in data["queries"]), 12)
        self.assertEqual(sum(q["task"] == "specific" for q in data["queries"]), 18)
        self.assertFalse({c["id"] for c in data["cases"]} & {c["id"] for c in build_default()["cases"]})
        data["protocol"]["topic_queries"] = 30
        with self.assertRaisesRegex(ValueError, "数量不一致"):
            validate_dataset(data)

    def test_topic_answers_accept_multiple_records_but_specific_answers_remain_specific(self) -> None:
        queries = build_paper()["queries"]
        broad = next(q for q in queries if q["task"] == "topic")
        specific = next(q for q in queries if q["task"] == "specific")
        other_chapter = broad["relevant_ids"][-1]
        self.assertEqual(rank_query(broad["relevant_ids"], [other_chapter], 1)["hit_at_1"], 1)
        self.assertEqual(rank_query(specific["relevant_ids"], [other_chapter], 1)["hit_at_5"], 0)

    def test_original_title_baseline_keeps_evidence_lost_by_summary(self) -> None:
        query = next(q for q in build_paper()["queries"] if q["task"] == "specific")
        record = {"summary": "检测到窗口活动", "active_windows": [json.dumps(
            {"title": "第1节 Python 列表推导式资料 - Visual Studio Code"}, ensure_ascii=False)]}
        self.assertTrue(keyword_match(record, query, raw_title=True))
        self.assertFalse(keyword_match(record, query))
        record["active_windows"] = [json.dumps({"title": "Python 列表推导式 第5节"}, ensure_ascii=False)]
        self.assertFalse(keyword_match(record, query, raw_title=True))

    def test_missing_and_failed_queries_do_not_inflate_primary_results(self) -> None:
        data = build_paper()
        ids = [q["id"] for q in data["queries"] if q["task"] == "topic"]
        queries = {"semantic": [{"id": ids[0], **rank_query(["a"], ["a"], 1)},
                                 {"id": ids[1], **rank_query(["b"], [], 1, "模型不可用")} ]}
        paper = overview(data, [], queries)
        values = paper["tasks"]["topic"]["methods"]["semantic"]
        self.assertEqual((values["total"], values["scored"], values["errors"], values["not_measured"]), (12, 1, 1, 10))
        self.assertEqual(values["hit_at_5"], 1)
        self.assertAlmostEqual(values["hit_at_5_all_questions"], 1 / 12)
        self.assertIsNone(paper["time_retrieval"]["exact_set_accuracy_all_ranges"])

    def test_time_queries_require_the_complete_correct_set(self) -> None:
        data = build_paper()
        start = datetime(2026, 10, 4, 9)
        cases = [{"id": c["id"], "input": {"timestamp": (start + timedelta(seconds=i)).isoformat()}}
                 for i, c in enumerate(data["cases"])]
        class IncorrectStore:
            def get_activities_in_range(self, _start, _end, limit):
                return [{"id": "first"}, {"id": "outside-range"}]
        rows = run_time_queries(data, cases, IncorrectStore(), {"first": data["cases"][0]["id"]}, lambda: False)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["hit_at_5"], 1)
        self.assertFalse(rows[0]["exact_set_match"])
        self.assertEqual(rows[0]["set_precision"], 0.5)
        self.assertEqual(rows[0]["set_recall"], 0.1)
        self.assertEqual(rows[0]["time_start"], start.isoformat())
        self.assertEqual(rows[0]["time_end"], (start + timedelta(seconds=9)).isoformat())

    def test_lan_model_rejects_data_not_declared_synthetic(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT, prefix=".paper-test-") as folder:
            data = build_default()
            data["synthetic"] = False
            path = Path(folder) / "dataset.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "虚构数据集"):
                create_run("custom", path, allow_remote_model=True)

    def test_dashboard_explains_paper_data_without_starting_an_experiment(self) -> None:
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        app.radio[0].set_value("论文实验").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("12题按主题" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
