import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("model_knob", Path(__file__).with_name("model-knob.py"))
knob = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knob)


class KnobTests(unittest.TestCase):
    def test_detents_restart_idle_deadline_and_only_final_choice_settles(self):
        s = knob.Selection(5, 0.5)
        s.rotate(1, 0)
        s.rotate(1, 0.4)
        s.rotate(1, 0.8)
        self.assertIsNone(s.settle(1.29))
        self.assertEqual(s.settle(1.31), 2)
        s.committed = 2
        self.assertIsNone(s.settle(2))
        s.rotate(-1, 3)
        self.assertEqual(s.settle(3.5), 1)

    def test_wrap_both_directions_and_cancel_returns_to_committed(self):
        s = knob.Selection(5, 1)
        s.rotate(-1, 0)
        self.assertEqual(s.settle(1), 4)
        s.committed = 4
        s.rotate(1, 2)
        self.assertEqual(s.pending, 0)
        s.cancel()
        self.assertIsNone(s.settle(5))
        s.rotate(-1, 6)
        self.assertEqual(s.pending, 3)

    def test_exact_requested_order_and_commands(self):
        presets = knob.load_presets(Path(__file__).with_name("model-presets.json"))
        self.assertEqual([(p['model'], p['reasoning']) for p in presets], [
            ('gpt-6.1-sol', 'xhigh'), ('gpt-6.1-sol', 'high'),
            ('gpt-6.1-sol', 'medium'), ('gpt-6-luna', 'xhigh'), ('gpt-6-luna', 'high')])
        self.assertEqual(knob.command(presets[2]), '/model gpt-6.1-sol --reasoning medium')

    def test_failed_apply_leaves_committed_selection_unchanged(self):
        s = knob.Selection(5, 0.5)
        s.committed = 1
        s.rotate(1, 0)
        self.assertEqual(s.settle(0.5), 2)
        self.assertEqual(s.committed, 1)
        s.rotate(1, 1)
        self.assertEqual(s.pending, 2)


if __name__ == '__main__':
    unittest.main()
