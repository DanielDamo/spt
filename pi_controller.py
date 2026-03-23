import base64
import datetime
import glob
import os
import queue
import threading
import time
import warnings
import zipfile

os.environ["MPLBACKEND"] = "Agg"

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import RPi.GPIO as GPIO

warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

import helpers
from helpers import State
from sensors.bme280 import BME280
from sensors.tsl2561 import TSL2561
import display.waveshare as waveshare

plt.rcParams["lines.antialiased"] = False
plt.rcParams["path.simplify"]     = True

PIXELS_X = 480
PIXELS_Y = 800
DPI      = 100

B1_PIN = 16
B1_LED = 26
B2_PIN = 5
B2_LED = 6

BLINK_DT  = 0.25
HOLD_TIME = 2.0

DEFAULT_SENSOR_INTERVAL = 600

WHITE     = "#ffffff"
RED       = "#ff0000"
GREEN     = "#00ff00"
BLUE      = "#0000ff"
YELLOW    = "#ffff00"
BG        = "#000000"
LABEL_COL = "#ffffff"

SENSORS = [
    {"key": "temp",     "label": "Temperature", "unit": "°C",  "color": RED,    "fmt": ".1f"},
    {"key": "humidity", "label": "Humidity",     "unit": "%",   "color": BLUE,   "fmt": ".0f"},
    {"key": "light",    "label": "Light",        "unit": "lux", "color": YELLOW, "fmt": ".0f"},
    {"key": "pressure", "label": "Pressure",     "unit": "hPa", "color": GREEN,  "fmt": ".1f"},
]

def _smooth(v, window=9):
    """
    Simple moving average using convolution.

    Padding with edge values avoids shrinking the signal length.
    """
    if len(v) <= window:
        return v
    k = np.ones(window) / window
    return np.convolve(np.pad(v, (window // 2, window // 2), mode="edge"), k, mode="valid")[:len(v)]

def _rgba(hex_col, a):
    h = hex_col.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4)) + (a,)

def _x_axis(ax, effective_days, edge_threshold=0.031):
    """
    Custom tick filtering.

    The edge_threshold prevents labels too close to the plot edges,
    which would otherwise get cut off or look cramped.
    """
    import matplotlib.ticker as mticker

    if effective_days <= 1:
        locator   = mdates.HourLocator(byhour=range(0, 24, 3))
        formatter = mdates.DateFormatter("%H:%M")
    elif effective_days <= 7:
        locator   = mdates.DayLocator()
        formatter = mdates.DateFormatter("%d %b")
    else:
        locator   = mdates.WeekdayLocator()
        formatter = mdates.DateFormatter("%d %b")

    xmin, xmax = ax.get_xlim()
    span       = xmax - xmin
    ticks      = [t for t in locator.tick_values(mdates.num2date(xmin), mdates.num2date(xmax))
                  if (t - xmin) / span > edge_threshold
                  and (xmax - t) / span > edge_threshold]

    ax.xaxis.set_major_locator(mticker.FixedLocator(ticks))
    ax.xaxis.set_major_formatter(formatter)
    ax.tick_params(axis="x", labelsize=7, labelcolor=LABEL_COL, length=0, pad=10)
    for label in ax.get_xticklabels():
        label.set_clip_on(False)

def _draw_panel(ax, s, t_num, v_arr, effective_days,
                show_x=False, label_fontsize=15, value_fontsize=24, unit_fontsize=11,
                pixels_wide=PIXELS_X, edge_threshold=0.031):
    """
    This function does quite a lot:

    - Smooths the data
    - Dynamically scales axes with padding
    - Draws layered fills for a nicer look
    - Annotates min/max points

    Worth noting:
    all scaling is relative, so it behaves well for different ranges.
    """

    color = s["color"]
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_facecolor(BG)

    if len(t_num) == 0 or len(v_arr) == 0:
        ax.text(0.5, 0.5, "No data", color=WHITE, fontsize=8,
                ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        return

    v_s        = _smooth(v_arr, max(3, len(v_arr) // 60))
    vmin, vmax = v_arr.min(), v_arr.max()
    vrange     = max(vmax - vmin, 1e-6)
    lo, hi     = vrange * 0.10, vrange * 0.52
    t_span     = t_num[-1] - t_num[0]

    ax.set_xlim(t_num[0] - t_span * 0.03, t_num[-1])
    ax.set_ylim(vmin - lo, vmax + hi)

    base = vmin - lo
    ax.fill_between(t_num, v_s, base, color=_rgba(color, 0.14), lw=0, zorder=1)
    ax.fill_between(t_num, v_s, np.maximum(v_s - vrange * 0.20, base),
                    color=_rgba(color, 0.28), lw=0, zorder=2)
    ax.plot(t_num, v_s, color=color, linewidth=1.5,
            solid_capstyle="round", solid_joinstyle="round", zorder=3)

    ax.set_yticks([])
    ax.set_xticks([])

    for idx, dot_col, t_va in [
        (int(np.argmin(v_s)), LABEL_COL, "top"),
        (int(np.argmax(v_s)), WHITE,      "bottom"),
    ]:
        frac = (t_num[idx] - t_num[0]) / max(t_num[-1] - t_num[0], 1e-9)
        ha   = "left" if frac < 0.06 else "right" if frac > 0.94 else "center"
        if t_va == "top":
            val_frac    = (v_s[idx] - vmin) / vrange
            v_off, t_va = (6, "bottom") if val_frac < 0.20 else (-8, "top")
        else:
            v_off = 6
        ax.plot(t_num[idx], v_s[idx], "o", color=dot_col, ms=4, zorder=5)
        ax.annotate(f"{v_arr[idx]:{s['fmt']}}",
                    xy=(t_num[idx], v_s[idx]),
                    xytext=(0, v_off), textcoords="offset points",
                    color=dot_col, fontsize=9, ha=ha, va=t_va, zorder=6)

    ax.text(0.012, 0.965, s["label"].upper(), transform=ax.transAxes,
            color=color, fontsize=label_fontsize, fontweight="bold", va="top", ha="left")

    cur_str   = f"{v_arr[-1]:{s['fmt']}}"
    unit_str  = s["unit"]
    unit_frac = (len(unit_str) * 6.5 + 4) / (pixels_wide * (1 - 0.025))
    num_right = 0.988 - unit_frac
    ax.text(num_right, 0.965, cur_str, transform=ax.transAxes,
            color=WHITE, fontsize=value_fontsize, fontweight="bold", va="top", ha="right", zorder=7)
    ax.text(num_right + 0.004, 0.965, unit_str, transform=ax.transAxes,
            color=WHITE, fontsize=unit_fontsize, va="top", ha="left", zorder=7)

    rule_y = vmax + hi * 0.06
    ax.plot([t_num[-1] * 0.62 + t_num[0] * 0.38, t_num[-1]], [rule_y, rule_y],
            color=color, lw=0.6, zorder=4, solid_capstyle="butt")

def _header(ax_h, date_str, period_str=None):
    ax_h.set_facecolor(BG)
    ax_h.axis("off")
    ax_h.text(0.0, 1.0, date_str, transform=ax_h.transAxes,
              color=WHITE, fontsize=12, fontweight="bold", va="top")
    if period_str:
        ax_h.text(0.988, 1.0, period_str, transform=ax_h.transAxes,
                  color=LABEL_COL, fontsize=12, va="top", ha="right")
    ax_h.add_line(plt.Line2D([0, 1], [0, 0], transform=ax_h.transAxes,
                              color=WHITE, linewidth=0.5))

def _save(fig, out, period_str=None):
    if period_str:
        fig.text(0.975, 0.012, period_str, color=LABEL_COL,
                 fontsize=11, ha="right", va="bottom")
    os.makedirs("assets", exist_ok=True)
    fig.savefig(out, facecolor=BG, dpi=DPI)
    plt.close(fig)

def _plot_portrait(data, date_str, period_str, effective_days):
    fig = plt.figure(figsize=(PIXELS_X / DPI, PIXELS_Y / DPI), dpi=DPI, facecolor=BG)
    gs  = fig.add_gridspec(5, 1,
                           height_ratios=[0.36, 2, 2, 2, 2.15],
                           hspace=0.10,
                           left=0.03, right=0.975,
                           top=0.978, bottom=0.085)
    _header(fig.add_subplot(gs[0]), date_str)
    for row, s in enumerate(SENSORS):
        times, v_arr = data[s["key"]]
        t_num = mdates.date2num(list(times)) if len(times) else np.array([])
        ax    = fig.add_subplot(gs[row + 1])
        _draw_panel(ax, s, t_num, v_arr, effective_days,
                    show_x=(row == 3), pixels_wide=PIXELS_X)
        ax.add_line(plt.Line2D([0, 1], [0, 0], transform=ax.transAxes,
                                color=s["color"], linewidth=0.4, clip_on=False))
    _save(fig, os.path.join("assets", "graph.png"), period_str)

def _plot_landscape(data, date_str, period_str, effective_days):
    fig = plt.figure(figsize=(PIXELS_Y / DPI, PIXELS_X / DPI), dpi=DPI, facecolor=BG)
    gs  = fig.add_gridspec(3, 2,
                           height_ratios=[0.28, 2, 2.3],
                           hspace=0.12, wspace=0.06,
                           left=0.01, right=0.975,
                           top=0.978, bottom=0.04)
    _header(fig.add_subplot(gs[0, :]), date_str, period_str)
    panel_positions = [(1, 0), (1, 1), (2, 0), (2, 1)]
    bottom_row      = {2, 3}
    panel_px        = PIXELS_Y // 2
    for i, s in enumerate(SENSORS):
        r, c         = panel_positions[i]
        times, v_arr = data[s["key"]]
        t_num        = mdates.date2num(list(times)) if len(times) else np.array([])
        ax           = fig.add_subplot(gs[r, c])
        _draw_panel(ax, s, t_num, v_arr, effective_days,
                    show_x=(i in bottom_row),
                    label_fontsize=11, value_fontsize=18, unit_fontsize=8,
                    pixels_wide=panel_px)
    _save(fig, os.path.join("assets", "graph.png"))

def generate_plot(days: int = 1, rotation: str = "portrait") -> None:
    if rotation not in ("portrait", "landscape"):
        raise ValueError(f"Unknown rotation: {rotation!r}")

    if days == -1:
        n = len(glob.glob(os.path.abspath(os.path.join("logs", "temp", "*.csv"))))
        effective_days = max(n, 1)
    else:
        effective_days = days

    data = {}
    for s in SENSORS:
        try:
            t, v = helpers._read_daily_csv_timeseries(
                os.path.abspath(os.path.join("logs", s["key"])), effective_days)
        except Exception:
            t, v = [], []
        data[s["key"]] = (t, np.array(v, dtype=float) if v else np.array([]))

    date_str   = datetime.date.today().strftime("%A  %d %B %Y").upper()
    period_str = ("All time" if days == -1
                  else f"Last {effective_days if effective_days != 1 else ''} day{'s' if effective_days != 1 else ''}")

    if rotation == "portrait":
        _plot_portrait(data, date_str, period_str, effective_days)
    else:
        _plot_landscape(data, date_str, period_str, effective_days)

class PiController:
    """
    Central controller for the SPT Pi. Manages GPIO, sensors, display,
    command queue, and persistent settings. BLE hooks (ble_send, ble_reset,
    ble_stop, ble_start) are wired in externally by main.py.
    """

    def __init__(self, upload_folder: str = os.path.join("assets", "images")) -> None:
        self._state    = State.BOOTING
        self._state_cv = threading.Condition()
        self._stop_evt = threading.Event()
        self._cmd_q: queue.Queue = queue.Queue()
        self.shutdown_requested  = threading.Event()
        self.set_state(State.BOOTING)

        self._xfer_chunks: list[str]  = []
        self._xfer_index:  int        = 0
        self._upload_name:   str      = ""
        self._upload_chunks: list[bytes] = []
        self._upload_total:  int      = 0

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(B1_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
        GPIO.setup(B1_LED, GPIO.OUT)
        GPIO.setup(B2_PIN, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
        GPIO.setup(B2_LED, GPIO.OUT)
        GPIO.add_event_detect(B1_PIN, GPIO.BOTH, callback=self._on_b1_edge, bouncetime=80)
        GPIO.add_event_detect(B2_PIN, GPIO.BOTH, callback=self._on_b2_edge, bouncetime=80)

        self._cmd_thread = threading.Thread(target=self._cmd_worker, daemon=True, name="cmd-worker")
        self._led_thread = threading.Thread(target=self._led_worker, daemon=True, name="led-worker")
        self._cmd_thread.start()
        self._led_thread.start()

        self._b1_pressed_at: float | None = None
        self._b1_lock = threading.Lock()
        self._b2_pressed_at: float | None = None
        self._b2_lock = threading.Lock()

        self.persistent     = helpers.load_persistent()
        sensor_interval     = int(self.persistent.get("sensor_interval", DEFAULT_SENSOR_INTERVAL))
        auto_delete_old_data = self.persistent.get("auto_delete_old_data") == "true"
        auto_delete_after   = int(self.persistent.get("auto_delete_after", -1))

        self.bme = BME280(sensor_interval, delete_old_data=auto_delete_old_data,
                          delete_after=auto_delete_after, on_reading=self._on_sensor_reading)
        self.tsl = TSL2561(sensor_interval, delete_old_data=auto_delete_old_data,
                           delete_after=auto_delete_after)

        self.upload_folder = upload_folder
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs("logs", exist_ok=True)

        try:
            self.epd = waveshare.EPD(rotation_callback=self.update_rotation)
            self.epd.init()
            self.display_lock = threading.Lock()
        except IOError as e:
            print(f"[PiController] Display init failed: {e}")
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            raise

        self.bme.start()
        self.tsl.start()
        self.set_state(State.READY)

    def stop(self) -> None:
        print("[PiController] Stopping.")
        if hasattr(self, "ble_stop") and callable(self.ble_stop):
            try:
                self.ble_stop()
            except Exception as e:
                print(f"[PiController] ble_stop failed: {e}")
        self._stop_evt.set()
        self._cmd_q.put(("stop", None))
        self._cmd_thread.join(timeout=2.0)
        self._led_thread.join(timeout=2.0)
        self.bme.stop()
        self.tsl.stop()
        waveshare.epdconfig.module_exit(cleanup=True)
        self._apply_leds_static(0, 0)
        GPIO.cleanup()
        helpers.save_persistent(self.persistent)
        print("[PiController] Stopped.")

    def _on_b1_edge(self, channel: int) -> None:
        level = GPIO.input(B1_PIN)
        if level == 0:
            with self._b1_lock:
                self._b1_pressed_at = time.monotonic()
        else:
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

    def _cmd_worker(self) -> None:
        """
        Command processing loop.

        Important behaviour:
        - Pauses if state is BUSY
        - Accepts both tuple commands and string commands
        - String commands are parsed as "op:payload"
        """
        while not self._stop_evt.is_set():
            if self.get_state() == State.BUSY:
                time.sleep(0.05)
                continue
            try:
                command = self._cmd_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if isinstance(command, tuple):
                op, payload = command
            elif isinstance(command, str):
                parts = command.split(":", 1)       # Only split once so payload can contain ':' safely
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
        """
        Large dispatcher for all commands.

        Design choice:
        everything funnels through here, which keeps BLE logic simple
        but makes this method quite dense.
        """
        if op == "request" and payload == "config":
            self.ble_send(self.get_config())
       
        elif op == "request" and payload == "images":
            names = [os.path.splitext(f)[0] for f in self.list_uploaded_images()]
            self.ble_send(f"imagesStart:{len(names)}")
            for name in names:
                self.ble_send(f"imagesItem:{name}")
            self.ble_send("imagesEnd")

        elif op == "disconnect":
            self.ble_reset()

        elif op == "mode":
            val = payload.strip()
            if val == "graph":
                self.display_graph()
            else:
                self.display_image(self.persistent.get("last_image", "lake.bin"))

        elif op == "sensorInterval":
            interval = int(payload.strip())
            self.persistent["sensor_interval"] = str(interval)
            self.bme.update_interval(interval)
            self.tsl.update_interval(interval)

        elif op == "graphAutoUpdate":
            self.persistent["auto_update_graph"] = payload.strip().lower()

        elif op == "graphScale":
            self.persistent["graph_time_scale"] = payload.strip()
            self.display_graph()
       
        elif op == "imageSet" and isinstance(payload, str):
            self.display_image(f"{payload.strip()}.bin")
       
        elif op == "imgStart":
            parts = payload.split(":")
            if len(parts) >= 3:
                self._upload_name   = parts[0].strip()
                self._upload_total  = int(parts[1])
                self._upload_chunks = []
                print(f"[PiController] Image upload starting: '{self._upload_name}' ({self._upload_total}B)")
                self.ble_send("imgReady:1")
            else:
                print(f"[PiController] Malformed imgStart: {payload!r}")
                self.ble_send("imgError:bad imgStart")

        elif op == "imgChunk":
            colon = payload.index(":")
            index_str = payload[:colon]
            b64data   = payload[colon + 1:]
            try:
                index       = int(index_str)
                # Each chunk is base64 encoded, so we decode and append
                chunk_bytes = base64.b64decode(b64data)
                # Note: no ordering check here, assumes sender behaves
                self._upload_chunks.append(chunk_bytes)
                bytes_received = sum(len(c) for c in self._upload_chunks)
                print(f"[PiController] imgChunk {index} received ({len(chunk_bytes)}B), total {bytes_received}B")
                self.ble_send(f"imgAck:{index}")
            except Exception as e:
                print(f"[PiController] imgChunk error: {e}")
                self.ble_send("imgError:chunk decode failed")

        elif op == "imgEnd":
            name = payload.strip()
            try:
                packed = b"".join(self._upload_chunks)
                out_path = os.path.join(self.upload_folder, f"{name}.bin")
                with open(out_path, "wb") as f:
                    f.write(packed)
                print(f"[PiController] Image saved: {out_path} ({len(packed)}B)")
                self._upload_chunks = []
                self._upload_name   = ""
                self.ble_send(f"imgDone:{name}")
                self.display_image(f"{name}.bin")
            except Exception as e:
                print(f"[PiController] imgEnd save error: {e}")
                self.ble_send(f"imgError:save failed")
       
        elif op == "deleteImage" and isinstance(payload, str):
            name     = payload.strip()
            filename = f"{name}.bin"
            path     = os.path.join(self.upload_folder, filename)
            if os.path.exists(path):
                os.remove(path)
                print(f"[PiController] {filename} deleted.")
            else:
                print(f"[PiController] {filename} not found.")
            if self.persistent.get("last_image") == filename:
                self.display_graph()
       
        elif op == "deleteAllImages":
            all_files = os.listdir(self.upload_folder)
            for file in all_files:
                os.remove(os.path.join(self.upload_folder, file))
                print(f"[PiController] {file} deleted.")
            self.display_graph()

        elif op == "autoDeleteData":
            self.persistent["auto_delete_old_data"] = payload.strip().lower()
            payload_bool = True if payload.strip().lower() == "true" else False
            self.bme.update_auto_delete(delete_old_data=payload_bool, delete_after=int(self.persistent["auto_delete_after"]))
            self.tsl.update_auto_delete(delete_old_data=payload_bool, delete_after=int(self.persistent["auto_delete_after"]))

        elif op == "autoDelete":
            self.persistent["auto_delete_after"] = payload.strip()
            payload_bool = True if self.persistent["auto_delete_old_data"] == "true" else False
            self.bme.update_auto_delete(delete_old_data=payload_bool, delete_after=int(self.persistent["auto_delete_after"]))
            self.tsl.update_auto_delete(delete_old_data=payload_bool, delete_after=int(self.persistent["auto_delete_after"]))

        elif op == "downloadSensorData":
            self._start_file_transfer()

        elif op == "fileReady":
            self._send_next_chunk()

        elif op == "fileAck":
            try:
                acked = int(payload.strip())
            except ValueError:
                acked = -1
            # Basic reliability mechanism:
            # only send next chunk if previous one was acknowledged
            if acked == self._xfer_index - 1:
                self._send_next_chunk()
            else:
                print(f"[PiController] fileAck out of order: got {acked}, expected {self._xfer_index - 1}")

       
        elif op == "deleteAllData":
            all_log_dirs = self.bme.get_log_dirs() + self.tsl.get_log_dirs()
            for d in all_log_dirs:
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))
                print(f"[PiController] {d} emptied.")

        elif op == "set_state":
            self.set_state(payload)

        elif op == "display_image":
            self.display_image(str(payload))

        elif op == "display_graph":
            self.display_graph()

        elif op == "toggle_screen":
            if self.persistent.get("display_mode") == "graph":
                self.display_image(self.persistent.get("last_image", "lake.bin"))
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

    def _apply_leds_static(self, led1: int | None, led2: int | None) -> None:
        if led1 is not None:
            GPIO.output(B1_LED, 1 if led1 else 0)
        if led2 is not None:
            GPIO.output(B2_LED, 1 if led2 else 0)

    def _led_worker(self) -> None:
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
        """
        Regenerates the plot every time.

        This is slightly expensive, but ensures the display is always fresh
        and consistent with current settings.
        """
        try:
            with self.display_lock:
                self.set_state(State.BUSY)
                rotation = self.persistent.get("rotation", "portrait")
                generate_plot(days=int(self.persistent.get("graph_time_scale", 1)), rotation=rotation)
                self.epd.display(self.epd.getbuffer(os.path.join("assets", "graph.png")))
                self.persistent["display_mode"] = "graph"
                self.set_state(State.READY)
        except KeyboardInterrupt:
            waveshare.epdconfig.module_exit(cleanup=True)
            raise

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

    CHUNK_BYTES = 170   # allows for 99,999 chunks

    def _start_file_transfer(self) -> None:
        """
        Zip logs into /tmp/spt_export.zip, slice into base64 chunks,
        then send fileStart to kick off the handshake with Android.
        """
        zip_path = "/tmp/spt_export.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk("logs"):
                    for fname in files:
                        full = os.path.join(root, fname)
                        zf.write(full, os.path.relpath(full, "."))

            with open(zip_path, "rb") as f:
                raw = f.read()

            self._xfer_chunks = []
            for i in range(0, len(raw), self.CHUNK_BYTES):
                chunk_bytes = raw[i:i + self.CHUNK_BYTES]
                self._xfer_chunks.append(base64.b64encode(chunk_bytes).decode("ascii"))
            self._xfer_index = 0

            total_bytes  = len(raw)
            total_chunks = len(self._xfer_chunks)
            print(f"[PiController] Transfer ready: {total_bytes}B in {total_chunks} chunks")
            self.ble_send(f"fileStart:spt_export.zip:{total_bytes}:{total_chunks}")

        except Exception as e:
            print(f"[PiController] _start_file_transfer error: {e}")
            self.ble_send("fileError:Failed to create export")

    def _send_next_chunk(self) -> None:
        """Send the next pending chunk, or fileEnd if all sent."""
        if self._xfer_index >= len(self._xfer_chunks):
            print("[PiController] Transfer complete")
            self.ble_send("fileEnd:spt_export.zip")
            self._xfer_chunks = []
            self._xfer_index  = 0
            return

        chunk_b64 = self._xfer_chunks[self._xfer_index]
        self.ble_send(f"fileChunk:{self._xfer_index}:{chunk_b64}")
        self._xfer_index += 1

    def _on_sensor_reading(self) -> None:
        """
        Called by the BME280 thread after every successful reading.
        If the display is in graph mode and auto-update is enabled, queue a
        graph refresh.
        """
        if (self.persistent.get("display_mode") == "graph"
                and self.persistent.get("auto_update_graph") == "true"):
            self._cmd_q.put(("display_graph", None))

    def update_rotation(self, rotation):
        self.persistent["rotation"] = rotation

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
            "autoDeleteData":  p.get("auto_delete_old_data",       "false"),
            "autoDelete":      p.get("auto_delete_after",          "-1"),
            "lastImage":       os.path.splitext(p.get("last_image", "lake.bin"))[0],
        }.items())
        return "{" + items + "}"

    def set_state(self, new_state: State) -> None:
        with self._state_cv:
            if new_state != self._state:
                self._state = new_state
                self._state_cv.notify_all()

    def get_state(self) -> State:
        with self._state_cv:
            return self._state