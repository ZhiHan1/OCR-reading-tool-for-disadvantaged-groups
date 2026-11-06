# utils/screenshot.py
import threading

import numpy as np
from PIL import Image, ImageGrab

from core.config_manager import get_config


_save_lock = threading.Lock()

# 变化检测用的缩略图尺寸：把整块区域缩成 128x32 的灰度图
_SIGNATURE_SIZE = (128, 32)


def grab_all_screens():
    """
    截取所有屏幕拼成的虚拟桌面。
    """
    return ImageGrab.grab(all_screens=True).convert("RGB")


def grab_region(region):
    """
    截取指定全局坐标区域。
    region: (x1, y1, x2, y2)
    """
    return ImageGrab.grab(
        bbox=region, all_screens=True
    ).convert("RGB")


def frame_signature(image):
    """
    生成画面的轻量指纹（128x32 灰度缩略图）。

    屏幕截图的像素是确定性的：画面没变时重复截图得到完全相同的位图，
    所以指纹也完全相同，可以直接比较，开销远低于一次 OCR 推理。
    128x32 的粒度足以让一个字的出现/消失改变至少一个格子。
    """
    small = image.convert("L").resize(_SIGNATURE_SIZE, Image.BILINEAR)
    # uint8 已足够表示灰度像素，减少指纹内存和比较开销。
    return np.asarray(small, dtype=np.uint8)


def frame_changed(signature, previous, threshold: float = 0.0) -> bool:
    """
    比较两帧指纹，判断画面是否变化。

    Args:
        signature: 当前帧指纹
        previous: 上一帧指纹（None 表示第一次，直接算变化）
        threshold: 平均像素差阈值。默认 0（像素完全一致才算没变），
                   因为截图是确定性的，留容差反而可能漏掉细微的文字变化。

    Returns:
        True 表示画面有变化，需要重新识别
    """
    if previous is None or signature is None:
        return True

    if signature.shape != previous.shape:
        return True

    # 默认阈值为 0 时使用底层精确比较，避免创建差值数组。
    if threshold <= 0:
        return not np.array_equal(signature, previous)

    return float(np.abs(signature.astype(np.int16) - previous.astype(np.int16)).mean()) > threshold



def save_processed_image(image):
    """
    保存 OCR 处理的图到缓存目录。
    多线程调用安全。
    """
    config = get_config()
    save_image = config.get("debug", "save_processed_image", default=False)

    if not save_image:
        return

    from pathlib import Path
    cache_dir = Path(config.get("paths", "cache_dir", default="缓存"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    screenshot_filename = config.get("paths", "screenshot_filename", default="截图.png")
    processed_image_path = cache_dir / screenshot_filename

    with _save_lock:
        image.convert("RGB").save(
            processed_image_path, format="PNG"
        )
