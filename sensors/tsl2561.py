import board
import time
import busio
import adafruit_tsl2561

from datetime import datetime

class tsl2561:
    def __init__(self, interval):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor = adafruit_tsl2561.TSL2561(self.i2c, address=0x39)
        self.interval = interval
        self.lightlog = "logs/light.log"
        self.stopped = False


    def start(self):
        while not self.stopped:
            light = self.sensor.lux

            if light is None:
                light = 0

            with open(self.lightlog, "a") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{light}\n")

            time.sleep(self.interval)
    

    def stop(self):
        self.stopped = True


    def format(self):
        with open(self.lightlog, "w") as f:
            f.write("time,light\n")
