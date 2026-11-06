"""
OCR 识别引擎 - NumPy矢量化优化版

性能优化:
1. 使用NumPy数组批量处理文本块坐标
2. 向量化的中心点计算和排序
3. 优化文本提取和过滤流程
4. 全局单例 OCR 实例（不再按线程各建一份），并支持预热
5. 检测阶段缩放策略可配置（默认 max，避免小图被放大后识别变慢）
"""
import threading
import numpy as np
from PIL import Image, ImageOps
from core.config_manager import get_config
from core.text_corrector import correct_text
from utils.paths import resolve_path


class OCREngine:
    """OCR 识别引擎 - 优化版"""

    def __init__(self):
        self.config = get_config()
        self.min_text_length = self.config.get("ocr", "min_text_length", default=1)
        self.line_tolerance = self.config.get("ocr", "line_tolerance", default=20)

        # 检测阶段缩放策略（性能关键）
        # RapidOCR 默认 limit_type="min"：把图片短边放大到 limit_side_len(736)。
        # 框选/固定区域通常是 800x120 这种小图，会被放大成 4900x736，
        # 单次识别从 0.1 秒变成 1.2 秒（实测）。
        # 改成 limit_type="max" 后：小图按原分辨率识别（不放大），大图才等比缩小。
        self.det_limit_type = str(
            self.config.get("ocr", "det_limit_type", default="max")
        ).lower()
        try:
            self.det_limit_side_len = int(
                self.config.get("ocr", "det_limit_side_len", default=1280)
            )
        except (TypeError, ValueError):
            self.det_limit_side_len = 1280

        # 方向分类（把 180° 倒置的文字转正）。屏幕截图几乎不会倒置，
        # 关掉可省一点时间；默认保持开启以免影响识别正确率。
        self.use_angle_cls = bool(
            self.config.get("ocr", "use_angle_cls", default=True)
        )

        # ONNX Runtime 的算子内并行线程数。
        # 默认 -1 会按物理核心数开满（8 核机器上是 8~16 条），
        # 单次小图推理根本吃不满，反而让 CPU 瞬间冲到 100%、温度飙升。
        # 限制在 4 条后峰值明显下降，单帧耗时几乎不变。
        try:
            self.intra_op_num_threads = int(
                self.config.get("ocr", "intra_op_num_threads", default=4)
            )
        except (TypeError, ValueError):
            self.intra_op_num_threads = 4

        # 推理设备（"cpu" / "cuda:0" ...），由设置页「功能设置 → 推理设备」选择。
        # 具体解析延迟到建模型时做：这里只记录配置值，避免启动时调用 nvidia-smi。
        self.device_id = str(
            self.config.get("ocr", "device", default="cpu") or "cpu"
        )

        # 全局单例 + 两把锁：
        # - _init_lock  : 只让一个线程创建模型（避免重复加载）
        # - _infer_lock : RapidOCR/onnxruntime 实例不保证线程安全，推理串行化
        self._ocr = None
        self._init_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def _resolve_device(self):
        """解析配置里的设备标识（不可用时回退 CPU）

        返回 (设备信息, 是否发生回退)
        """
        from core import device_manager

        requested = self.device_id
        device = device_manager.resolve_device(requested)
        fell_back = requested != device["id"]
        if fell_back:
            print(
                f"[OCR] 设备 {requested} 不可用，已回退到 CPU。"
                f"原因参考: {device_manager.diagnostic_text()}"
            )
        return device, fell_back

    def _build_params(self):
        """组装 RapidOCR 参数（只覆盖需要调整的项）"""
        params = {
            "Det.limit_type": self.det_limit_type,
            "Det.limit_side_len": self.det_limit_side_len,
        }
        # 外置模型相对应用目录，显式配置路径优先。
        for component, key, filename in [
            ('Det', 'det_model_path', 'PP-OCRv6_det_small.onnx'),
            ('Cls', 'cls_model_path', 'ch_ppocr_mobile_v2.0_cls_mobile.onnx'),
            ('Rec', 'rec_model_path', 'PP-OCRv6_rec_small.onnx'),
        ]:
            configured=self.config.get('ocr',key,default='')
            path=resolve_path(configured) if configured else resolve_path('models/ocr/'+filename)
            # 部署配置可能来自另一台机器；本地自定义路径失效时安全回退到
            # 当前便携包随附的通用 OCR 推理权重。
            if not path.is_file():path=resolve_path('models/ocr/'+filename)
            if not path.is_file():
                raise FileNotFoundError(f'OCR 模型文件不存在：{path}')
            params[component + '.model_path'] = str(path)
        if not self.use_angle_cls:
            params["Global.use_cls"] = False

        device, _ = self._resolve_device()
        device_id = device["id"]

        # 线程数只对 CPU 有意义：GPU 推理时开多线程反而会和 CUDA 抢调度
        if device_id != "cpu" and self.intra_op_num_threads > 0:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = self.intra_op_num_threads
            # 单图推理没有并行的子图，inter_op 开多线程只是多一份线程池
            params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1

        if device_id != "cpu":
            params["EngineConfig.onnxruntime.use_cuda"] = True
            # 多卡机器上指定具体哪一块（"cuda:1" -> device_id=1）
            try:
                gpu_index = int(str(device_id).split(":")[-1])
            except (TypeError, ValueError):
                gpu_index = 0
            params["EngineConfig.onnxruntime.cuda_device_id"] = gpu_index

        return params

    def _get_ocr(self):
        """获取全局 OCR 实例（首次调用时加载模型，约 0.2~0.4 秒）"""
        if self._ocr is not None:
            return self._ocr
        with self._init_lock:
            if self._ocr is None:
                self._ocr = self._build_ocr()
        return self._ocr

    def _build_ocr(self):
        """创建 RapidOCR 实例；GPU 失败时回退 CPU。"""
        device, _ = self._resolve_device()
        # rapidocr 延迟导入：它会把 onnxruntime + cv2 一起拉进来（约 80MB），
        # 只有在真的要用 OCR 时才值得付这笔钱。
        from rapidocr import RapidOCR

        try:
            ocr = RapidOCR(params=self._build_params())
            print(f"[OCR] 已加载引擎，推理设备: {device['id']}")
            return ocr
        except Exception as e:
            # GPU 初始化失败（常见于缺 cuDNN / 显存不足）时退回 CPU，
            # 不能让整个识别功能直接不可用。
            if device["id"] == "cpu":
                raise
            print(f"[OCR] 使用 {device['id']} 初始化失败，回退 CPU: {e}")
            self.device_id = "cpu"
            return RapidOCR(params=self._build_params())

    def reload_device(self, device_id: str = None):
        """切换推理设备并释放已加载的引擎（下次识别时按新设备重建）

        Args:
            device_id: 目标设备标识；为 None 时沿用当前实例的 self.device_id

        与 UI 线程的约定：本方法只释放、不加载。GPU 初始化可能耗时数秒，
        绝不能放在界面线程同步执行，真正的加载交给下次识别时的懒加载。
        """
        target = device_id or self.device_id
        self.device_id = target

        # 置空引用即可：正在使用旧实例的推理线程持有自己的局部引用会自然跑完，
        # 之后由 GC 回收。这里只保证「下次识别会重新按新设备建引擎」。
        self._ocr = None
        print(f"[OCR] 推理设备已切换为 {target}，引擎将在下次识别时重新加载")
        return True

    def warmup(self):
        """预热：提前加载模型并空跑一次推理

        不预热的话，第一次点击功能1/功能2 要等模型加载 + 首次推理图优化，
        表现为"第一下特别慢"。在后台线程调用即可，不阻塞界面。
        """
        try:
            ocr = self._get_ocr()
            blank = Image.new("RGB", (320, 80), (255, 255, 255))
            with self._infer_lock:
                ocr(blank)
            print("[OCR] 预热完成")
        except Exception as e:
            print(f"[OCR] 预热失败: {e}")

    def recognize(self, image):
        """识别图片中的文字"""
        # 快速预处理：只做必要的转换
        try:
            image = ImageOps.exif_transpose(image)
        except Exception:
            pass

        if image.mode != "RGB":
            image = image.convert("RGB")

        # 直接识别（串行化：同一实例不并发推理，避免抢 CPU 反而更慢）
        try:
            with self._infer_lock:
                result = self._get_ocr()(image)
        except Exception as e:
            print(f"[OCR] 识别失败: {e}")
            return ""

        # 提取文本（已按位置排序）
        texts = self._extract_texts(result)
        if not texts:
            return ""

        output = self._filter_texts(texts)

        if not output:
            return ""

        # 拼接并清理空格
        result_text = " ".join(output)
        result_text = " ".join(result_text.split())

        # 应用文本修正
        result_text = correct_text(result_text)

        return result_text

    def _filter_texts(self, texts):
        """
        使用向量化操作过滤文本
        优化: 批量处理文本长度检查
        """
        return [
            cleaned for text in texts
            if (cleaned := str(text).strip())
            and len(cleaned) >= self.min_text_length
        ]

    def _extract_texts(self, result):
        """提取识别结果中的文本,并按位置排序"""
        if result is None:
            return []

        # 提取文本块和位置信息
        text_blocks = []

        # 新版 RapidOCR - result 是一个对象
        if hasattr(result, "boxes") and hasattr(result, "txts"):
            boxes = result.boxes
            txts = result.txts

            if boxes is None or txts is None:
                return []

            for i, text in enumerate(txts):
                if i < len(boxes) and boxes[i] is not None:
                    text_blocks.append((boxes[i], text))
                else:
                    text_blocks.append((None, text))

        # 旧版 RapidOCR - result 是 tuple/list
        elif isinstance(result, (tuple, list)) and len(result) > 0:
            if isinstance(result[0], (tuple, list)) and len(result[0]) > 0:
                for item in result[0]:
                    if isinstance(item, (tuple, list)) and len(item) >= 2:
                        text_blocks.append((item[0], item[1]))
            elif hasattr(result[0], "txts"):
                txts = result[0].txts
                boxes = result[0].boxes if hasattr(result[0], "boxes") else []
                if txts is not None:
                    for i, text in enumerate(txts):
                        box = boxes[i] if boxes is not None and i < len(boxes) else None
                        text_blocks.append((box, text))

        # 如果没有提取到任何内容
        if not text_blocks:
            if hasattr(result, "txts") and result.txts is not None:
                return list(result.txts)
            return []

        # 按位置排序（向量化优化）
        sorted_blocks = self._sort_text_blocks_vectorized(text_blocks)

        return [text for _, text in sorted_blocks]

    def _sort_text_blocks_vectorized(self, text_blocks):
        """
        按阅读顺序排序文本块(从上到下,从左到右) - NumPy优化版

        优化: 使用NumPy数组批量计算所有文本块的中心坐标并排序

        Args:
            text_blocks: [(box, text), ...] 其中 box 是 4 个点的坐标或 None

        Returns:
            排序后的 text_blocks
        """
        # 分离有位置信息和无位置信息的文本块
        blocks_with_pos = [(box, text) for box, text in text_blocks if box is not None]
        blocks_without_pos = [(box, text) for box, text in text_blocks if box is None]

        if not blocks_with_pos:
            return text_blocks

        # 提取所有boxes到列表
        boxes = [box for box, _ in blocks_with_pos]

        # 批量计算中心坐标（向量化）
        centers = self._batch_calculate_centers_vectorized(boxes)

        # 使用NumPy排序
        # 将 y 坐标量化到行（使用容差值）
        rows = (centers[:, 0] / self.line_tolerance).astype(np.int32)
        x_coords = centers[:, 1]

        # 创建排序索引：先按行，再按x坐标
        sort_keys = np.column_stack([rows, x_coords])
        sort_indices = np.lexsort((sort_keys[:, 1], sort_keys[:, 0]))

        # 应用排序
        sorted_with_pos = [blocks_with_pos[i] for i in sort_indices]

        # 将无位置信息的文本块放到最后
        return sorted_with_pos + blocks_without_pos

    def _batch_calculate_centers_vectorized(self, boxes):
        """
        批量计算所有文本框的中心坐标
        优化: 使用NumPy向量化计算，一次性处理所有boxes

        Args:
            boxes: 文本框列表，每个box是4个点 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]

        Returns:
            NumPy数组，shape=(n, 2)，每行是 [center_y, center_x]
        """
        centers = []

        for box in boxes:
            try:
                if isinstance(box, (list, tuple)) or hasattr(box, '__len__'):
                    if len(box) >= 4:
                        # 转换为NumPy数组
                        points = np.array([
                            [float(point[0]), float(point[1])]
                            for point in box[:4]
                            if len(point) >= 2
                        ])

                        if len(points) > 0:
                            # 向量化计算平均值
                            center_x = np.mean(points[:, 0])
                            center_y = np.mean(points[:, 1])
                            centers.append([center_y, center_x])
                            continue
            except (IndexError, TypeError, ValueError):
                pass

            # 无效位置放到最后
            centers.append([999999.0, 999999.0])

        return np.array(centers)

    def _get_center_y_x(self, box):
        """
        获取文本框的中心坐标 (y, x) - 保留用于兼容性

        Args:
            box: 4个角点 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]

        Returns:
            (center_y, center_x)
        """
        try:
            if isinstance(box, (list, tuple)) or hasattr(box, '__len__'):
                if len(box) >= 4:
                    # 使用NumPy计算
                    points = np.array([
                        [float(point[0]), float(point[1])]
                        for point in box[:4]
                        if len(point) >= 2
                    ])

                    if len(points) > 0:
                        center_x = np.mean(points[:, 0])
                        center_y = np.mean(points[:, 1])
                        return (center_y, center_x)
        except (IndexError, TypeError, ValueError):
            pass

        return (999999.0, 999999.0)
