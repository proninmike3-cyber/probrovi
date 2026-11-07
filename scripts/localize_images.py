#!/usr/bin/env python3
"""Mirror static site images into GitHub Pages repository; preserve originals.

One-time migration: fetch each remote <img> and icon, write it to assets/images,
and update index.html to refer to the local image. Any failed fetch aborts the
migration instead of committing broken image links.
"""
from __future__ import annotations

import hashlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
IMAGE_DIR = ROOT / "assets" / "images"

# Stable file names for the exact image URLs used in the live demo.
SOURCES: tuple[tuple[str, str], ...] = (
    ("logo-probrovi", "https://avatars.mds.yandex.net/get-altay/19799298/2a0000019e87a5fe6006bc116a706b59c995/L"),
    ("hero-probrovi", "https://f1.dikidi.net/c10/v9716/knab053792.jpg"),
    ("brows-service", "https://f2.dikidi.net/c18/v17481/5qahk5e32f.jpg"),
    ("permanent-service", "https://f2.dikidi.net/c18/v17481/6di8p1nh3o.jpg"),
    ("laser-service", "https://images.unsplash.com/photo-1700760933941-3a06a28fbf47?auto=format&fit=crop&w=900&q=82"),
    ("studio-interior", "https://f1.dikidi.net/c12/v11610/5q1imcq00p.jpg"),
    ("elena-photo", "https://f2.dikidi.net/c18/v17481/442esg2nmq.jpg"),
    ("regina-work", "https://f2.dikidi.net/c18/v17481/1tit4m913e.jpg"),
    ("portfolio-brows-1", "https://f2.dikidi.net/c18/v17481/3ssoit5pru.jpg"),
    ("portfolio-brows-2", "https://f2.dikidi.net/c18/v17481/995u3ogodr.jpg"),
    ("portfolio-lashes", "https://f2.dikidi.net/c18/v17481/2513p3b22p.jpg"),
    ("portfolio-brows-3", "https://f2.dikidi.net/c18/v17481/4d7ea3e8ng.jpg"),
    ("portfolio-lips", "https://f1.dikidi.net/c11/v10964/6ttfn6hm09.jpg"),
)

def image_extension(data: bytes) -> str:
    """Infer the true format from file signature, not the HTTP MIME or URL."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    raise ValueError("The server did not return a recognized raster image")

def fetch_image(url: str) -> tuple[bytes, str]:
    """Try the original host, then a trusted image-resize proxy as last resort."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    refs = {
        "dikidi.net": "https://dikidi.net/",
        "yandex.net": "https://yandex.ru/maps/",
        "unsplash.com": "https://unsplash.com/",
    }
    referer = next((r for h, r in refs.items() if h in host), "https://www.google.com/")
    attempts = [
        (url, referer),
        (url, "https://www.google.com/"),
        ("https://images.weserv.nl/?url=" + urllib.parse.quote(url, safe=""), "https://images.weserv.nl/"),
    ]
    last_error = None
    for attempt, (target, current_referer) in enumerate(attempts, start=1):
        request = urllib.request.Request(
            target,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36",
                "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.1",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Referer": current_referer,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                data = response.read(12 * 1024 * 1024 + 1)
                if len(data) > 12 * 1024 * 1024 or len(data) < 200:
                    raise ValueError("Image size outside safe range")
                ext = image_extension(data)
                return data, ext
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
            last_error = exc
            print(f"Attempt {attempt} failed for {url}: {exc}", flush=True)
            time.sleep(attempt)
    raise RuntimeError(f"Could not download {url}: {last_error}")

def main() -> None:
    original = INDEX.read_text(encoding="utf-8")
    updated = original
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    for name, url in SOURCES:
        # Already migrated sites remain unchanged during a manual rerun.
        if url not in updated:
            existing = list(IMAGE_DIR.glob(name + ".*"))
            if len(existing) == 1 and existing[0].stat().st_size > 200:
                print(f"Already local: {existing[0].relative_to(ROOT)}")
                continue
            raise RuntimeError(f"Expected source URL missing from HTML: {url}")

        data, ext = fetch_image(url)
        dest = IMAGE_DIR / f"{name}.{ext}"
        dest.write_bytes(data)
        local_path = dest.relative_to(ROOT).as_posix()
        updated = updated.replace(url, local_path)
        digest = hashlib.sha256(data).hexdigest()[:12]
        print(f"Saved {local_path} ({len(data):,} bytes, sha256 {digest})", flush=True)

    # Remove preconnect hints used only to fetch remote images.
    updated = re.sub(
        r'^ *<link rel="preconnect" href="https://(?:avatars\.mds\.yandex\.net|f[12]\.dikidi\.net)" crossorigin />\s*\n',
        "",
        updated,
        flags=re.MULTILINE,
    )

    remote_images = re.findall(
        r'<img\b[^>]*\bsrc=["\'](https?://[^"\']+)',
        updated,
        flags=re.IGNORECASE,
    )
    if remote_images:
        raise RuntimeError(f"Remaining external <img> URLs: {remote_images}")
    external_icons = re.findall(r'<link[^>]*rel="icon"[^>]*href=["\']https?://', updated)
    if external_icons:
        raise RuntimeError("The favicon is still loaded externally")

    local_srcs = re.findall(r'<img\b[^>]*\bsrc=["\'](assets/[^"\']+)', updated, flags=re.I)
    local_srcs += re.findall(r'<link[^>]*rel="icon"[^>]*href=["\'](assets/[^"\']+)', updated, flags=re.I)
    for src in set(local_srcs):
        asset = ROOT / src
        if not asset.is_file() or asset.stat().st_size == 0:
            raise RuntimeError(f"Missing local image referenced in HTML: {src}")

    if not (ROOT / "assets/eyelashes_card.webp").exists():
        raise RuntimeError("The previously uploaded eyelash photo was not found")

    INDEX.write_text(updated, encoding="utf-8")
    print(f"Success: {len(SOURCES)} remote originals mirrored and HTML switched to local assets.")
    print(f"Verified {len(set(local_srcs))} local image file paths. No external img/src remains.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"IMAGE MIRROR FAILURE: {exc}", file=sys.stderr)
        sys.exit(1)
