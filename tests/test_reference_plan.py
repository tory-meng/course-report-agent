import unittest
from scripts.reference_plan import rubric_goal_mean, uniform_goal_score, normalized_title, threshold_evidence


class ReferencePlanTests(unittest.TestCase):
    def test_full_marks_are_in_denominator(self):
        rows = [{'average': 15.7, 'maximum': 20, 'weight': .3},
                {'average': 39, 'maximum': 50, 'weight': .4},
                {'average': 15.7, 'maximum': 20, 'weight': .3}]
        self.assertAlmostEqual(rubric_goal_mean(rows), .781875)
        self.assertNotAlmostEqual(rubric_goal_mean(rows), (.785*.3 + .78*.4 + .785*.3), places=5)

    def test_same_total_does_not_identify_goal_scores(self):
        a, b = {'usual': 100, 'report': 50}, {'usual': 50, 'report': 100}
        # 相同75分总评可对应不同目标分数，不能用总评分解出目标真值。
        self.assertEqual((a['usual']+a['report'])/2, (b['usual']+b['report'])/2)
        allocation, weight, maxima = {'usual': 30, 'report': 50}, {'usual': .6, 'report': .4}, {'usual': 100, 'report': 100}
        self.assertNotEqual(uniform_goal_score(a, allocation, weight, maxima), uniform_goal_score(b, allocation, weight, maxima))

    def test_unknown_is_not_zero_and_missing_scores_block(self):
        with self.assertRaises(ValueError):
            uniform_goal_score({'x': 80}, {'x': None}, {'x': 1}, {'x': 100})
        with self.assertRaises(ValueError):
            uniform_goal_score({}, {'x': 100}, {'x': 1}, {'x': 100})
        self.assertEqual(uniform_goal_score({'x': 0}, {'x': 100}, {'x': 1}, {'x': 100}), 0)

    def test_editions_deduplicate_and_threshold_is_section_bound(self):
        self.assertEqual(normalized_title('2022级《科学任务训练--大型软件项目综合设计》.pdf'),
                         normalized_title('2023级《科学任务训练-大型软件项目综合设计》.pdf'))
        rows = [{'id': 'x', 'locator': 'page:7', 'ocr': 1,
                 'text': '成绩权重0.60。5.1 课程目标达成标准 将课程目标的达成标准设定为 0.70。'}]
        self.assertEqual(threshold_evidence(rows)[0]['value'], .7)
        self.assertEqual(threshold_evidence([{**rows[0], 'text': '成绩权重0.60'}]), [])

    def test_invalid_or_zero_denominator_rejected(self):
        for row in ({'average': 5, 'maximum': 0, 'weight': 1},
                    {'average': 101, 'maximum': 100, 'weight': 1},
                    {'average': 10, 'maximum': 100, 'weight': 0}):
            with self.assertRaises(ValueError):rubric_goal_mean([row])
