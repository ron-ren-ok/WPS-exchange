import importlib.util
import re
import sys
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("progress_card", SCRIPTS / "prepare_progress_card.py")
CARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CARD)


class ProgressCardTests(unittest.TestCase):
    def summary(self, actual, measured, projected, target):
        return {"actual": actual, "projected": projected, "target": target,
                "parts": [actual, measured - actual, projected - measured, max(target - projected, 0)]}

    def test_success_has_no_gray_tail_and_one_target_marker(self):
        s = self.summary(40, 45, 60, 30)
        self.assertEqual(CARD.bar_parts(s), [13, 2, 5, 0])
        self.assertNotIn(CARD.COLORS[3], CARD.progress_bar(s))
        self.assertEqual(CARD.progress_bar(s).count("│"), 1)

    def test_expected_success_before_actual_success_has_no_gap(self):
        s = self.summary(20, 25, 40, 30)
        self.assertEqual(CARD.bar_parts(s)[3], 0)
        block = CARD.metric_block("血量", "万美元", s, 30)
        self.assertIn("预计达标", block)
        self.assertIn("实际缺口 10.00 万 · 预计超目标 10.00 万", block)
        self.assertNotIn("实际超目标", block)

    def test_shortfall_and_tiny_shortfall_keep_visible_gap(self):
        for s in [self.summary(50, 60, 70, 100), self.summary(99.9, 99.9, 99.9, 100)]:
            self.assertGreater(CARD.bar_parts(s)[3], 0)
            self.assertEqual(sum(CARD.bar_parts(s)), 20)
            self.assertIn(CARD.COLORS[3], CARD.progress_bar(s))

    def test_exact_target_and_zero_prediction(self):
        self.assertEqual(CARD.bar_parts(self.summary(100, 100, 100, 100)), [20, 0, 0, 0])
        self.assertEqual(CARD.bar_parts(self.summary(0, 0, 0, 100)), [0, 0, 0, 20])

    def test_confirmed_copy_and_paragraph_breaks(self):
        blood = CARD.metric_block("血量", "万美元", self.summary(40, 45, 60, 30), 30)
        users = CARD.metric_block("360 新增", "万", self.summary(50, 60, 70, 100), 100)
        self.assertIn("实际超目标 10.00 万 · 预计超目标 30.00 万", blood)
        self.assertIn("实际缺口 50.00 万 · 预计缺口 30.00 万", users)
        self.assertNotIn("万人", users)
        self.assertIn("**血量（月目标 30.00）**", blood)
        self.assertIn("**360 新增（月目标 100.00）**", users)
        self.assertIn("已回传 **40.00**　·　预测回传 **45.00**　·　月底预测 **60.00**", blood)
        self.assertIn("已回传 **50.00**　·　预测回传 **60.00**　·　月底预测 **70.00**", users)
        for block in [blood, users]:
            self.assertNotIn("已回传累计", block)
            self.assertNotIn("　·　未回传", block)
            self.assertNotIn("目标位置", block)
            self.assertNotIn("未回传估算", block)
            self.assertNotIn("日均", block)
            self.assertNotIn("灰色：", block)
            self.assertNotIn("预计缺口：", block)
            self.assertNotIn("\\n", block)
            self.assertIn("\n\n", block)
            self.assertNotRegex(block, r"(?<!\n)\n(?!\n)")

    def test_both_metrics_report_actual_and_forecast_target_differences(self):
        cases = [
            (50, 60, 70, "实际缺口 50.00 万 · 预计缺口 30.00 万"),
            (50, 60, 110, "实际缺口 50.00 万 · 预计超目标 10.00 万"),
            (110, 115, 120, "实际超目标 10.00 万 · 预计超目标 20.00 万"),
            (50, 60, 100, "实际缺口 50.00 万 · 预计已达标"),
            (100, 100, 100, "实际已达标 · 预计已达标"),
        ]
        for label in ["血量", "360 新增"]:
            for actual, measured, projected, expected in cases:
                with self.subTest(label=label, actual=actual, projected=projected):
                    block = CARD.metric_block(label, "万", self.summary(actual, measured, projected, 100), 100)
                    self.assertIn(f"**{expected}**", block)
                    self.assertNotIn("万人", block)

    def test_month_filter_and_360_filter_with_current_forecast(self):
        records = [
            {"date": date(2026, 9, 1), "partner": "360", "operation": "气泡", "新增": 10000, "血量": None},
            {"date": date(2026, 9, 2), "partner": "Avast", "operation": "气泡", "新增": 990000, "血量": 20000},
            {"date": date(2026, 8, 2), "partner": "Avast", "operation": "气泡", "新增": 990000, "血量": 990000},
            {"date": date(2026, 9, 3), "partner": "360", "operation": "气泡", "新增": 990000, "血量": None},
        ]
        text = CARD.card_content(records, {"血量": 20, "360新增": 60}, date(2026, 9, 2))
        plain = re.sub(r"<[^>]*>", "", text)
        self.assertIn("⚠ 数据不全 · 1 个合作方\n\n360", plain)
        for gray_text in ["⚠ 数据不全 · 1 个合作方", "360", "已回传", "未回传", "后续预测", "预计缺口", "│ 目标位置"]:
            self.assertIn(f"<font color='#8c8c8c'>{gray_text}</font>", text)
        self.assertIn("已回传 **1.00**　·　预测回传 **2.00**", text)
        self.assertIn("月底预测 **30.00**", text)
        self.assertIn("已回传 **2.00**　·　预测回传 **2.00**", text)
        self.assertEqual(text.count("目标位置"), 1)
        self.assertIn("预计缺口　│ 目标位置", plain)
        elements = CARD.card_elements(records, {"血量": 20, "360新增": 60}, date(2026, 9, 2))
        self.assertEqual([element["tag"] for element in elements], ["text", "hr", "text", "hr", "text"])
        self.assertEqual(elements[1], {"tag": "hr", "style": "solid"})
        self.assertEqual(elements[3], {"tag": "hr", "style": "solid"})
        self.assertTrue(elements[0]["content"]["text"].startswith("**血量"))
        self.assertTrue(elements[2]["content"]["text"].startswith("**360 新增"))
        self.assertIn("⚠ 数据不全", elements[4]["content"]["text"])
        self.assertNotIn("─", text)
        self.assertIn("⚠ 数据不全 · 1 个合作方\n\n360\n\n▰ 已回传", plain)
        self.assertLess(text.index("⚠ 数据不全"), text.index("目标位置"))
        self.assertNotIn("990", text)
        self.assertNotIn("![]", text)
        self.assertNotIn("data:image", text)
        self.assertNotIn("万人", text)

    def test_missing_360_is_not_rendered_as_zero_or_forecast(self):
        records = [{"date": date(2026, 9, 2), "partner": "Avast", "operation": "气泡", "新增": 100, "血量": 10000}]
        text = CARD.card_content(records, {"血量": 10, "360新增": 60}, date(2026, 9, 2))
        self.assertIn("当月尚未回传，暂不预测", text)
        self.assertIn("⚠ 数据不全 · 1 个合作方\n\n360", re.sub(r"<[^>]*>", "", text))

    def test_no_month_data_or_invalid_target_fails(self):
        with self.assertRaises(ValueError):
            CARD.card_content([], {"血量": 10, "360新增": 60}, date(2026, 9, 2))
        with self.assertRaises(ValueError):
            CARD.metric_summary([], "血量", 0, date(2026, 9, 2))

    def test_month_end_subtitle_has_no_remaining_days(self):
        self.assertEqual("2026-10-01 · 时间进度 100% · 距月末 0 天", CARD.card_subtitle(date(2026, 9, 30), date(2026, 10, 1)))

    def test_generated_message_fits_webhook_limit(self):
        records = [{"date": date(2026, 9, 2), "partner": "360", "operation": "气泡", "新增": 10000, "血量": 10000}]
        text = CARD.card_content(records, {"血量": 10, "360新增": 60}, date(2026, 9, 2))
        self.assertLess(len(text.encode("utf-8")), 5000)
        bars = [line for line in text.split("\n\n") if "│" in line and "<font" in line and "目标位置" not in line]
        self.assertEqual(len(bars), 2)
        for bar in bars:
            self.assertEqual(len(re.sub(r"<[^>]*>", "", bar).replace("│", "")), 20)


if __name__ == "__main__":
    unittest.main()
