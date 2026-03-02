import threading
from dataclasses import dataclass
from datetime import datetime, date
import os
import pandas as pd
import time
os.environ["MPLBACKEND"] = "Agg"

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from sensors.bme280 import bme280
from sensors.tsl2561 import tsl2561
import display.waveshare as waveshare
import RPi.GPIO as GPIO
import time
import queue
import helpers
from helpers import State


PIXELS_X = 480
PIXELS_Y = 800
DPI = 100


B1_PIN = 16
B1_LED = 26

B2_PIN = 5
B2_LED = 6

BLINK_DT = 0.25
HOLD_TIME = 2
GRAPH_UPDATE_INTERVAL = 15 * 60
INTERVAL = 1

    

class PiController:
    def __init__(self, upload_folder="assets/images"):
        # State and commands
        self._state = State.BOOTING
        self._state_cv = threading.Condition()
        self._stop_evt = threading.Event()
        self._cmd_q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.shutdown_requested = threading.Event()

        # Buttons
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(B1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(B1_LED, GPIO.OUT)
        GPIO.setup(B2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(B2_LED, GPIO.OUT)

        GPIO.add_event_detect(B1_PIN, GPIO.BOTH, callback=self._on_b1_edge, bouncetime=80)
        GPIO.add_event_detect(B2_PIN, GPIO.BOTH, callback=self._on_b2_edge, bouncetime=80)
        
        self._b1_pressed_at = None
        self._b1_lock = threading.Lock()
        self._b2_pressed_at = None
        self._b2_lock = threading.Lock()

        # Sensors
        self.bme = bme280(INTERVAL)
        self.tsl = tsl2561(INTERVAL)

        # Display
        try:
            self.epd = waveshare.EPD()
            self.epd.init()
            self.display_lock = threading.Lock()        
        except IOError as e:
            logging.info(e)
        except KeyboardInterrupt:    
            waveshare.epdconfig.module_exit(cleanup=True)
            exit()

        # Directories
        self.upload_folder = upload_folder
        os.makedirs(self.upload_folder, exist_ok=True)
        self.logs_folder = "logs"
        os.makedirs(self.logs_folder, exist_ok=True)

        # Start Threads
        self._cmd_thread = threading.Thread(target=self._cmd_worker, daemon=True)
        self._led_thread = threading.Thread(target=self._led_worker, daemon=True)
        self._bme_thread = threading.Thread(target=self.bme.start, daemon=True)
        self._tsl_thread = threading.Thread(target=self.tsl.start, daemon=True)
        self._cmd_thread.start()
        self._led_thread.start()
        self._bme_thread.start()
        self._tsl_thread.start()

        self.set_state(State.BOOTING)

        # Load state
        self.persistent = helpers.load_persistent()

        self.set_state(State.READY)


    # Stop all sensors, buttons and display and save
    def stop(self):
        print("[PiController] Stopping.")
        # Ask BLE server to stop (if it exists)
        if hasattr(self, "ble_stop") and callable(self.ble_stop):
            try:
                self.ble_stop()
            except Exception as e:
                print("[PiController] ble_stop failed:", e)

        self._stop_evt.set()
        self._cmd_q.put(("stop", None))

        self._cmd_thread.join(timeout=1.0)
        self._led_thread.join(timeout=1.0)
        self.bme.stop()
        self.tsl.stop()

        waveshare.epdconfig.module_exit(cleanup=True)
        self._apply_leds_static(0, 0)
        GPIO.cleanup()
        helpers.save_persistent(self.persistent)

        print("[PiController] Stopped.")
        
    
    def _on_b1_edge(self, channel) -> None:
        level = GPIO.input(B1_PIN)

        if level == 0:
            # pressed (falling)
            with self._b1_lock:
                self._b1_pressed_at = time.monotonic()
        else:
            # released (rising)
            with self._b1_lock:
                t0 = self._b1_pressed_at
                self._b1_pressed_at = None

            if t0 is None:
                return

            held = time.monotonic() - t0
            if held >= HOLD_TIME:
                self._cmd_q.put(("hold1", None))
            else:
                self._cmd_q.put(("press1", None))


    def _on_b2_edge(self, channel) -> None:
        level = GPIO.input(B2_PIN)

        if level == 0:
            # pressed
            with self._b2_lock:
                self._b2_pressed_at = time.monotonic()
        else:
            # released
            with self._b2_lock:
                t0 = self._b2_pressed_at
                self._b2_pressed_at = None

            if t0 is None:
                return

            held = time.monotonic() - t0
            if held >= HOLD_TIME:
                self._cmd_q.put(("hold2", None))
            else:
                self._cmd_q.put(("press2", None))

    

    def _cmd_worker(self) -> None:
        while not self._stop_evt.is_set():
            try:
                cmd, payload = self._cmd_q.get(timeout=0.5)
            except queue.Empty:
                continue

            if cmd == "stop":
                return

            try:
                if cmd == "press1":
                    # Toggle display mode safely here (heavy work allowed)
                    if self.persistent.get("display_mode") == "graph":
                        self.display_image(self.persistent.get("last_image", "lake.jpg"))
                    else:
                        self.display_graph()

                elif cmd == "hold1":
                    self.shutdown_requested.set()

                elif cmd == "press2":
                    # TODO: something
                    pass
                
                elif cmd == "hold2":
                    # REDO bluetooth
                    if hasattr(self, "ble_reset") and callable(self.ble_reset):
                        self.ble_reset()
                    else:
                        print("[PiController] No ble_reset hook wired up")

                elif cmd == "set_state":
                    self.set_state(payload)  # payload is a State

                elif cmd == "display_image":
                    self.display_image(str(payload))

                elif cmd == "display_graph":
                    self.display_graph()

            except Exception as e:
                # If something blows up, ERROR
                print("[PiController] Command error:", e)
                self.set_state(State.ERROR)


    def _apply_leds_static(self, led1: int, led2: int) -> None:
        if led1 is not None:
            GPIO.output(B1_LED, 1 if led1 else 0)
        if led2 is not None:
            GPIO.output(B2_LED, 1 if led2 else 0)

    def _led_worker(self) -> None:
        last_state = None
        phase = 0          # used for alternation/blinking
        next_tick = time.monotonic()

        while not self._stop_evt.is_set():
            with self._state_cv:
                state = self._state

            # If state changed, reset phase and apply immediately
            if state != last_state:
                phase = 0
                next_tick = time.monotonic()
                last_state = state

                # Apply immediate “base” output so it looks responsive
                if state == State.BOOTING:
                    # start with LED1 on, LED2 off (then alternate)
                    self._apply_leds_static(1, 0)
                    print("[STATE]: Booting")
                elif state == State.BLUETOOTH_NOT_CONNECTED:
                    self._apply_leds_static(None, 1)   # LED2 on
                    print("[STATE]: Bluetooth not connected")
                elif state == State.BLUETOOTH_CONNECTED:
                    self._apply_leds_static(None, 0)   # LED2 off
                    print("[STATE]: Bluetooth connected")
                elif state == State.BLUETOOTH_CONNECTING:
                    self._apply_leds_static(None, 1)   # start LED2 on then blink
                    print("[STATE]: Bluetooth connecting")
                elif state == State.READY:
                    self._apply_leds_static(0, None)   # LED1 off
                    print("[STATE]: Ready")
                elif state == State.BUSY:
                    self._apply_leds_static(1, None)   # start LED1 on then blink
                    print("[STATE]: Busy")
                elif state == State.ERROR:
                    self._apply_leds_static(1, 1)   # both on
                    print("[STATE]: Error")

            now = time.monotonic()

            # Handle flashing states
            if state == State.BOOTING:
                # Alternate every 250ms: LED1 then LED2 then LED1...
                if now >= next_tick:
                    phase ^= 1
                    if phase == 0:
                        self._apply_leds_static(1, 0)
                    else:
                        self._apply_leds_static(0, 1)
                    next_tick = now + BLINK_DT

            elif state == State.BLUETOOTH_CONNECTING:
                # LED2 blinks every 250ms
                if now >= next_tick:
                    phase ^= 1
                    self._apply_leds_static(None, phase)  # LED1 off, LED2 toggles
                    next_tick = now + BLINK_DT

            elif state == State.BUSY:
                # LED1 blinks every 250ms
                if now >= next_tick:
                    phase ^= 1
                    self._apply_leds_static(phase, None)  # LED1 toggles, LED2 off
                    next_tick = now + BLINK_DT

            time.sleep(0.02)





    def display_image(self, img_name):
        try:
            with self.display_lock:
                self.set_state(State.BUSY)
                self.epd.display(self.epd.getbuffer(os.path.join(self.upload_folder, img_name)))
                self.persistent["display_mode"] = "image"
                self.persistent["last_image"] = img_name
                self.set_state(State.READY)
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            exit()
    
    def display_graph(self):
        try:
            with self.display_lock:
                self.set_state(State.BUSY)
                self.generate_plot()
                self.epd.display(self.epd.getbuffer(f"assets/graph.png"))
                self.persistent["display_mode"] = "graph"
                self.set_state(State.READY)
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            exit()

    # Show current values
    def get_sensor_values(self):
        return {
            "temperature": self.bme.sensor.temperature,
            "humidity": self.bme.sensor.humidity,
            "pressure": self.bme.sensor.pressure,
            "light": self.tsl.sensor.lux
        }
    
    # Show all available images
    def list_uploaded_photos(self):
        return os.listdir(self.upload_folder)



    # Generate plot
    def generate_plot(self, days: int = 1) -> None:
        """
        days:
        1  = today only
        7  = last week
        14 = last two weeks
        """
        hum_t, hum_v   = helpers._read_daily_csv_timeseries(os.path.join("logs", "humidity"), days)
        light_t, light_v = helpers._read_daily_csv_timeseries(os.path.join("logs", "light"), days)
        pres_t, pres_v = helpers._read_daily_csv_timeseries(os.path.join("logs", "pressure"), days)
        temp_t, temp_v = helpers._read_daily_csv_timeseries(os.path.join("logs", "temp"), days)

        fig, axes = plt.subplots(
            4, 1,
            figsize=(PIXELS_X / DPI, PIXELS_Y / DPI),
            dpi=DPI,
            sharex=True
        )
        ax_hum, ax_light, ax_pres, ax_temp = axes

        ax_hum.plot(hum_t, hum_v)
        ax_hum.set_title("Humidity")

        ax_light.plot(light_t, light_v)
        ax_light.set_title("Light Level")

        ax_pres.plot(pres_t, pres_v)
        ax_pres.set_title("Pressure")

        ax_temp.plot(temp_t, temp_v)
        ax_temp.set_title("Temperature")

        plt.tight_layout()
        os.makedirs("assets", exist_ok=True)
        fig.savefig(os.path.join("assets", "graph.png"))
        plt.close(fig)

    def set_state(self, new_state: State) -> None:
        with self._state_cv:
            if new_state != self._state:
                self._state = new_state
                self._state_cv.notify_all()

    def get_state(self) -> State:
        with self._state_cv:
            return self._state




