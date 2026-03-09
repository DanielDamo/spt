import os
import threading
from datetime import datetime

import board
import busio
import adafruit_tsl2561


class TSL2561:
    """
    Reads lux values from a TSL2561 light sensor and logs them to daily CSV
    files.  Runs on its own daemon thread.

    Call start() once to begin logging.  Call update_interval() at any time
    to change the sampling period — the current sleep is interrupted immediately
    so the new interval takes effect on the very next cycle.
    """

    def __init__(self, interval: float) -> None:
        self._i2c    = busio.I2C(board.SCL, board.SDA)
        self._sensor = adafruit_tsl2561.TSL2561(self._i2c, address=0x39)
        self._interval = interval

        self._light_dir = os.path.join("logs", "light")
        os.makedirs(self._light_dir, exist_ok=True)

        self._stop_evt = threading.Event()
        self._wake_evt = threading.Event()
        self._thread   = threading.Thread(target=self._run, daemon=True, name="tsl2561")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the logging thread."""
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop and block until it exits (max 5 s)."""
        self._stop_evt.set()
        self._wake_evt.set()
        self._thread.join(timeout=5)

    def update_interval(self, interval: float) -> None:
        """Change the sampling interval.  Takes effect after the current reading."""
        self._interval = interval
        self._wake_evt.set()

    def get_log_dirs(self) -> list:
        return [self._light_dir]

    @property
    def sensor(self):
        """Direct access to the underlying Adafruit sensor object."""
        return self._sensor

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            self._read_and_log()
            self._wake_evt.wait(timeout=self._interval)
            self._wake_evt.clear()

    def _read_and_log(self) -> None:
        now      = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        lux = self._sensor.lux
        if lux is None:
            lux = 0.0

        self._append(os.path.join(self._light_dir, f"{date_str}.csv"), time_str, lux)

    @staticmethod
    def _append(path: str, timestamp: str, value: float) -> None:
        new_file = not os.path.exists(path)
        with open(path, "a") as f:
            if new_file:
                f.write("time,light\n")
            f.write(f"{timestamp},{value}\n")