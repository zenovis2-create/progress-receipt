#!/usr/bin/env python3
"""Build the README demo GIF from real CLI output and the sealed self-report.

Run with a dev-only Pillow environment, for example:

    uv run --isolated --python 3.12 --no-project --with-editable . \
      --with pillow python tools/build_demo_gif.py --output docs/demo.gif
"""

# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
from html import escape
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

from generate_self_report import find_browser


FRAME_SIZE = (1200, 675)
GIF_SIZE = (960, 540)
FRAME_DURATIONS_MS = [650, 900, 650, 700, 500, 900, 1000, 1000]
MAX_DURATION_MS = 8_000
MAX_BYTES = 2_500_000


class DemoGifError(RuntimeError):
    pass


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [
        windows / ("CascadiaMono-Bold.ttf" if bold else "CascadiaMono.ttf"),
        windows / ("consolab.ttf" if bold else "consola.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def terminal_frame(repo: Path, lines: list[tuple[str, str]], cursor: bool) -> Image.Image:
    frame = Image.new("RGB", FRAME_SIZE, "#0c111b")
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((34, 30, 1166, 645), radius=22, fill="#111827", outline="#30394a", width=2)
    draw.rounded_rectangle((34, 30, 1166, 78), radius=22, fill="#1d2635")
    draw.rectangle((34, 55, 1166, 78), fill="#1d2635")
    for index, color in enumerate(("#ff6b6b", "#ffd166", "#5bd6aa")):
        x = 62 + index * 26
        draw.ellipse((x, 47, x + 13, 60), fill=color)
    title_font = load_font(17, bold=True)
    body_font = load_font(19)
    bold_font = load_font(19, bold=True)
    draw.text((495, 44), "progress-receipt — PowerShell", font=title_font, fill="#aab3c2")
    prompt = f"PS {repo}>"
    y = 114
    draw.text((68, y), prompt, font=body_font, fill="#7aa2ff")
    command_x = 68 + int(draw.textlength(prompt + " ", font=body_font))
    draw.text((command_x, y), "uvx --from . progress-receipt demo", font=bold_font, fill="#f2f4f7")
    if cursor:
        cursor_x = command_x + int(draw.textlength("uvx --from . progress-receipt demo", font=bold_font)) + 4
        draw.rectangle((cursor_x, y + 2, cursor_x + 10, y + 24), fill="#5bd6aa")
    y += 48
    for text, color in lines:
        draw.text((68, y), text, font=body_font, fill=color)
        y += 32
    draw.text((68, 598), "Receipts for AI coding — changed · verified · blocked · not_observed", font=title_font, fill="#8390a5")
    return frame


def run_demo(repo: Path, scratch: Path) -> tuple[list[tuple[str, str]], Path]:
    uvx = shutil.which("uvx")
    if not uvx:
        raise DemoGifError("uvx is required to capture the real demo command")
    demo_temp = scratch / "tmp"
    demo_temp.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "UV_CACHE_DIR": str(scratch / "uv-cache"),
            "TMPDIR": str(demo_temp),
            "TEMP": str(demo_temp),
            "TMP": str(demo_temp),
        }
    )
    result = subprocess.run(
        [uvx, "--from", ".", "progress-receipt", "demo"],
        cwd=repo,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode:
        raise DemoGifError(f"demo command failed with exit code {result.returncode}")
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(paths) != 1:
        raise DemoGifError("demo stdout must contain exactly one report path")
    report = Path(paths[0])
    if not report.is_absolute() or report.name != "index.html" or not report.is_file():
        raise DemoGifError("demo stdout did not contain a parseable report path")
    stderr = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    try:
        start = next(index for index, line in enumerate(stderr) if line.startswith("progress-receipt "))
    except StopIteration as exc:
        raise DemoGifError("demo stderr did not contain the expected summary") from exc
    summary = stderr[start:]
    selected = [
        line
        for line in summary
        if line.startswith(("progress-receipt ", "canned Git fixture", "claims", "evidence", "report", "open this"))
    ]
    terminal_lines = [(line, "#aab3c2") for line in selected[:6]]
    terminal_lines.append((str(report), "#5bd6aa"))
    return terminal_lines, report


def report_variant(source: str, caption: str, target: str, slider: int | None = None) -> str:
    slider_script = ""
    if slider is not None:
        slider_script = f"""
        const slider = document.querySelector('.comparison-slider');
        slider.value = '{slider}';
        slider.dispatchEvent(new Event('input', {{bubbles: true}}));
        """
    target_expression = {
        "comparison": "document.querySelector('.visual-comparisons')",
        "claims": "[...document.querySelectorAll('section.panel')].find((node) => node.querySelector('h2')?.textContent === 'What changed and what it means')",
        "evidence": "[...document.querySelectorAll('section.panel')].find((node) => node.querySelector('h2')?.textContent === 'Verification evidence')",
    }.get(target)
    if target_expression is None:
        target_script = "window.scrollTo(0, 0);"
    else:
        target_script = f"""
        const target = {target_expression};
        const targetTop = target.getBoundingClientRect().top;
        document.querySelector('main').style.transform = `translateY(${{12 - targetTop}}px)`;
        window.scrollTo(0, 0);
        """
    extra_head = f"""
<style>
html {{ scroll-behavior: auto !important; }}
.visual-comparisons {{ width: min(940px, calc(100% - 32px)); margin-inline: auto; }}
.demo-gif-caption {{ position: fixed; z-index: 9999; top: 14px; right: 18px; padding: 8px 13px;
  border-radius: 999px; background: rgba(12,17,27,.88); color: white; font: 800 13px/1.2 system-ui,sans-serif;
  letter-spacing: .04em; box-shadow: 0 8px 24px rgba(0,0,0,.18); }}
</style>
"""
    extra_body = f"""
<div class="demo-gif-caption">{escape(caption)}</div>
<script>
window.addEventListener('load', () => {{
  {slider_script}
  {target_script}
}});
</script>
"""
    if "</head>" not in source or "</body>" not in source:
        raise DemoGifError("self-report HTML is missing document boundaries")
    return source.replace("</head>", extra_head + "</head>", 1).replace("</body>", extra_body + "</body>", 1)


def screenshot(browser: Path, source: Path, output: Path) -> None:
    result = subprocess.run(
        [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--force-device-scale-factor=1",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=1000",
            f"--window-size={FRAME_SIZE[0]},{FRAME_SIZE[1]}",
            f"--screenshot={output.resolve()}",
            source.resolve().as_uri(),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if result.returncode or not output.is_file():
        raise DemoGifError(f"browser capture failed for {source.name}")


def capture_report_frames(repo: Path, scratch: Path) -> list[Image.Image]:
    report = repo / "examples" / "self-report"
    if not (report / "index.html").is_file():
        raise DemoGifError("committed self-report is missing")
    site = scratch / "site"
    shutil.copytree(report, site)
    source = (site / "index.html").read_text(encoding="utf-8")
    variants: list[tuple[str, str, str, int | None]] = [
        ("hero", "OPENED · evidence-bound report", "top", None),
        ("before", "BEFORE · imported skill", "comparison", 100),
        ("split", "DRAG · compare the same surface", "comparison", 50),
        ("after", "AFTER · launchable OSS product", "comparison", 0),
        ("claims", "CLAIMS · four states stay separate", "claims", None),
        ("evidence", "EVIDENCE · commands, refs, and exit codes", "evidence", None),
    ]
    browser = find_browser()
    frames: list[Image.Image] = []
    for name, caption, target, slider in variants:
        html_path = site / f"gif-{name}.html"
        png_path = scratch / f"gif-{name}.png"
        html_path.write_text(report_variant(source, caption, target, slider), encoding="utf-8")
        screenshot(browser, html_path, png_path)
        with Image.open(png_path) as captured:
            frames.append(captured.convert("RGB"))
    return frames


def shared_palette(frames: list[Image.Image]) -> Image.Image:
    sample = Image.new("RGB", GIF_SIZE)
    tile_width = GIF_SIZE[0] // 4
    tile_height = GIF_SIZE[1] // 2
    for index, frame in enumerate(frames):
        tile = frame.resize((tile_width, tile_height), Image.Resampling.LANCZOS)
        sample.paste(tile, ((index % 4) * tile_width, (index // 4) * tile_height))
    return sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def write_gif(frames: list[Image.Image], output: Path) -> None:
    if len(frames) != len(FRAME_DURATIONS_MS):
        raise DemoGifError("frame and duration counts differ")
    resized = [frame.resize(GIF_SIZE, Image.Resampling.LANCZOS) for frame in frames]
    palette = shared_palette(resized)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in resized]
    output.parent.mkdir(parents=True, exist_ok=True)
    quantized[0].save(
        output,
        save_all=True,
        append_images=quantized[1:],
        duration=FRAME_DURATIONS_MS,
        loop=0,
        optimize=True,
        disposal=1,
    )
    if output.stat().st_size >= MAX_BYTES:
        raise DemoGifError(f"GIF is too large: {output.stat().st_size} bytes")
    with Image.open(output) as gif:
        duration = sum(gif.seek(index) or gif.info.get("duration", 0) for index in range(gif.n_frames))
        if gif.n_frames != len(frames) or duration > MAX_DURATION_MS:
            raise DemoGifError(f"GIF validation failed: {gif.n_frames} frames, {duration} ms")


def build(repo: Path, output: Path, scratch_parent: Path | None) -> None:
    repo = repo.resolve()
    parent = scratch_parent.resolve() if scratch_parent else None
    if parent:
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pr-gif-", dir=parent) as temporary:
        scratch = Path(temporary)
        terminal_lines, _ = run_demo(repo, scratch)
        frames = [
            terminal_frame(repo, [], cursor=True),
            terminal_frame(repo, terminal_lines, cursor=False),
            *capture_report_frames(repo, scratch),
        ]
        write_gif(frames, output.resolve())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build the README demo GIF from real local artifacts.")
    result.add_argument("--repo", default=".")
    result.add_argument("--output", default="docs/demo.gif")
    result.add_argument("--scratch-parent")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        build(
            Path(args.repo),
            Path(args.output),
            Path(args.scratch_parent) if args.scratch_parent else None,
        )
    except (OSError, subprocess.SubprocessError, DemoGifError) as exc:
        raise SystemExit(f"demo GIF generation failed: {exc}") from exc
    output = Path(args.output).resolve()
    print(f"{output} ({output.stat().st_size} bytes, {sum(FRAME_DURATIONS_MS)} ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
