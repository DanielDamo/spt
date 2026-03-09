import datetime as dt
from enum import Enum, auto
from datetime import datetime, date, timedelta
import os

###################################
#            HELPERS              #
###################################
class State(Enum):
    BOOTING = auto()
    BLUETOOTH_ON = auto()
    BLUETOOTH_OFF = auto()
    BLUETOOTH_CONNECTED = auto()
    READY = auto()
    BUSY = auto()
    ERROR = auto()


STATE_FILE = "persistent.dat"

state = {
    "display_mode": 0       # 0 = Graph, 1 = picture
}

config = {
    "sensor_interval": 1    # Seconds
}


PERSISTENT_DEFAULTS = {
    "display_mode":               "graph",
    "sensor_interval":            "600",
    "auto_update_graph":          "false",
    "auto_update_graph_interval": "600",
    "graph_time_scale":           "1",
    "auto_delete_old_date":       "false",
    "auto_delete_after":          "-1",
}


def save_persistent(state: dict, file=STATE_FILE):
    with open(file, "w") as f:
        for key, val in state.items():
            f.write(f"{key}: {val}\n")


def load_persistent(file=STATE_FILE):
    state = dict(PERSISTENT_DEFAULTS)   # seed with defaults first
    try:
        with open(file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":", 1)   # limit=1 so time-like values don't break
                if len(parts) == 2:
                    v = parts[1].strip()
                    if v in ("True", "False"):   # normalise Python booleans to lowercase
                        v = v.lower()
                    state[parts[0].strip()] = v
    except FileNotFoundError:
        pass   # first run — defaults are fine
    return state



def _read_daily_csv_timeseries(directory: str, days: int):
    """
    Read sensor CSVs from `directory` and return (timestamps, values).

    days:  number of past days to read (1 = today only, 7 = last week, etc.)
           pass -1 to read every CSV file in the directory (all time).
    """
    t_all = []
    v_all = []

    directory = os.path.abspath(directory)

    if days == -1:
        # Collect every file that matches the YYYY-MM-DD.csv pattern
        try:
            all_files = os.listdir(directory)
        except FileNotFoundError:
            return t_all, v_all

        day_files = []
        for filename in all_files:
            if not filename.endswith(".csv"):
                continue
            stem = filename[:-4]   # strip .csv
            try:
                day = date.fromisoformat(stem)
                day_files.append((day, os.path.join(directory, filename)))
            except ValueError:
                continue   # skip files that don't match YYYY-MM-DD
    else:
        today = date.today()
        day_files = [
            (today - timedelta(days=i),
             os.path.join(directory, f"{(today - timedelta(days=i)):%Y-%m-%d}.csv"))
            for i in range(days)
        ]

    for day, path in day_files:
        if not os.path.exists(path):
            continue

        with open(path, "r") as f:
            f.readline()   # skip header

            for line in f:
                line = line.strip()
                if not line:
                    continue

                parts = line.split(",")
                if len(parts) < 2:
                    continue

                t_str = parts[0].strip()
                v_str = parts[1].strip()

                parsed_dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        parsed_dt = datetime.strptime(f"{day:%Y-%m-%d} {t_str}", fmt)
                        break
                    except ValueError:
                        pass

                if parsed_dt is None:
                    continue

                try:
                    val = float(v_str)
                except ValueError:
                    continue

                t_all.append(parsed_dt)
                v_all.append(val)

    if t_all:
        pairs = sorted(zip(t_all, v_all), key=lambda x: x[0])
        t_all, v_all = map(list, zip(*pairs))

    return t_all, v_all