"""Meaningful accounting checks using synthetic /proc/stat samples."""
import unittest
from unittest.mock import mock_open, patch

import dashboard


class CPUAccountingTests(unittest.TestCase):
    def test_parse_aggregate_and_noncontiguous_cores_without_guest_double_count(self):
        sample = "cpu  100 20 30 400 50 5 10 0 7 3\ncpu0 40 10 10 200 20 2 3 0 4 2\ncpu2 60 10 20 200 30 3 7 0 3 1\nintr 200\n"
        with patch("builtins.open", mock_open(read_data=sample)):
            result = dashboard._cpu_times()
        self.assertEqual(set(result), {"cpu", "cpu0", "cpu2"})
        self.assertEqual(result["cpu"], (615, 450))
        self.assertEqual(result["cpu0"], (285, 220))

    def test_malformed_core_does_not_break_other_measurements(self):
        sample = "cpu 10 0 2 100\ncpu0 bad 0 0 1\ncpu1 1 2\ncpu2 -1 0 0 100\ncpu3 5 0 1 50\nintr 99\n"
        with patch("builtins.open", mock_open(read_data=sample)):
            result = dashboard._cpu_times()
        self.assertEqual(result, {"cpu": (112, 100), "cpu3": (56, 50)})

    def test_load_uses_deltas_instead_of_lifetime_totals(self):
        self.assertEqual(dashboard._cpu_percent((1000, 800), (1100, 850)), 50.0)
        self.assertEqual(dashboard._cpu_percent((2000, 1500), (2300, 1799)), .3)

    def test_full_busy_and_idle_boundaries(self):
        self.assertEqual(dashboard._cpu_percent((1000, 800), (1100, 800)), 100.0)
        self.assertEqual(dashboard._cpu_percent((1000, 800), (1100, 900)), 0.0)

    def test_first_sample_counter_reset_and_inconsistent_idle_are_unknown(self):
        for previous, current in [(None, (100, 80)), ((1000, 800), (1000, 800)), ((1000, 800), (50, 10)), ((1000, 800), (1100, 790)), ((1000, 800), (1100, 950))]:
            with self.subTest(previous=previous, current=current):
                self.assertIsNone(dashboard._cpu_percent(previous, current))

    def test_different_cores_can_show_independent_loads(self):
        previous = {"cpu0": (1000, 800), "cpu1": (1000, 800), "cpu2": (1000, 800), "cpu3": (1000, 800)}
        current = {"cpu0": (1100, 800), "cpu1": (1100, 900), "cpu2": (1100, 850), "cpu3": (1100, 825)}
        measured = [dashboard._cpu_percent(previous[name], current[name]) for name in previous]
        self.assertEqual(measured, [100.0, 0.0, 50.0, 75.0])


if __name__ == "__main__":
    unittest.main()
