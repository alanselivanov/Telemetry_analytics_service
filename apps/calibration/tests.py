from django.test import SimpleTestCase

from .parser import CalibrationParseError, parse_calibration_text


class CalibrationParserTests(SimpleTestCase):
    def test_parse_semicolon_table(self):
        grid = parse_calibration_text("0;0;0\n40;2;10\n80;6;17\n")

        self.assertEqual(grid.sensor_count, 2)
        self.assertEqual(len(grid.rows), 3)
        self.assertEqual(grid.rows[-1].sensor_codes, (6, 17))

    def test_reject_inconsistent_sensor_count(self):
        with self.assertRaises(CalibrationParseError):
            parse_calibration_text("0;0;0\n40;2\n")

    def test_reject_code_outside_sensor_range(self):
        with self.assertRaises(CalibrationParseError):
            parse_calibration_text("0;0\n40;4096\n")
