"""Measure the local authored-render cache boundary without invoking any model.

Run from ``servers/fastapi`` so the backend package is importable:
    uv run python ../../scripts/bench_authored_render_policy.py --samples 3
"""

import argparse
import asyncio
import statistics
import time

from utils import slide_capture


def _html(sample: int) -> str:
    return f"""<!doctype html><html><head><style>
    html,body{{margin:0;width:1280px;height:720px;overflow:hidden;background:#fff}}
    .small{{position:absolute;left:40px;top:40px;font:400 8px/11px Arial,\"Malgun Gothic\",sans-serif;color:#111}}
    .korean{{position:absolute;left:40px;top:64px;width:120px;font:700 10px/14px Arial,\"Malgun Gothic\",sans-serif;color:#111}}
    .photo{{position:absolute;left:200px;top:100px;width:240px;height:120px;object-fit:cover}}
    table{{position:absolute;left:700px;top:100px;border-collapse:collapse;font:700 10px/12px Arial,\"Malgun Gothic\",sans-serif}} td{{border:1px solid #222;padding:2px}}
    </style></head><body><span class=\"small\">8px render policy sample {sample}</span><span class=\"korean\">한글 English ① ≤ → fallback sample</span>
    <img class=\"photo\" alt=\"benchmark image\" src=\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='120'%3E%3Crect width='240' height='120' fill='%230072ce'/%3E%3C/svg%3E\"><table><tr><td>한글</td><td>English</td></tr><tr><td>① ≤</td><td>42%</td></tr></table></body></html>"""


async def _measure(samples: int) -> None:
    if not slide_capture.find_chrome():
        raise RuntimeError("No Chrome/Playwright headless shell found")

    original_version = slide_capture.RENDER_CACHE_VERSION
    original_size = slide_capture.RENDER_CACHE_SIZE
    slide_capture._render_cache.clear()
    slide_capture._render_inflight.clear()
    slide_capture.RENDER_CACHE_SIZE = max(1, original_size)
    try:
        cold_seconds = []
        for index in range(samples):
            started = time.perf_counter()
            await slide_capture.render_html_to_png(_html(index))
            cold_seconds.append(time.perf_counter() - started)

        cached_html = _html(999)
        slide_capture._render_cache.clear()
        started = time.perf_counter()
        first_png = await slide_capture.render_html_to_png(cached_html)
        first_seconds = time.perf_counter() - started
        started = time.perf_counter()
        cached_png = await slide_capture.render_html_to_png(cached_html)
        cache_hit_seconds = time.perf_counter() - started

        slide_capture.RENDER_CACHE_VERSION = f"{original_version}-bench-next-policy"
        started = time.perf_counter()
        invalidated_png = await slide_capture.render_html_to_png(cached_html)
        invalidated_seconds = time.perf_counter() - started
    finally:
        slide_capture.RENDER_CACHE_VERSION = original_version
        slide_capture.RENDER_CACHE_SIZE = original_size
        slide_capture._render_cache.clear()
        slide_capture._render_inflight.clear()

    mean_cold = statistics.mean(cold_seconds)
    print("model_calls=0 (renderer/cache-only benchmark)")
    print(f"cold_unique_samples={samples}; mean_seconds={mean_cold:.3f}; samples={cold_seconds}")
    print(f"first_render_seconds={first_seconds:.3f}; cache_hit_seconds={cache_hit_seconds:.6f}")
    print(f"cache_hit_identical_png={cached_png == first_png}")
    print(f"policy_version_invalidation_seconds={invalidated_seconds:.3f}; identical_png={invalidated_png == first_png}")
    print(f"estimated_90_unique_serial_seconds={mean_cold * 90:.1f}")
    print(
        "estimated_90_unique_seconds_at_render_concurrency_"
        f"{slide_capture.RENDER_CONCURRENCY}={mean_cold * 90 / slide_capture.RENDER_CONCURRENCY:.1f}"
    )
    print(f"estimated_90_identical_cached_seconds={first_seconds + (89 * cache_hit_seconds):.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    asyncio.run(_measure(args.samples))


if __name__ == "__main__":
    main()
