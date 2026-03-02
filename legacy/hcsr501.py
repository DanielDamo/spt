import lgpio
import time
from datetime import datetime

class hcsr501:
    def __init__(self, interval):
        self.chip = 0
        self.pin = 27
        self.motionlog = "logs/motion.log"
        self.stopped = False
        self.interval = interval


    def start(self):
        chip = lgpio.gpiochip_open(self.chip)
        lgpio.gpio_claim_input(chip, self.pin)
        while not self.stopped:
            motion = lgpio.gpio_read(chip, self.pin)
            
            if motion:
                with open(self.motionlog, "a") as f:
                    f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

            time.sleep(self.interval)
    

    def stop(self):
        self.stopped = True


    def format(self):
        with open(self.motionlog, "w") as f:
            f.write("time\n")