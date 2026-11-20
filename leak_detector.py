"""Пошук зони витоку (бульбашок): рамка LEAK на найгарячішій точці карти «підгоряння»,
лише коли сцена спокійна.

Вхід: відео 1920×1080. Аналізується вирізка 960×780 з лівим верхнім кутом (x, y).
Запуск:
    python leak_detector.py <вхід.mp4> <вихід.mp4> [--crop X Y] [--start С] [--duration С] [--csv файл.csv]

Етапи для кожного кадру (аналіз 15 к/с):
  1. зміна кадру: піксель змінився на >= THR рівнів яскравості
  2. накопичення E: згасає зі сталою часу ~2 с, тож постійна зміна накопичується
  3. рух у кадрі за останню секунду -> показ рамки з гістерезисом (CalmGate)
  4. найгарячіша точка карти E, центр згладжений (HotPoint)
  5. малювання карти, рамки й підписів (render)
Вихід: 30 к/с, кожен аналізований кадр записано двічі, тож тривалість дорівнює тривалості входу.
Алгоритм каузальний: кожен кадр залежить лише від попередніх.
"""
import argparse
import collections
import csv
import subprocess
import sys
import time

import cv2
import numpy as np

FPS, OUT_FPS = 15, 30              # частота аналізу і вихідного відео
CW, CH = 960, 780                  # розмір вирізки
THR = 15                           # поріг зміни яскравості
LAM = 1 - 1 / (2.0 * FPS)          # згасання накопичення, стала часу ~2 с
ALPHA_BASE, ALPHA_HEAT = 0.45, 0.6 # накладання сірого кадру і карти
CALM_ON, CALM_OFF = 3.0, 4.5       # рух у %: нижче CALM_ON спокій, вище CALM_OFF рух
ON_FR, OFF_FR = 2 * FPS, 1 * FPS   # кадрів спокою, щоб показати; кадрів руху, щоб сховати
BOX_HALF_W, BOX_HALF_H = 40, 30    # рамка 80×60 px
GREEN, WHITE, GRAY = (0, 255, 0), (255, 255, 255), (160, 160, 160)
FONT = cv2.FONT_HERSHEY_SIMPLEX


class CalmGate:
    """Показ рамки: після ON_FR кадрів спокою показати, після OFF_FR кадрів руху сховати.
    Між порогами CALM_ON і CALM_OFF обидва лічильники скидаються, стан не міняється."""

    def __init__(self):
        self.visible = False
        self.calm_run = 0
        self.busy_run = 0

    def update(self, avg):
        if avg < CALM_ON:
            self.calm_run, self.busy_run = self.calm_run + 1, 0
        elif avg > CALM_OFF:
            self.calm_run, self.busy_run = 0, self.busy_run + 1
        else:
            self.calm_run = self.busy_run = 0
        if not self.visible and self.calm_run >= ON_FR:
            self.visible = True
        if self.visible and self.busy_run >= OFF_FR:
            self.visible = False
        return self.visible


class HotPoint:
    """Центр рамки: argmax згладженої карти, згладжений EMA (0.8 старе, 0.2 нове)."""

    def __init__(self):
        self.c = None

    def update(self, val):
        iy, ix = np.unravel_index(np.argmax(val), val.shape)
        new = np.array([ix, iy], np.float32)
        self.c = new if self.c is None else 0.8 * self.c + 0.2 * new
        return int(self.c[0]), int(self.c[1])


def read_gray_frames(dec):
    """Віддає сірі кадри вирізки по одному, поки ffmpeg не закінчив."""
    while True:
        buf = dec.stdout.read(CW * CH)
        if len(buf) < CW * CH:
            return
        yield np.frombuffer(buf, np.uint8).reshape(CH, CW)


def render(cur, val, hx, hy, visible, avg, t_sec):
    col = cv2.applyColorMap((val * 255).astype(np.uint8), cv2.COLORMAP_MAGMA)
    img = cv2.addWeighted(cv2.cvtColor(cur, cv2.COLOR_GRAY2BGR), ALPHA_BASE, col, ALPHA_HEAT, 0)
    if visible:
        cv2.rectangle(img, (hx - BOX_HALF_W, hy - BOX_HALF_H), (hx + BOX_HALF_W, hy + BOX_HALF_H), GREEN, 3)
        cv2.putText(img, "LEAK", (hx - BOX_HALF_W, hy - BOX_HALF_H - 6), FONT, 0.7, GREEN, 2, cv2.LINE_AA)
    cv2.putText(img, f"t={t_sec:.1f}s", (12, 28), FONT, 0.7, WHITE, 2, cv2.LINE_AA)
    status = "SHOW" if visible else "hidden"
    cv2.putText(img, f"motion {avg:.1f}%  {status}", (12, 60), FONT, 0.7,
                GREEN if visible else GRAY, 2, cv2.LINE_AA)
    return img


def probe_duration(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", video], capture_output=True, text=True, check=True)
    return float(out.stdout)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Рамка LEAK у спокійні моменти відео.")
    p.add_argument("video", help="вхідне відео 1920×1080")
    p.add_argument("out", help="вихідне відео (вирізка 960×780 з картою і рамкою)")
    p.add_argument("--crop", nargs=2, type=int, default=(640, 300), metavar=("X", "Y"),
                   help="лівий верхній кут вирізки 960×780 (за замовчуванням 640 300)")
    p.add_argument("--start", type=float, default=0.0, help="з якої секунди входу почати")
    p.add_argument("--duration", type=float, default=None, help="скільки секунд обробити")
    p.add_argument("--csv", default=None, help="записати стан по кадрах: t, рух, показ, центр рамки")
    return p.parse_args(argv)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    a = parse_args(argv)
    t0 = time.perf_counter()
    cx, cy = a.crop
    duration = a.duration if a.duration is not None else probe_duration(a.video) - a.start
    n_est = int(duration * FPS)

    trim_in = ["-ss", f"{a.start:.3f}"] if a.start > 0 else []
    trim_out = ["-t", f"{a.duration:.3f}"] if a.duration is not None else []
    dec = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", *trim_in, "-i", a.video, *trim_out,
         "-vf", f"crop={CW}:{CH}:{cx}:{cy},fps={FPS}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE)
    enc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{CW}x{CH}",
         "-r", str(OUT_FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
         a.out], stdin=subprocess.PIPE)

    rows = []
    E = np.zeros((CH, CW), np.float32)
    prev = None
    fr_hist = collections.deque(maxlen=FPS)   # рух за останню секунду, %
    avg = 0.0
    gate = CalmGate()
    hot = HotPoint()
    t = 0
    for cur in read_gray_frames(dec):
        if prev is not None:
            m = (cv2.absdiff(cur, prev) >= THR).astype(np.float32)
            E = LAM * E + (1 - LAM) * m
            fr_hist.append(float(m.mean()) * 100)
            avg = sum(fr_hist) / len(fr_hist)
            gate.update(avg)
        prev = cur

        val = cv2.GaussianBlur(np.clip(E, 0, 1), (0, 0), 3)
        hx, hy = hot.update(val)
        t_sec = a.start + t / FPS
        img = render(cur, val, hx, hy, gate.visible, avg, t_sec)
        for _ in range(OUT_FPS // FPS):
            enc.stdin.write(img.tobytes())
        rows.append((t, round(t_sec, 3), round(avg, 3), int(gate.visible), hx, hy))

        t += 1
        if t % 150 == 0:
            el = time.perf_counter() - t0
            rate = t / el
            print(f"progress: {t}/{n_est} кадрів | {rate:.1f} кадр/с | минуло {el:.0f} с | "
                  f"лишилось ~{max(n_est - t, 0) / rate:.0f} с", flush=True)
    enc.stdin.close()
    enc.wait()
    dec.wait()

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["frame", "t", "motion_pct", "visible", "box_x", "box_y"])
            w.writerows(rows)
    print(f"готово: {t} кадрів за {time.perf_counter() - t0:.1f} с -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
