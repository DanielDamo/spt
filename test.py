import RPi.GPIO as GPIO
import time

B1_PIN = 16
B1_LED = 26

B2_PIN = 5
B2_LED = 6

GPIO.setmode(GPIO.BCM)

# Button 1
GPIO.setup(B1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(B1_LED, GPIO.OUT)

# Button 2
GPIO.setup(B2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(B2_LED, GPIO.OUT)


def on_press_1(channel):
    GPIO.output(B1_LED, 1)
    print("1")
    time.sleep(1)
    GPIO.output(B1_LED, 0)

def on_press_2(channel):
    GPIO.output(B2_LED, 0)
    print("2")
    time.sleep(1)
    GPIO.output(B2_LED, 1)

GPIO.add_event_detect(B1_PIN, GPIO.FALLING, callback=on_press_1, bouncetime=150)
GPIO.add_event_detect(B2_PIN, GPIO.FALLING, callback=on_press_2, bouncetime=150)


print("Press button")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    GPIO.cleanup()