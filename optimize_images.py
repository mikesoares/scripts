#!/usr/bin/env python3
"""
Image Optimization Script for QSDA

Converts all JPG/PNG images in the WordPress webroot to WebP format.
Keeps original files alongside WebP versions so existing HTML references
continue to work while new references can use WebP.

Also generates a mapping file that can be used to update HTML references.

Usage:
    python scripts/optimize_images.py

Scans: tmp/wordpress/ (excluding wp-admin, wp-includes, wp-content/themes, wp-content/plugins)
"""

import os
import sys
from pathlib import Path
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
WP_DIR = os.path.join(PROJECT_ROOT, "tmp", "wordpress")

# Quality settings
WEBP_QUALITY = 80  # Good balance of size vs quality for photos
MAX_WIDTH = 1920   # Max image width (hero-sized)

# Directories to skip (WordPress core, themes, plugins — don't optimize their assets)
SKIP_DIRS = {
    "wp-admin",
    "wp-includes",
    "wp-content/themes",
    "wp-content/plugins",
    "wp-content/uploads",
    ".git",
    ".claude",
}

# Image extensions to convert
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Skip tiny images (icons, spacers) — only optimize images > 1 KB
MIN_FILE_SIZE = 1024  # bytes


def should_skip_dir(dirpath: str) -> bool:
    """Check if a directory should be skipped during scanning."""
    rel = os.path.relpath(dirpath, WP_DIR)
    for skip in SKIP_DIRS:
        if rel == skip or rel.startswith(skip + os.sep):
            return True
    return False


def optimize_image(filepath: str) -> dict:
    """
    Convert a single image to WebP format.

    Returns a dict with conversion stats:
    - original_size: bytes
    - webp_size: bytes
    - saved: bytes saved
    - skipped: True if conversion was skipped
    - reason: why it was skipped (if applicable)
    """
    original_size = os.path.getsize(filepath)

    if original_size < MIN_FILE_SIZE:
        return {
            "skipped": True,
            "reason": "too small",
            "original_size": original_size,
        }

    webp_path = os.path.splitext(filepath)[0] + ".webp"

    # Skip if WebP already exists and is newer
    if os.path.exists(webp_path):
        if os.path.getmtime(webp_path) >= os.path.getmtime(filepath):
            return {
                "skipped": True,
                "reason": "webp exists",
                "original_size": original_size,
            }

    try:
        with Image.open(filepath) as img:
            # Convert RGBA to RGB for JPEG-sourced images (WebP supports alpha but
            # photos don't need it)
            if img.mode == "RGBA":
                # Check if image actually uses transparency
                extrema = img.getextrema()
                if extrema[3][0] == 255:
                    # Fully opaque — convert to RGB
                    img = img.convert("RGB")

            # Resize if too wide
            if img.width > MAX_WIDTH:
                ratio = MAX_WIDTH / img.width
                new_height = int(img.height * ratio)
                img = img.resize((MAX_WIDTH, new_height), Image.LANCZOS)

            # Save as WebP
            img.save(webp_path, "WEBP", quality=WEBP_QUALITY, method=4)

        webp_size = os.path.getsize(webp_path)

        return {
            "skipped": False,
            "original_size": original_size,
            "webp_size": webp_size,
            "saved": original_size - webp_size,
        }
    except Exception as e:
        return {
            "skipped": True,
            "reason": f"error: {e}",
            "original_size": original_size,
        }


def main():
    if not os.path.exists(WP_DIR):
        print(f"ERROR: WordPress directory not found at {WP_DIR}")
        sys.exit(1)

    print(f"Scanning {WP_DIR} for images to optimize...")

    # Collect all images
    images = []
    for dirpath, dirnames, filenames in os.walk(WP_DIR):
        if should_skip_dir(dirpath):
            # Prune this directory from further walking
            dirnames.clear()
            continue

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                images.append(os.path.join(dirpath, filename))

    print(f"Found {len(images)} images to process\n")

    # Process
    stats = {
        "converted": 0,
        "skipped": 0,
        "total_original": 0,
        "total_webp": 0,
        "total_saved": 0,
        "errors": [],
    }

    for i, filepath in enumerate(sorted(images)):
        rel_path = os.path.relpath(filepath, WP_DIR)
        result = optimize_image(filepath)

        if result["skipped"]:
            stats["skipped"] += 1
            reason = result.get("reason", "unknown")
            if reason.startswith("error"):
                stats["errors"].append(f"{rel_path}: {reason}")
                print(f"  [{i+1:4d}/{len(images)}] SKIP  {rel_path} ({reason})")
        else:
            stats["converted"] += 1
            stats["total_original"] += result["original_size"]
            stats["total_webp"] += result["webp_size"]
            stats["total_saved"] += result["saved"]
            savings_pct = (
                (result["saved"] / result["original_size"] * 100)
                if result["original_size"] > 0
                else 0
            )
            print(
                f"  [{i+1:4d}/{len(images)}] OK    {rel_path} "
                f"({result['original_size']:,} → {result['webp_size']:,} bytes, "
                f"-{savings_pct:.0f}%)"
            )

    # Summary
    print(f"\n{'='*60}")
    print(f"Image Optimization Complete")
    print(f"{'='*60}")
    print(f"Converted:  {stats['converted']}")
    print(f"Skipped:    {stats['skipped']}")
    if stats["total_original"] > 0:
        total_pct = stats["total_saved"] / stats["total_original"] * 100
        print(
            f"Size:       {stats['total_original']/1024/1024:.1f} MB → "
            f"{stats['total_webp']/1024/1024:.1f} MB "
            f"(saved {stats['total_saved']/1024/1024:.1f} MB, -{total_pct:.0f}%)"
        )
    if stats["errors"]:
        print(f"\nErrors ({len(stats['errors'])}):")
        for err in stats["errors"]:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
