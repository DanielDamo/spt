import board
import time
import busio
import adafruit_tsl2561

class tsl2561:
    def __init__(self):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.sensor = adafruit_tsl2561.TSL2561(self.i2c, address=0x39)

        self.lightlog = "logs/light.log"
        self.stopped = False


    def start(self):
        while not self.stopped:
            light = self.sensor.lux

            if light is None:
                light = 0

            with open(self.lightlog, "a") as f:
                f.write(f"{time.time()},{light}\n")

            time.sleep(1)
    

    def stop(self):
        self.stopped = True


    def format(self):
        with open(self.lightlog, "w") as f:
            f.write("time,light\n")
