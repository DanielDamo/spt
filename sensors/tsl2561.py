import board
import time
import busio
import os
from datetime import datetime
import adafruit_tsl2561


SLEEP_SLICE = 1.0 #TODO: add to constants file

class tsl2561:
    def __init__(self, interval):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor = adafruit_tsl2561.TSL2561(self.i2c, address=0x39)
        self.interval = interval

        # Daily log directory
        self.lightdir = os.path.join("logs", "light")
        os.makedirs(self.lightdir, exist_ok=True)

        self.stopped = False

    def start(self):
        while not self.stopped:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H:%M:%S")

            light = self.sensor.lux
            if light is None:
                light = 0

            path = os.path.join(self.lightdir, f"{date_str}.csv")
            self._append(path, time_str, light)

            # Sleep in small steps so stop() reacts quickly
            for _ in range(int(self.interval / SLEEP_SLICE)):
                if self.stopped:
                    break
                time.sleep(SLEEP_SLICE)

    def _append(self, path, t, value):
        new_file = not os.path.exists(path)
        with open(path, "a") as f:
            if new_file:
                f.write("time,light\n")
            f.write(f"{t},{value}\n")

    def stop(self):
        self.stopped = True