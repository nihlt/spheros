"""Відтворює всі демо з media/demo/ з вхідних кліпів у media/input/.

Для кожного демо: leak_detector -> build/<назва>.mp4 (1x) і build/<назва>.csv,
далі прискорення в 5 разів -> media/demo/<назва>_5x.mp4 і GIF-прев'ю -> media/preview/<назва>.gif.
Для одного демо ще зберігаються кадри «вхід / результат» для шапки README.

Запуск:
    python make_demos.py                # усі демо
    python make_demos.py 124646_leak    # лише вибрані
"""
import os
import subprocess
import sys

import leak_detector

ROOT = os.path.dirname(os.path.abspath(__file__))
INPUT, BUILD = os.path.join(ROOT, "media", "input"), os.path.join(ROOT, "build")
DEMO, PREVIEW = os.path.join(ROOT, "media", "demo"), os.path.join(ROOT, "media", "preview")

# назва, вхідний кліп, вирізка (x, y), старт і тривалість у секундах кліпу
DEMOS = [
    ("125707_leak",         "125707_0000-0155.mp4", (800, 200), 0,  None),
    ("125707_from20s_leak", "125707_0000-0155.mp4", (640, 300), 20, 65),
    ("124646_leak",         "124646_0627-0836.mp4", (640, 300), 0,  None),
]
SPEEDUP = 5
README_FRAME = ("124646_leak", 80)   # з якого демо і на якій секунді взяти кадри для шапки README


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


def make(name, clip, crop, start, duration):
    raw = os.path.join(BUILD, f"{name}.mp4")
    argv = [os.path.join(INPUT, clip), raw, "--crop", str(crop[0]), str(crop[1]),
            "--start", str(start), "--csv", os.path.join(BUILD, f"{name}.csv")]
    if duration is not None:
        argv += ["--duration", str(duration)]
    print(f"=== {name}: {clip}, вирізка {crop}, старт {start} с", flush=True)
    leak_detector.main(argv)

    fast = os.path.join(DEMO, f"{name}_{SPEEDUP}x.mp4")
    ffmpeg("-i", raw, "-vf", f"setpts=PTS/{SPEEDUP}", "-r", "30", "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-pix_fmt", "yuv420p", "-an", fast)
    make_gif(name)
    if name == README_FRAME[0]:
        make_readme_frames(clip, crop, raw)
    print(f"    -> {os.path.relpath(fast, ROOT)}", flush=True)


def make_readme_frames(clip, crop, raw):
    """Пара кадрів «вхід / результат» для шапки README."""
    t = str(README_FRAME[1])
    ffmpeg("-ss", t, "-i", os.path.join(INPUT, clip), "-frames:v", "1",
           "-vf", f"crop=960:780:{crop[0]}:{crop[1]}", "-q:v", "3", os.path.join(PREVIEW, "frame_input.jpg"))
    ffmpeg("-ss", t, "-i", raw, "-frames:v", "1", "-q:v", "3", os.path.join(PREVIEW, "frame_result.jpg"))


def make_gif(name):
    """Легке прев'ю для README: 6 к/с, ширина 320 px, 64 кольори (~2–4 МБ)."""
    ffmpeg("-i", os.path.join(DEMO, f"{name}_{SPEEDUP}x.mp4"), "-vf",
           "fps=6,scale=320:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];"
           "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
           os.path.join(PREVIEW, f"{name}.gif"))


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    for d in (BUILD, DEMO, PREVIEW):
        os.makedirs(d, exist_ok=True)
    wanted = set(sys.argv[1:])
    unknown = wanted - {d[0] for d in DEMOS}
    if unknown:
        sys.exit(f"невідомі демо: {', '.join(sorted(unknown))}")
    for demo in DEMOS:
        if not wanted or demo[0] in wanted:
            make(*demo)


if __name__ == "__main__":
    main()
