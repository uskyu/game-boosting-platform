"""一次性批量压缩 uploads 历史大图（幂等，可安全重复运行）。

与运行时 file_service 的压缩参数保持一致：>2MB 的图重编码为最长边 2000px、
JPEG 质量 82。只处理文件名以 .jpg/.jpeg 结尾的文件（PNG/WEBP 保留原样，
避免透明通道/扩展名与实际格式不一致的问题）；压完都 <1MB，重复运行自动跳过。

用法（容器内）：
    python -m scripts.compress_uploads          # 预览 + 执行
    python -m scripts.compress_uploads --dry-run
"""

import argparse
import sys
from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image

from app.core.config import settings

THRESHOLD = 2 * 1024 * 1024
MAX_EDGE = 2000
QUALITY = 82
EXTENSIONS = {".jpg", ".jpeg"}


def compress_file(path: Path, *, dry_run: bool = False) -> tuple[bool, int, int]:
    """压缩单张图，返回 (是否压缩, 原字节数, 压后字节数)。dry_run 只统计不写入。"""
    data = path.read_bytes()
    if len(data) <= THRESHOLD:
        return False, len(data), len(data)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(data)) as image:
                image.load()
        except Exception as exc:  # 损坏文件跳过，不影响其他文件
            print(f"SKIP (decode failed) {path}: {exc}")
            return False, len(data), len(data)
        if max(image.width, image.height) > MAX_EDGE:
            scale = MAX_EDGE / max(image.width, image.height)
            image = image.resize(
                (round(image.width * scale), round(image.height * scale)),
                Image.LANCZOS,
            )
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=QUALITY, optimize=True)
    new = buf.getvalue()
    if len(new) >= len(data):
        return False, len(data), len(data)
    if dry_run:
        return True, len(data), len(new)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(new)
    tmp.replace(path)
    return True, len(data), len(new)


def main() -> int:
    parser = argparse.ArgumentParser(description="批量压缩 uploads 历史大图")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    root = Path(settings.UPLOAD_DIR)
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS]
    big = [p for p in files if p.stat().st_size > THRESHOLD]
    print(f"total={len(files)} big(>2MB)={len(big)}")

    total_before = total_after = 0
    changed = 0
    for path in big:
        before_bytes = path.stat().st_size
        ok, _, after_bytes = compress_file(path, dry_run=args.dry_run)
        total_before += before_bytes
        total_after += after_bytes
        if ok:
            changed += 1
        print(
            f"{'OK ' if ok else 'SKIP'} {path.relative_to(root)} "
            f"{before_bytes/1048576:.1f}MB -> {after_bytes/1048576:.1f}MB"
        )
    print(f"compressed={changed} saved={(total_before-total_after)/1048576:.0f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
