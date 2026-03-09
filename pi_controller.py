import os
import queue
import threading
import time

os.environ["MPLBACKEND"] = "Agg"

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import RPi.GPIO as GPIO

import helpers
from helpers import State
from sensors.bme280 import BME280
from sensors.tsl2561 import TSL2561
import display.waveshare as waveshare


# ------------------------------------------------------------------
# Display constants
# ------------------------------------------------------------------
PIXELS_X = 480
PIXELS_Y = 800
DPI      = 100

# ------------------------------------------------------------------
# Button / LED GPIO pin assignments (BCM numbering)
# ------------------------------------------------------------------
B1_PIN = 16
B1_LED = 26
B2_PIN = 5
B2_LED = 6

# ------------------------------------------------------------------
# Timing constants
# ------------------------------------------------------------------
BLINK_DT  = 0.25   # seconds between LED blink transitions
HOLD_TIME = 2.0    # seconds a button must be held to register a long-press

# ------------------------------------------------------------------
# Default sensor sampling interval (seconds)
# ------------------------------------------------------------------
DEFAULT_SENSOR_INTERVAL = 600


class PiController:
    """
    Central controller for the SPT Pi.

    Manages:
    - GPIO buttons and LEDs
    - BME280 and TSL2561 sensor logging threads
    - E-ink display
    - Command queue (from BLE app and physical buttons)
    - Persistent settings (loaded on start, saved on graceful shutdown)

    BLE hooks (ble_send, ble_reset, ble_stop, ble_start) are wired in
    externally by main.py after the BLE server thread is started.
    """

    def __init__(self, upload_folder: str = os.path.join("assets", "images")) -> None:
        # ------------------------------------------------------------------
        # State
        # ------------------------------------------------------------------
        self._state    = State.BOOTING
        self._state_cv = threading.Condition()
        self._stop_evt = threading.Event()
        self._cmd_q: queue.Queue = queue.Queue()
        self.shutdown_requested  = threading.Event()

        # ------------------------------------------------------------------
        # GPIO — buttons with debounce, LEDs as outputs
        # ------------------------------------------------------------------
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(B1_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
        GPIO.setup(B1_LED, GPIO.OUT)
        GPIO.setup(B2_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
        GPIO.setup(B2_LED, GPIO.OUT)

        GPIO.add_event_detect(B1_PIN, GPIO.BOTH, callback=self._on_b1_edge, bouncetime=80)
        GPIO.add_event_detect(B2_PIN, GPIO.BOTH, callback=self._on_b2_edge, bouncetime=80)

        self._b1_pressed_at: float | None = None
        self._b1_lock = threading.Lock()
        self._b2_pressed_at: float | None = None
        self._b2_lock = threading.Lock()

        # ------------------------------------------------------------------
        # Persistent settings (loaded before sensors so interval is available)
        # ------------------------------------------------------------------
        self.persistent = helpers.load_persistent()
        sensor_interval = int(self.persistent.get("sensor_interval", DEFAULT_SENSOR_INTERVAL))

        def _on_sensor_reading():
            # Only uses bme but they should match the tsl too
            if (self.persistent.get("display_mode") == "graph"
                    and self.persistent.get("auto_update_graph", "false") == "true"):
                self._cmd_q.put(("display_graph", None))

        # ------------------------------------------------------------------
        # Sensors
        # ------------------------------------------------------------------
        self.bme = BME280(sensor_interval, on_reading=self._on_sensor_reading)
        self.tsl = TSL2561(sensor_interval)

        # ------------------------------------------------------------------
        # Display
        # ------------------------------------------------------------------
        self.upload_folder = upload_folder
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs("logs", exist_ok=True)

        try:
            self.epd = waveshare.EPD()
            self.epd.init()
            self.display_lock = threading.Lock()
        except IOError as e:
            print(f"[PiController] Display init failed: {e}")
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            raise

        # ------------------------------------------------------------------
        # Worker threads
        # ------------------------------------------------------------------
        self._cmd_thread = threading.Thread(target=self._cmd_worker, daemon=True, name="cmd-worker")
        self._led_thread = threading.Thread(target=self._led_worker, daemon=True, name="led-worker")

        self._cmd_thread.start()
        self._led_thread.start()
        self.bme.start()
        self.tsl.start()

        self.set_state(State.READY)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """
        Gracefully shut down all threads, save settings, and release hardware.
        Called by main.py when shutdown_requested is set.
        """
        print("[PiController] Stopping.")

        if hasattr(self, "ble_stop") and callable(self.ble_stop):
            try:
                self.ble_stop()
            except Exception as e:
                print(f"[PiController] ble_stop failed: {e}")

        self._stop_evt.set()
        self._cmd_q.put(("stop", None))   # unblock _cmd_worker if it is waiting

        self._cmd_thread.join(timeout=2.0)
        self._led_thread.join(timeout=2.0)
        self.bme.stop()
        self.tsl.stop()

        waveshare.epdconfig.module_exit(cleanup=True)
        self._apply_leds_static(0, 0)
        GPIO.cleanup()
        helpers.save_persistent(self.persistent)

        print("[PiController] Stopped.")

    # ------------------------------------------------------------------
    # Button edge handlers (called from GPIO interrupt thread)
    # ------------------------------------------------------------------

    def _on_b1_edge(self, channel: int) -> None:
        level = GPIO.input(B1_PIN)
        if level == 0:
            # Falling edge — button pressed
            with self._b1_lock:
                self._b1_pressed_at = time.monotonic()
        else:
            # Rising edge — button released
            with self._b1_lock:
                t0 = self._b1_pressed_at
                self._b1_pressed_at = None

            if t0 is None:
                return

            held = time.monotonic() - t0
            if held >= HOLD_TIME:
                self._cmd_q.put(("shutdown", None))
            else:
                self._cmd_q.put(("toggle_screen", None))

    def _on_b2_edge(self, channel: int) -> None:
        level = GPIO.input(B2_PIN)
        if level == 0:
            with self._b2_lock:
                self._b2_pressed_at = time.monotonic()
        else:
            with self._b2_lock:
                t0 = self._b2_pressed_at
                self._b2_pressed_at = None

            if t0 is None:
                return

            held = time.monotonic() - t0
            if held >= HOLD_TIME:
                self._cmd_q.put(("bluetooth_toggle", None))
            else:
                self._cmd_q.put(("bluetooth_reset", None))

    # ------------------------------------------------------------------
    # Command worker
    # ------------------------------------------------------------------

    def _cmd_worker(self) -> None:
        """
        Processes commands from _cmd_q on a dedicated thread.
logs/humidity logs/light logs/pressure logs/temp
        - strings "op:payload"     from the BLE app
        """
        while not self._stop_evt.is_set():
            # Do not process commands while the display is busy
            if self.get_state() == State.BUSY:
                time.sleep(0.05)
                continue

            try:
                command = self._cmd_q.get(timeout=0.5)
            except queue.Empty:
                continue

            # Parse command
            if isinstance(command, tuple):
                op, payload = command
            elif isinstance(command, str):
                parts = command.split(":", 1)
                if len(parts) < 2:
                    print(f"[PiController] Malformed command (no colon): {command!r}")
                    continue
                op, payload = parts[0], parts[1]
            else:
                continue

            if op == "stop":
                return

            try:
                self._handle_command(op, payload)
            except Exception as e:
                print(f"[PiController] Command error ({op!r}): {e}")
                self.set_state(State.ERROR)

    def _handle_command(self, op: str, payload) -> None:
        """Dispatch a single command op to the appropriate handler."""

        # ---- BLE app commands ----------------------------------------

        if op == "request" and payload == "config":
            self.ble_send(self.get_config())
        
        elif op == "request" and payload == "images":
            images = self.list_uploaded_images()
            self.ble_send("images:" + ",".join(images))

        elif op == "disconnect":
            self.ble_reset()

        elif op == "mode":
            val = payload.strip()
            if val == "graph":
                self.display_graph()
            else:
                self.display_image(self.persistent.get("last_image", "lake.jpg"))

        elif op == "sensorInterval":
            interval = int(payload.strip())
            self.persistent["sensor_interval"] = str(interval)
            self.bme.update_interval(interval)
            self.tsl.update_interval(interval)

        elif op == "graphAutoUpdate":
            self.persistent["auto_update_graph"] = payload.strip().lower()

        elif op == "graphScale":
            self.persistent["graph_time_scale"] = payload.strip()
        
        elif op == "imageSet" and isinstance(payload, str):
            img = payload.strip()
            self.display_image(img)
        
        elif op == "imageUpload":
            # TODO image upload 
            pass
        
        elif op == "deleteImage" and isinstance(payload, str):
            imageToDelete = os.path.join(self.upload_folder, payload)
            if os.path.exists(imageToDelete):
                os.remove(imageToDelete)
                print(f"[PiController] {payload} deleted.")
            else:
                print(f"[PiController] {payload} not found.") 
        
        elif op == "deleteAllImages":
            all_files = os.listdir(self.upload_folder)
            for file in all_files:
                os.remove(os.path.join(self.upload_folder, file))
                print(f"[PiController] {file} deleted.")

        elif op == "autoDeleteData":
            # TODO
            self.persistent["auto_delete_old_date"] = payload.strip().lower()

        elif op == "autoDelete":
            # TODO
            self.persistent["auto_delete_after"] = payload.strip()

        elif op == "downloadSensorData":
            # TODO
            pass

        
        elif op == "deleteAllData":
            all_log_dirs = self.bme.get_log_dirs() + self.tsl.get_log_dirs()
            print(all_log_dirs)
            for dir in all_log_dirs:
                all_files = os.listdir(dir)
                for file in all_files:
                    os.remove(os.path.join(dir, file))
                print(f"[PiController] {dir} emptied.")

        # ---- Internal / display commands -----------------------------

        elif op == "set_state":
            self.set_state(payload)

        elif op == "display_image":
            self.display_image(str(payload))

        elif op == "display_graph":
            self.display_graph()

        # ---- Button commands -----------------------------------------

        elif op == "toggle_screen":
            if self.persistent.get("display_mode") == "graph":
                self.display_image(self.persistent.get("last_image", "lake.jpg"))
            else:
                self.display_graph()

        elif op == "shutdown":
            self.shutdown_requested.set()

        elif op == "bluetooth_reset":
            if hasattr(self, "ble_reset") and callable(self.ble_reset):
                self.ble_reset()
            else:
                print("[PiController] No ble_reset hook wired up")

        elif op == "bluetooth_toggle":
            ble_running = getattr(self, "_ble_running", True)
            if ble_running:
                if hasattr(self, "ble_stop") and callable(self.ble_stop):
                    self.ble_stop()
                self._ble_running = False
            else:
                if hasattr(self, "ble_start") and callable(self.ble_start):
                    self.ble_start()
                self._ble_running = True

        else:
            print(f"[PiController] Unknown command: {op!r}")

    # ------------------------------------------------------------------
    # LED worker
    # ------------------------------------------------------------------

    def _apply_leds_static(self, led1: int | None, led2: int | None) -> None:
        if led1 is not None:
            GPIO.output(B1_LED, 1 if led1 else 0)
        if led2 is not None:
            GPIO.output(B2_LED, 1 if led2 else 0)

    def _led_worker(self) -> None:
        """Drives the two LEDs to reflect the current system state."""
        last_state = None
        phase      = 0
        next_tick  = time.monotonic()

        while not self._stop_evt.is_set():
            with self._state_cv:
                state = self._state

            if state != last_state:
                phase      = 0
                next_tick  = time.monotonic()
                last_state = state

                if state == State.BOOTING:
                    self._apply_leds_static(1, 0)
                    print("[STATE] Booting")
                elif state == State.BLUETOOTH_OFF:
                    self._apply_leds_static(None, 1)
                    print("[STATE] Bluetooth off")
                elif state == State.BLUETOOTH_CONNECTED:
                    self._apply_leds_static(None, 0)
                    print("[STATE] Bluetooth connected")
                elif state == State.BLUETOOTH_ON:
                    self._apply_leds_static(None, 1)
                    print("[STATE] Bluetooth on")
                elif state == State.READY:
                    self._apply_leds_static(0, None)
                    print("[STATE] Ready")
                elif state == State.BUSY:
                    self._apply_leds_static(1, None)
                    print("[STATE] Busy")
                elif state == State.ERROR:
                    self._apply_leds_static(1, 1)
                    print("[STATE] Error")

            now = time.monotonic()

            if state == State.BOOTING:
                if now >= next_tick:
                    phase ^= 1
                    self._apply_leds_static(1 - phase, phase)
                    next_tick = now + BLINK_DT

            elif state == State.BLUETOOTH_ON:
                if now >= next_tick:
                    phase ^= 1
                    self._apply_leds_static(None, phase)
                    next_tick = now + BLINK_DT

            elif state == State.BUSY:
                if now >= next_tick:
                    phase ^= 1
                    self._apply_leds_static(phase, None)
                    next_tick = now + BLINK_DT

            time.sleep(0.02)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def display_image(self, img_name: str) -> None:
        if not os.path.exists(os.path.join(self.upload_folder, img_name)):
            print(f"[PiController] {img_name} not found.")
            return 

        try:
            with self.display_lock:
                self.set_state(State.BUSY)
                self.epd.display(self.epd.getbuffer(os.path.join(self.upload_folder, img_name)))
                self.persistent["display_mode"] = "image"
                self.persistent["last_image"]   = img_name
                self.set_state(State.READY)
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            raise

    def display_graph(self) -> None:
        try:
            with self.display_lock:
                self.set_state(State.BUSY)
                self.generate_plot(days=int(self.persistent["graph_time_scale"]))
                self.epd.display(self.epd.getbuffer(os.path.join("assets", "graph.png")))
                self.persistent["display_mode"] = "graph"
                self.set_state(State.READY)
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            raise

    # ------------------------------------------------------------------
    # Sensor / data helpers
    # ------------------------------------------------------------------

    def get_sensor_values(self) -> dict:
        """Return the latest raw readings from all sensors."""
        return {
            "temperature": self.bme.sensor.temperature,
            "humidity":    self.bme.sensor.humidity,
            "pressure":    self.bme.sensor.pressure,
            "light":       self.tsl.sensor.lux,
        }

    def list_uploaded_images(self) -> list[str]:
        """Return a list of image filenames available on the device."""
        return os.listdir(self.upload_folder)

    def _on_sensor_reading(self) -> None:
        """
        Called by the BME280 thread after every successful reading.
        If the display is in graph mode and auto-update is enabled, queue a
        graph refresh.  Uses the command queue so the display lock is respected
        and we never block the sensor thread.
        """
        if (self.persistent.get("display_mode") == "graph"
                and self.persistent.get("auto_update_graph") == "true"):
            self._cmd_q.put(("display_graph", None))

    def generate_plot(self, days: int = 1) -> None:
        """
        Render a four-panel sensor graph and save it to assets/graph.png.

        days: number of past days to include (1 = today only).
        """
        hum_t,   hum_v   = helpers._read_daily_csv_timeseries(os.path.join("logs", "humidity"), days)
        light_t, light_v = helpers._read_daily_csv_timeseries(os.path.join("logs", "light"),    days)
        pres_t,  pres_v  = helpers._read_daily_csv_timeseries(os.path.join("logs", "pressure"), days)
        temp_t,  temp_v  = helpers._read_daily_csv_timeseries(os.path.join("logs", "temp"),     days)

        fig, axes = plt.subplots(
            4, 1,
            figsize=(PIXELS_X / DPI, PIXELS_Y / DPI),
            dpi=DPI,
            sharex=True,
        )
        ax_hum, ax_light, ax_pres, ax_temp = axes

        ax_hum.plot(hum_t,   hum_v)
        ax_hum.set_title("Humidity")

        ax_light.plot(light_t, light_v)
        ax_light.set_title("Light Level")

        ax_pres.plot(pres_t,  pres_v)
        ax_pres.set_title("Pressure")

        ax_temp.plot(temp_t,  temp_v)
        ax_temp.set_title("Temperature")

        plt.tight_layout()
        os.makedirs("assets", exist_ok=True)
        fig.savefig(os.path.join("assets", "graph.png"))
        plt.close(fig)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def get_config(self) -> str:
        """
        Build a Python dict-repr string that parsePiConfig() on Android can parse.
        All values are stored and returned as strings; booleans as 'true'/'false'.
        """
        p = self.persistent
        items = ", ".join(f"'{k}': '{v}'" for k, v in {
            "mode":            p.get("display_mode",               "graph"),
            "sensorInterval":  p.get("sensor_interval",            "600"),
            "graphAutoUpdate": p.get("auto_update_graph",          "false"),
            "graphScale":      p.get("graph_time_scale",           "1"),
            "autoDeleteData":  p.get("auto_delete_old_date",       "false"),
            "autoDelete":      p.get("auto_delete_after",          "-1"),
            "lastImage":       p.get("last_image",                 "lake.jpg"),
        }.items())
        return "{" + items + "}"

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def set_state(self, new_state: State) -> None:
        with self._state_cv:
            if new_state != self._state:
                self._state = new_state
                self._state_cv.notify_all()

    def get_state(self) -> State:
        with self._state_cv:
            return self._state