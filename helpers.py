import datetime as dt
from enum import Enum, auto
from datetime import datetime, date, timedelta
import os

###################################
#            HELPERS              #
###################################
class State(Enum):
    BOOTING = auto()
    BLUETOOTH_NOT_CONNECTED = auto()
    BLUETOOTH_CONNECTING = auto()
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


def save_persistent(state: dict, file=STATE_FILE):
    with open(file, "w") as f:
        for key, val in state.items():
            f.write(f"{key}: {val}\n")


def load_persistent(file=STATE_FILE):
    state = {}
    with open(file, "r") as f:
        for line in f:
            key, val = line.split(":")

            state[key.strip()] = val.strip()
    
    return state



def _read_daily_csv_timeseries(directory: str, days: int):
    t_all = []
    v_all = []

    # Make directory absolute so "where I ran python from" doesn't matter
    directory = os.path.abspath(directory)

    today = date.today()

    for i in range(days):
        day = today - timedelta(days=i)

        path = os.path.join(directory, f"{day:%Y-%m-%d}.csv")

        if path is None:
            continue

        with open(path, "r") as f:
            header = f.readline().strip()

            for lineno, line in enumerate(f, start=2):
                line = line.strip()
                if not line:
                    continue

                parts = line.split(",")
                t_str = parts[0].strip()
                v_str = parts[1].strip()

                # Accept HH:MM:SS or HH:MM
                dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        dt = datetime.strptime(f"{day:%Y-%m-%d} {t_str}", fmt)
                        break
                    except ValueError:
                        pass

                try:
                    val = float(v_str)
                except ValueError:
                    continue

                t_all.append(dt)
                v_all.append(val)

    # Sort by time
    if t_all:
        pairs = sorted(zip(t_all, v_all), key=lambda x: x[0])
        t_all, v_all = map(list, zip(*pairs))

    return t_all, v_all