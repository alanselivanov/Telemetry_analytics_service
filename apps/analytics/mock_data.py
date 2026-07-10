
from __future__ import annotations

from datetime import datetime, timezone


MOCK_TERMINAL_ID = 999000001
MOCK_VEHICLE_NAME = "Mock ТС: аномалии ДУТ"
MOCK_CALIBRATION_NAME = "Mock тарировка ДУТ"

MOCK_CALIBRATION = """\
0;0;0
500;100;100
1000;200;200
1500;250;250
2000;300;300
2500;350;350
3000;400;400
3500;420;420
4095;439;439
"""


def build_mock_points() -> list[dict]:
    start = int(datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc).timestamp())
    points: list[dict] = []
    code_1 = 2300
    code_2 = 2300

    for index in range(80):
        ts = start + index * 300
        speed = 12 if index < 25 or 48 <= index < 70 else 0

        if index < 20:
            code_1 -= 8
            code_2 -= 8
        elif 20 <= index < 28:
            speed = 15
            code_1 = 2140
            code_2 = 2140
        elif 32 <= index < 38:
            speed = 0
            code_1 += 85
            code_2 += 85
        elif index == 50:
            code_1 -= 420
            code_2 -= 420
        elif 60 <= index < 68:
            speed = 0
            code_1 = 1600 if index % 2 else 2600
            code_2 = 1700 if index % 2 else 2700
        else:
            code_1 -= 5
            code_2 -= 5

        points.append(
            {
                "EVENT_DATE": ts,
                "SPEED": speed,
                "LLS_CODE": [code_1, code_2, 0, 0, 0, 0],
            }
        )

    return points