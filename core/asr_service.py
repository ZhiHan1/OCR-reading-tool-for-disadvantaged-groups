"""语音识别（ASR）服务 - 语音转文字

职责：
- 统一封装「本地离线（FunASR） / 在线（OpenAI 兼容，待补）」两种识别后端。
- 对外提供同一个 `recognize(audio: np.ndarray) -> str` 接口，调用方无需关心后端。
- 本地模型懒加载：首次识别时才加载，避免启动变慢；加载失败给出明确提示。

设计说明：
- 后端切换由 `config.yaml` 的 `asr.backend` 决定（"local" / "online"）。
- 本地模型默认用 FunASR 的 SenseVoiceSmall（中文准确、CPU 可跑、延迟低）；
  后续可在设置页选择 Paraformer-large 等更高精度模型。
- 模型加载较重，放在后台线程由控制器触发，这里用锁保证只加载一次。
"""
import os
import re
import threading
from typing import Optional

import numpy as np

from core.config_manager import get_config
from utils.paths import app_root, resolve_path

# 后端标识
BACKEND_LOCAL = "local"
BACKEND_ONLINE = "online"

# 本地模型名称 -> FunASR 模型标识
# Paraformer-large：中文识别精度更高（CER 更低），当前项目专用模型。
# 曾评估过 SenseVoice-Small（CPU 版），结论是不划算：INT8 量化后体积只差 4 MB
# （234.1 vs 230.0），速度差异用户感知不到，却多一个 BPE 中文路径的坑。
# 详见 KNOWLEDGE/asr.md §8.10（KB-ASR-009）。已移除，不再作为可选项。
LOCAL_MODELS = {
    "paraformer_large": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
}

# 模型的中文可读名（设置页下拉展示）
LOCAL_MODEL_LABELS = {
    "paraformer_large": "Paraformer-Large（精度更高）",
}

# 各模型的加载参数：
# - Paraformer-Large 使用官方 vad+punc 组合权重（自带端点检测与标点），
#   因此不再额外挂 punc_model，避免重复加标点。
LOCAL_MODEL_LOAD_ARGS = {
    "paraformer_large": {"punc_model": "ct-punc"},
}

# 默认采样率（FunASR 输入）
SAMPLE_RATE = 16000

# 项目内本地模型目录（相对项目根目录）。存在完整模型时优先使用本地路径，
# 避免 FunASR 再去 ModelScope 联网下载/校验。
_ROOT = str(app_root())
LOCAL_MODEL_DIRS = {
    "paraformer_large": os.path.join(_ROOT, "模型", "语音", "paraformer-large"),
}
SUPPORT_MODEL_DIRS = {
    "fsmn-vad": os.path.join(_ROOT, "models", "asr", "fsmn-vad"),
    "ct-punc": os.path.join(_ROOT, "models", "asr", "ct-punc"),
}


def _is_model_dir(path: str) -> bool:
    """目录里是否有 FunASR 模型文件（config.yaml + 权重之一）。"""
    if not path or not os.path.isdir(path):
        return False
    has_config = os.path.exists(os.path.join(path, "config.yaml"))
    has_weight = any(
        os.path.exists(os.path.join(path, name))
        for name in ("model.pt", "model.pb", "model.onnx", "pytorch_model.bin")
    )
    return has_config and has_weight


def _resolve(model_id: str, local_dir: str) -> str:
    """优先返回项目内本地目录，否则回退到 ModelScope 模型 ID。"""
    return local_dir if _is_model_dir(local_dir) else model_id


_TAG_RE = re.compile(r"<\|[^|]*\|>")

# SenseVoice 的 SentencePiece 分词器在 Windows 上无法打开含中文的路径
# （C++ 侧按 ANSI 打开文件）。这里把 bpe 文件复制到纯 ASCII 目录再加载。
def _pick_ascii_cache_dir() -> str:
    public = os.environ.get("PUBLIC")
    candidates = []
    if public:
        candidates.append(os.path.join(public, "ocr_asr_cache"))
    drive = os.path.splitdrive(_ROOT)[0]
    if drive:
        candidates.append(drive + "\\ocr_asr_cache")
    candidates.append(os.path.join(os.path.dirname(_ROOT), "ocr_asr_cache"))
    for path in candidates:
        if path.isascii():
            return path
    return candidates[-1]


_ASCII_CACHE_DIR = _pick_ascii_cache_dir()


def _ascii_bpe_path(src: str) -> str:
    """返回 bpe 文件的 ASCII 路径（必要时复制到缓存目录）。"""
    if os.path.abspath(src).isascii():
        return src
    import shutil

    os.makedirs(_ASCII_CACHE_DIR, exist_ok=True)
    dst = os.path.join(_ASCII_CACHE_DIR, os.path.basename(src))
    try:
        if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
            shutil.copy2(src, dst)
        return dst
    except Exception as e:  # noqa: BLE001
        print(f"[语音识别] 复制分词器文件失败（将尝试原路径）: {e}")
        return src


# Paraformer 输出是按 token 分开的（"今 天 天 气"），中文之间需要去掉空格；
# 英文单词之间的空格必须保留。
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_CJK_PUNC_SPACE_RE = re.compile(rf"(?<=[{_CJK}])\s+(?=[，。！？、；：）】》」』,.!?;:)])")
_PUNC_CJK_SPACE_RE = re.compile(rf"(?<=[，。！？、；：（【《「『])\s+(?=[{_CJK}])")

# 单个字母/数字之间的空格也要合并：Paraformer 会把型号 "TP1" 输出成 "T P 1"、
# 把 "QSn6.5-0.1" 输出成 "Q Sn 6.5 - 0.1"。三条中文正则都以 _CJK 为锚点，
# 纯字母数字串一条都不匹配，空格原样留下，热词表里的 27 条牌号因此**永远匹配不上**。
#
# 规则（每一条都是踩坑换来的）：
#   1. 只在**含字母**的片段上生效。"5 0 元" 这类纯数字不合并——
#      它可能是「五十元」也可能是「5、0 元」，粘成 "50" 反而制造新错误。
#   2. 片段里必须**至少有一个短 token（1~2 字符或孤立连接符）**。
#      牌号的典型形态是 "Q Sn 6.5 - 0.1"（短-长-短-长），而纯英文句子
#      （"I love Python code"）全是 3 字符以上的完整单词，天然被排除。
#   3. 若所有 token 都是常见英文单词（"Hello world" / "the end"），
#      判为句子，保留空格。
_ALNUM_TOKEN = r"[A-Za-z0-9]+(?:[.\-][A-Za-z0-9]+)*"   # TP1 / QSn6.5 / 0.1 / 3956
_SEP_TOKEN = r"[.\-]"                                   # 孤立的 . 或 -
_SEP = r"[ \t]+"                                        # 允许 token 之间有空白
_SPACED_ALNUM_RE = re.compile(
    rf"(?<![A-Za-z0-9.])(?:{_ALNUM_TOKEN}|{_SEP_TOKEN})"
    rf"(?:{_SEP}(?:{_ALNUM_TOKEN}|{_SEP_TOKEN}))+(?![A-Za-z0-9])"
)

# 常见的英文单词片段：用来判断「这段是不是句子而不是牌号」。
# 不需要完整词典，只覆盖 Paraformer 会输出的高频英文即可。
_COMMON_EN = frozenset("""
a an the and or but if then this that these those is are was were be been
i you he she it we they me my your his her its our their
to of in on at for with from by as into about over under
not no yes do does did done have has had can could will would should may might must
hello world open close love like want need make made take taken get got give given
good bad new old big small first last next more most some any all
python code java html css json file data text time page error test api http
please thank thanks sorry ok okay
end start begin stop go come look see find use using used run set put
ai it gpt ml ok up down out off one two three four five six seven eight nine ten
""".split())


def _is_wordlike_sequence(tokens: list) -> bool:
    """判断是不是英文句子（= 不是牌号）。

    判据：**每个 token 都是常见英文单词**。这样：
        "Hello world"  -> 两个都在字典里 -> 是句子，保留空格
        "the end"      -> 两个都在字典里 -> 是句子
        "Open AI"      -> Open 在，AI 在  -> 是句子
        "Q Sn 6.5"     -> "Q"/"Sn" 不在字典 -> 是牌号，合并
    """
    if not tokens:
        return False
    return all(t.lower() in _COMMON_EN for t in tokens)


def _join_spaced_alnum(match: "re.Match") -> str:
    """把 "T P 1" / "Q Sn 6.5 - 0.1" 这类逐字符分开的牌号合回原形。

    满足以下任一条件就不合并，原样返回：
    - 片段不含字母（纯数字，如 "5 0"）
    - 片段连起来像一个英文句子（"Hello world"）
    """
    text = match.group(0)
    if not re.search(r"[A-Za-z]", text):
        return text  # 纯数字不合并
    tokens = [t for t in re.split(r"[ \t]+", text) if t]
    if _is_wordlike_sequence(tokens):
        return text  # 是英文句子，保留空格
    return re.sub(r"[ \t]+", "", text)


def _clean_text(text: str) -> str:
    """清理模型输出：去掉 SenseVoice 标签，合并中文 token 与字母牌号间的空格。"""
    out = _TAG_RE.sub("", str(text or "")).strip()
    out = _CJK_SPACE_RE.sub("", out)
    out = _CJK_PUNC_SPACE_RE.sub("", out)
    out = _PUNC_CJK_SPACE_RE.sub("", out)
    out = _SPACED_ALNUM_RE.sub(_join_spaced_alnum, out)
    return out.strip()


# 识别时的 generate 参数（按模型类型区分）：
# - SenseVoice：显式指定中文，避免 language="auto" 误判成其它语种导致输出跑偏；
#   use_itn=True 开启逆文本规整（数字/日期/标点），可读性更好。
# - Paraformer：标点由外挂 ct-punc 负责，不认 language 参数，保持默认。
def local_generate_kwargs(kind: str) -> dict:
    if kind == "sensevoice":
        return {"language": "zh", "use_itn": True}
    return {}


# 只在「整段文本完全等于」这些样板句时才丢弃，避免误杀正常语音。
_BOILERPLATE = (
    "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    "字幕由Amara.org社区提供",
    "字幕由amara.org社区提供",
    "字幕由Amara org社区提供",
)

# 有意义的字符（文字/数字/字母）；只有标点或 emoji 说明是幻觉。
_MEANINGFUL_RE = re.compile(r"[0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CORE_RE = re.compile(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def active_ms(audio, frame: int = 320, floor: float = 0.008, rel: float = 0.15) -> float:
    """估算一段音频里「真正有声」的时长（毫秒）。

    VAD 误触发经常来自键盘声、碰麦、呼吸这类**瞬态噪声**：片段总长可能有
    0.8s，但真正有声只有 200ms。真实说话的有声占比通常在 50% 以上。
    这里按 20ms 一帧算 RMS，超过「绝对下限」与「整段 RMS 的 15%」取大者
    才算有声，避免把轻声说话判成静音。
    """
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return 0.0
    n = x.size // frame
    if n == 0:
        return 0.0
    frames = x[: n * frame].reshape(n, frame).astype(np.float64)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    thr = max(floor, rel * float(np.sqrt(np.mean(x.astype(np.float64) ** 2))))
    return float(np.count_nonzero(rms >= thr)) * frame / 16.0


def filter_hallucination(
    text: str, duration_ms: Optional[float] = None, voice_ms: Optional[float] = None
) -> str:
    """过滤 ASR 在静音/噪声上的「幻觉」输出，减少无中生有的文字。

    规则保持保守，只拦截明确的非语音产物：
    1. 没有任何文字/数字（只剩标点、emoji、标签）；
    2. 整段是同一个字重复 5 次以上（噪声上常见的「啊啊啊啊啊」）；
    3. 整段等于已知样板幻觉句；
    4. 极短音频（<400ms）却输出较长文本；
    5. 有效字 ≥7 且字种多样性 <0.45（「没有没有没有没有没有意见」这类复读）；
    6. 真正有声时长 <350ms 却输出 ≥3 个字（瞬态噪声只会响一下，说不出三个字）。
    """
    t = (text or "").strip()
    if not t:
        return ""
    if not _MEANINGFUL_RE.search(t):
        return ""
    if re.fullmatch(r"(.)\1{4,}", t, re.S):
        return ""
    if t in _BOILERPLATE:
        return ""
    core = _CORE_RE.sub("", t)
    if duration_ms is not None and duration_ms < 400 and len(t) > 12:
        return ""
    if len(core) >= 7 and len(set(core)) / len(core) < 0.45:
        return ""
    if voice_ms is not None and voice_ms < 350 and len(core) >= 3:
        return ""
    return t


def is_funasr_available() -> bool:
    """funasr 是否已安装"""
    try:
        import funasr  # noqa: F401
        return True
    except Exception:
        return False


class ASRService:
    """语音识别服务（本地 FunASR，在线后端待补）"""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._model = None
        self._model_name = None
        # 模型「类型」（sensevoice / paraformer_large），决定 generate 参数。
        # 由 qt_web.asr_models.configure_service 按模型目录内容设置。
        self._model_kind = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    # ===== 后端 =====

    def backend(self) -> str:
        """当前后端（local / online）"""
        return self.config.get("asr", "backend", default=BACKEND_LOCAL)

    # ===== 本地模型加载 =====

    def _load_local_model(self):
        """加载本地 FunASR 模型（懒加载，线程安全，只加载一次）"""
        with self._lock:
            if self._model is not None:
                return self._model

            if not is_funasr_available():
                self._load_error = "未安装 funasr，无法本地识别（pip install funasr）"
                return None

            model_key = self.config.get("asr", "local_model", default="paraformer_large")
            model_id = LOCAL_MODELS.get(model_key, LOCAL_MODELS["paraformer_large"])
            model_path = _resolve(model_id, LOCAL_MODEL_DIRS.get(model_key, ""))

            try:
                from funasr import AutoModel

                # 每个模型用各自的加载参数（VAD / 标点恢复），
                # 保证两个模型都能正确工作。
                load_args = dict(LOCAL_MODEL_LOAD_ARGS.get(model_key, {}))
                load_args["model"] = model_path
                # SenseVoice 的 bpe 分词器：Windows 中文路径会让 sentencepiece
                # 的 C++ 加载器报 NOT_FOUND，改用 ASCII 缓存路径。
                if os.path.isdir(model_path):
                    bpe = os.path.join(
                        model_path, "chn_jpn_yue_eng_ko_spectok.bpe.model"
                    )
                    if os.path.exists(bpe):
                        load_args.setdefault("tokenizer_conf", {})[
                            "bpemodel"
                        ] = _ascii_bpe_path(bpe)
                if "vad_model" in load_args:
                    # VAD 用于长音频分段。本地缺失时不回退联网（当前镜像不可用），
                    # 直接对短语音识别；等 fsmn-vad 下载完成后会自动启用。
                    vad_dir = SUPPORT_MODEL_DIRS["fsmn-vad"]
                    vad_dir = str(resolve_path(self.config.get('asr', 'vad_path', default=vad_dir)))
                    if _is_model_dir(vad_dir):
                        load_args["vad_model"] = vad_dir
                    else:
                        load_args.pop("vad_model", None)
                if "punc_model" in load_args:
                    # 标点模型只影响可读性：本地缺失时直接跳过，避免整个模型加载失败。
                    punc_dir = SUPPORT_MODEL_DIRS["ct-punc"]
                    punc_dir = str(resolve_path(self.config.get('asr', 'punc_path', default=punc_dir)))
                    if _is_model_dir(punc_dir):
                        load_args["punc_model"] = punc_dir
                    else:
                        load_args.pop("punc_model", None)
                load_args["disable_update"] = True
                self._model = AutoModel(**load_args)
                self._model_name = model_key
                self._load_error = None
                print(f"[语音识别] 本地模型已加载: {model_path}")
            except Exception as e:
                self._load_error = f"本地模型加载失败: {e}"
                print(f"[语音识别] {self._load_error}")
                self._model = None
            return self._model

    # ===== 识别 =====

    def recognize(self, audio: np.ndarray) -> str:
        """识别一段音频，返回文字

        Args:
            audio: float32 单声道 NumPy 数组（任意采样率，内部会重采样到 16k）

        Returns:
            str: 识别结果（失败时返回空字符串）
        """
        if audio is None or len(audio) == 0:
            return ""

        if self.backend() == BACKEND_ONLINE:
            return self._recognize_online(audio)

        return self._recognize_local(audio)

    def _recognize_local(self, audio: np.ndarray) -> str:
        """本地 FunASR 识别"""
        model = self._load_local_model()
        if model is None:
            return ""

        # 确保采样率正确（sounddevice 已按 16k 采，这里做兜底）
        kind = self._model_kind or self._model_name or self.config.get(
            "asr", "local_model", default="paraformer_large"
        )
        kwargs = local_generate_kwargs(kind)
        try:
            result = model.generate(input=audio, batch_size_s=60, **kwargs)
        except Exception as e:
            print(f"[语音识别] 识别失败: {e}")
            return ""

        try:
            # FunASR 返回 list[dict]，字段 text 是识别结果
            if result and isinstance(result, list):
                text = _clean_text(result[0].get("text", ""))
                # 过滤静音/噪声上常见的幻觉文本（样板句、纯标点、复读、瞬态噪声）
                duration_ms = len(audio) / SAMPLE_RATE * 1000.0
                return filter_hallucination(text, duration_ms, active_ms(audio))
        except Exception as e:
            print(f"[语音识别] 解析结果失败: {e}")
        return ""

    def _recognize_online(self, audio: np.ndarray) -> str:
        """在线识别（待实现，预留接口）"""
        print(
            "[语音识别] 在线后端尚未实现，请在「语音输入」页把识别后端切换为「本地离线」"
        )
        return ""

    # ===== 状态查询 =====

    def is_model_loaded(self) -> bool:
        """本地模型是否已加载"""
        return self._model is not None

    def load_error(self) -> Optional[str]:
        """最近一次加载错误信息（供 UI 提示）"""
        return self._load_error

    def model_label(self) -> str:
        """当前模型的中文可读名称"""
        name = self._model_name or self.config.get("asr", "local_model", default="paraformer_large")
        return LOCAL_MODEL_LABELS.get(name, LOCAL_MODELS.get(name, name))

    def switch_model(self, model_key: str):
        """切换本地模型：卸载当前模型，下次识别时按新模型懒加载

        Args:
            model_key: LOCAL_MODELS 中的键（sensevoice / paraformer_large）
        """
        if model_key not in LOCAL_MODELS:
            return
        with self._lock:
            # 卸载旧模型，释放内存/显存，下次识别时按新配置加载
            self._model = None
            self._model_name = None
            self._model_kind = None
            self._load_error = None
        print(f"[语音识别] 已切换本地模型为: {LOCAL_MODELS[model_key]}")
