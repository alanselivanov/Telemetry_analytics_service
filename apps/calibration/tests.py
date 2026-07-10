from django.test import SimpleTestCase

from .parser import CalibrationParseError, parse_calibration_text


class CalibrationParserTests(SimpleTestCase):
    def test_parse_semicolon_table(self):
        grid = parse_calibration_text("0;0;0\n40;2;10\n80;6;17\n")

        self.assertEqual(grid.sensor_count, 2)
        self.assertEqual(len(grid.rows), 3)
        self.assertEqual(grid.rows[-1].sensor_code, 80)
        self.assertEqual(grid.rows[-1].tank_litres, (6.0, 17.0))
        self.assertEqual(grid.sensor_curves[0].points[1], (40, 2.0))
        self.assertEqual(grid.sensor_curves[1].points[2], (80, 17.0))

    def test_reject_inconsistent_tank_count(self):
        with self.assertRaises(CalibrationParseError):
            parse_calibration_text("0;0;0\n40;2\n")

    def test_reject_code_outside_sensor_range(self):
        with self.assertRaises(CalibrationParseError):
            parse_calibration_text("0;0\n4096;10\n")

    def test_builds_per_tank_curves_from_example_file(self):
        grid = parse_calibration_text("0;0;0\n400;639;798\n870;4095;4095\n")

        self.assertEqual(grid.sensor_curves[0].points[-1], (870, 4095.0))
        self.assertEqual(grid.sensor_curves[1].points[-1], (870, 4095.0))
