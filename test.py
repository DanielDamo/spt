# import time
# import board
# import busio
# import adafruit_tsl2561

# # Set up I2C
# i2c = busio.I2C(board.SCL, board.SDA)

# # Create TSL2561 sensor object
# sensor = adafruit_tsl2561.TSL2561(i2c)

# # Optional: Change gain or integration time
# sensor.gain = 0  # 0=1X, 1=16X
# sensor.integration_time = 1  # 0=13.7ms, 1=101ms, 2=402ms

# # Read light value
# while True:
#     lux = sensor.lux
#     print(f"Light: {lux} lux")
#     time.sleep(1)

import lgpio
import time

CHIP = 0
PIR_PIN = 17

chip = lgpio.gpiochip_open(CHIP)
lgpio.gpio_claim_input(chip, PIR_PIN)

while True:
    if lgpio.gpio_read(chip, PIR_PIN):
        print("!")
    else:
        print(" F")
    time.sleep(0.2)
