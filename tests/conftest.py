from datetime import date, timedelta
import numpy as np
import pandas as pd
import pytest

@pytest.fixture
def history_data():
    rows = []
    events = ["single", "field_out", "strikeout", "walk", "double", "field_out", "home_run", "field_out"]
    for game in range(70):
        for pa in range(3 + game % 3):
            event = events[(game + pa) % len(events)]
            tracked = event in {"single", "double", "home_run", "field_out"}
            rows.append({"batter": 99, "pitcher": 10, "game_pk": 1000 + game,
                "game_date": (date(2025, 4, 1) + timedelta(days=game)).isoformat(),
                "at_bat_number": pa + 1, "pitch_number": 1, "events": event,
                "p_throws": "R" if game % 3 else "L", "pitch_type": "FF" if pa % 2 else "SL",
                "release_speed": 94.0, "launch_speed": 96.0 if tracked else np.nan,
                "launch_angle": 15.0 if tracked else np.nan, "launch_speed_angle": 6 if tracked else np.nan,
                "estimated_ba_using_speedangle": .35 if tracked else np.nan})
    return pd.DataFrame(rows)
