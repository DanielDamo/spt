import os, sys, glob, datetime, warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

plt.rcParams["lines.antialiased"] = False
plt.rcParams["path.simplify"]     = True

PIXELS_X = 480
PIXELS_Y = 800
DPI      = 100

WHITE    = "#ffffff"
RED      = "#ff0000"
GREEN    = "#00ff00"
BLUE     = "#0000ff"
YELLOW   = "#ffff00"
BG       = "#000000"
LABEL_COL = "#ffffff"

SENSORS = [
    {"key": "temp",     "label": "Temperature", "unit": "°C",  "color": RED,    "fmt": ".1f"},
    {"key": "humidity", "label": "Humidity",     "unit": "%",   "color": BLUE,   "fmt": ".0f"},
    {"key": "light",    "label": "Light",        "unit": "lux", "color": YELLOW, "fmt": ".0f"},
    {"key": "pressure", "label": "Pressure",     "unit": "hPa", "color": GREEN,  "fmt": ".1f"},
]


def _smooth(v, window=9):
    if len(v) <= window:
        return v
    k = np.ones(window) / window
    return np.convolve(np.pad(v, (window // 2, window // 2), mode="edge"), k, mode="valid")[:len(v)]


def _rgba(hex_col, a):
    h = hex_col.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4)) + (a,)


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

    period_str = ("All time" if days == -1
                  else f"Last {effective_days if effective_days != 1 else ''} day{'s' if effective_days != 1 else ''}")
    date_str = datetime.date.today().strftime("%A  %d %B %Y").upper()

    if rotation == "portrait":
        _plot_portrait(data, date_str, period_str, days, effective_days)
    else:
        _plot_landscape(data, date_str, period_str, days, effective_days)


def _x_axis(ax, effective_days):
    """Apply x-axis ticks and labels to ax."""
    if effective_days <= 1:
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    elif effective_days <= 7:
        ax.xaxis.set_major_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    else:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.tick_params(axis="x", labelsize=7, labelcolor=LABEL_COL, length=0, pad=10)
    for label in ax.get_xticklabels():
        label.set_clip_on(False)


def _draw_panel(ax, s, t_num, v_arr, effective_days,
                show_x=False, label_fontsize=15, value_fontsize=24, unit_fontsize=11,
                pixels_wide=PIXELS_X):
    """Render a single sensor panel into ax."""
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

    v_s    = _smooth(v_arr, max(3, len(v_arr) // 60))
    vmin, vmax = v_arr.min(), v_arr.max()
    vrange     = max(vmax - vmin, 1e-6)
    lo, hi     = vrange * 0.10, vrange * 0.52

    t_span = t_num[-1] - t_num[0]
    ax.set_xlim(t_num[0] - t_span * 0.03, t_num[-1])
    ax.set_ylim(vmin - lo, vmax + hi)

    base = vmin - lo
    ax.fill_between(t_num, v_s, base, color=_rgba(color, 0.14), lw=0, zorder=1)
    ax.fill_between(t_num, v_s, np.maximum(v_s - vrange * 0.20, base),
                    color=_rgba(color, 0.28), lw=0, zorder=2)
    ax.plot(t_num, v_s, color=color, linewidth=1.5,
            solid_capstyle="round", solid_joinstyle="round", zorder=3)

    ax.set_yticks([])
    if show_x:
        _x_axis(ax, effective_days)
    else:
        ax.set_xticks([])

    # Min / max markers
    for idx, dot_col, t_va in [
        (int(np.argmin(v_arr)), LABEL_COL, "top"),
        (int(np.argmax(v_arr)), WHITE,      "bottom"),
    ]:
        frac = (t_num[idx] - t_num[0]) / max(t_num[-1] - t_num[0], 1e-9)
        ha   = "left" if frac < 0.06 else "right" if frac > 0.94 else "center"
        if t_va == "top":
            val_frac    = (v_arr[idx] - vmin) / vrange
            v_off, t_va = (6, "bottom") if val_frac < 0.20 else (-8, "top")
        else:
            v_off = 6
        ax.plot(t_num[idx], v_s[idx], "o", color=dot_col, ms=4, zorder=5)
        ax.annotate(f"{v_arr[idx]:{s['fmt']}}",
                    xy=(t_num[idx], v_s[idx]),
                    xytext=(0, v_off), textcoords="offset points",
                    color=dot_col, fontsize=9, ha=ha, va=t_va, zorder=6)

    # Sensor label and current value
    ax.text(0.012, 0.965, s["label"].upper(), transform=ax.transAxes,
            color=color, fontsize=label_fontsize, fontweight="bold", va="top", ha="left")

    cur_str  = f"{v_arr[-1]:{s['fmt']}}"
    unit_str = s["unit"]
    unit_frac = (len(unit_str) * 6.5 + 4) / (pixels_wide * (1 - 0.025))
    num_right = 0.988 - unit_frac
    ax.text(num_right, 0.965, cur_str, transform=ax.transAxes,
            color=WHITE, fontsize=value_fontsize, fontweight="bold", va="top", ha="right", zorder=7)
    ax.text(num_right + 0.004, 0.965, unit_str, transform=ax.transAxes,
            color=WHITE, fontsize=unit_fontsize, va="top", ha="left", zorder=7)

    # Accent rule
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
    print(f"[generate_plot] Saved {out}")


def _plot_portrait(data, date_str, period_str, days, effective_days):
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

        # Coloured separator below each panel
        ax.add_line(plt.Line2D([0, 1], [0, 0], transform=ax.transAxes,
                                color=s["color"], linewidth=0.4, clip_on=False))

    _save(fig, os.path.join("assets", "graph.png"), period_str)


def _plot_landscape(data, date_str, period_str, days, effective_days):
    fig = plt.figure(figsize=(PIXELS_Y / DPI, PIXELS_X / DPI), dpi=DPI, facecolor=BG)

    gs = fig.add_gridspec(3, 2,
                          height_ratios=[0.28, 2, 2.3],
                          hspace=0.12, wspace=0.06,
                          left=0.02, right=0.985,
                          top=0.978, bottom=0.095)

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

    _save(fig, os.path.join("assets", "graph_landscape.png"))


if __name__ == "__main__":
    generate_plot(-1, "portrait")
    generate_plot(-1, "landscape")


    