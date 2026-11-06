"""
自定义在线翻译模型（支持自定义请求模板 + 内置三大国内可用服务商预设）

支持两类在线翻译：
1. 自定义（OpenAI 兼容 / 任意模板）：
   - 通过可编辑的「请求体模板」适配 OpenAI、DeepSeek、通义、火山方舟、
     腾讯混元等 OpenAI 兼容接口。
2. 内置预设（百度 / 微软 / 谷歌）：
   - 百度翻译：GET + MD5(appid + q + salt + secret) 签名。
   - 微软 Azure：POST + 双请求头（Ocp-Apim-Subscription-Key / -Region）。
   - 谷歌 v2：POST + ?key= 参数，data.translations[].translatedText。

配置字段（translation.online_providers[] 每项）：
  name, preset, base_url, api_key, model, request_body, response_path,
  auth_header, appid, secret, region

只用标准库 urllib / hashlib 发请求，不引入第三方依赖。
"""
from core.translation_service import TranslationModel
from core.language_utils import language_name
import json
import hashlib
import random
import urllib.request
import urllib.error
import urllib.parse
import logging

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT = 20

# 语言代码 -> 语言中文名（用于构建翻译提示词）
# 默认 OpenAI 兼容请求体模板
DEFAULT_REQUEST_BODY = (
    '{"model": "{model}", "messages": [{"role": "user", "content": "{prompt}"}]}'
)

# 默认响应提取路径（OpenAI 兼容）
DEFAULT_RESPONSE_PATH = "choices.0.message.content"

# 内置预设的默认配置（base_url / response_path / auth 说明）
PRESETS = {
    "baidu": {
        "name": "百度翻译",
        "base_url": "https://fanyi-api.baidu.com/api/trans/vip/translate",
        "response_path": "trans_result.0.dst",
    },
    "microsoft": {
        "name": "微软翻译 (Azure)",
        "base_url": "https://api.cognitive.microsofttranslator.com/translate",
        "response_path": "0.translations.0.text",
    },
    "google": {
        "name": "谷歌翻译",
        "base_url": "https://translation.googleapis.com/language/translate/v2",
        "response_path": "data.translations.0.translatedText",
    },
}


class CustomOpenAITranslationModel(TranslationModel):
    """自定义在线翻译模型（OpenAI 兼容 / 内置预设）"""

    def __init__(self, base_url: str = None, api_key: str = None, model: str = None,
                 request_body: str = None, response_path: str = None,
                 auth_header: str = None, preset: str = None,
                 appid: str = None, secret: str = None, region: str = None):
        """
        参数均可为 None，为 None 时从配置读取（便于懒加载时按最新配置构建）。

        preset: 内置预设标识（baidu / microsoft / google），None 或 "" 表示自定义模板。
        appid/secret: 百度翻译的 APP ID 与密钥。
        region: 微软翻译的区域（如 eastasia）。
        """
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model
        self.request_body = request_body
        self.response_path = response_path
        self.auth_header = auth_header
        self.preset = preset
        self.appid = appid
        self.secret = secret
        self.region = region
        self.loaded = False

    def _ensure_config(self):
        """从配置读取缺失字段

        优先从 translation.online_providers 里按 active_provider 名查找当前
        服务商配置；找不到时回退到旧的 translation.custom_openai 单段配置。
        """
        from core.config_manager import get_config
        cfg = get_config()

        active_name = cfg.get("translation", "active_provider", default="")
        providers = cfg.get("translation", "online_providers", default=None) or []
        provider = next((p for p in providers if p.get("name") == active_name), None)
        if provider is None and providers:
            provider = providers[0]

        def pick(attr, key, default):
            if getattr(self, attr) is not None:
                return
            value = default
            if provider is not None:
                value = provider.get(key, default)
            else:
                value = cfg.get("translation", "custom_openai", key, default=default)
            setattr(self, attr, value)

        pick("preset", "preset", "")
        pick("base_url", "base_url", "")
        pick("api_key", "api_key", "")
        pick("model_name", "model", "")
        pick("appid", "appid", "")
        pick("secret", "secret", "")
        pick("region", "region", "")
        pick("request_body", "request_body", DEFAULT_REQUEST_BODY)
        pick("response_path", "response_path", DEFAULT_RESPONSE_PATH)
        pick("auth_header", "auth_header", "Bearer {api_key}")

        # 内置预设：未显式填 base_url / response_path 时用预设默认值
        if self.preset and self.preset in PRESETS:
            p = PRESETS[self.preset]
            if not self.base_url:
                self.base_url = p["base_url"]
            if (not self.response_path) or self.response_path == DEFAULT_RESPONSE_PATH:
                self.response_path = p["response_path"]

    def load(self):
        """加载（在线模型无本地资源，仅校验配置）"""
        self._ensure_config()
        if self.preset == "baidu":
            if not self.appid or not self.secret:
                raise ValueError("百度翻译需要填写 APP ID 和密钥")
        elif self.preset == "microsoft":
            if not self.api_key:
                raise ValueError("微软翻译需要填写订阅密钥 (Key)")
        elif self.preset == "google":
            if not self.api_key:
                raise ValueError("谷歌翻译需要填写 API Key")
        else:
            if not self.base_url:
                raise ValueError("未填写接口地址（base_url）")
            if not self.model_name:
                raise ValueError("未填写模型名（model）")
        self.loaded = True
        logger.info("[在线模型] 已就绪: %s @ %s", self.preset or self.model_name, self.base_url)

    def unload(self):
        self.loaded = False

    def _build_prompt(self, text: str, source_lang: str, target_lang: str) -> str:
        src = language_name(source_lang)
        tgt = language_name(target_lang)
        if source_lang == "auto":
            return f"将下面的文本翻译成{tgt}：\n{text}"
        return f"将下面的{src}翻译成{tgt}：\n{text}"

    def _render_body(self, text: str, source_lang: str, target_lang: str, prompt: str) -> str:
        """渲染请求体模板，替换占位符

        注意：{text} / {prompt} 可能包含换行、引号等字符，直接 str.replace
        会破坏 JSON。这里把占位符值先 JSON 转义、再去掉首尾引号（得到
        「JSON 字符串值内部内容」），替换进模板后整体仍是合法 JSON。
        """
        def inner(value: str) -> str:
            return json.dumps(value, ensure_ascii=False)[1:-1]

        result = self.request_body
        result = result.replace("{text}", inner(text))
        result = result.replace("{prompt}", inner(prompt))
        result = result.replace("{source_lang}", inner(source_lang))
        result = result.replace("{target_lang}", inner(target_lang))
        result = result.replace("{api_key}", inner(self.api_key or ""))
        result = result.replace("{model}", inner(self.model_name or ""))
        return result

    def _extract(self, body: dict) -> str:
        """按 response_path 提取翻译文本"""
        path = (self.response_path or DEFAULT_RESPONSE_PATH).strip()
        current = body
        for part in path.split("."):
            if current is None:
                return ""
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return ""
            else:
                return ""
        if isinstance(current, str):
            return current.strip()
        if current is None:
            return ""
        # 兜底：非字符串结果（如 dict）取 content
        if isinstance(current, dict):
            return str(current.get("content") or current.get("text") or "").strip()
        return str(current).strip()

    def _request(self, prompt: str, text: str, source_lang: str, target_lang: str) -> str:
        """按预设分发到对应请求实现"""
        if self.preset == "baidu":
            return self._request_baidu(text, source_lang, target_lang)
        if self.preset == "microsoft":
            return self._request_microsoft(text, source_lang, target_lang)
        if self.preset == "google":
            return self._request_google(text, source_lang, target_lang)
        return self._request_generic(prompt, text, source_lang, target_lang)

    # ---- 百度翻译：GET + MD5 签名 ----
    def _request_baidu(self, text: str, source_lang: str, target_lang: str) -> str:
        salt = str(random.randint(32768, 65536))
        sign = hashlib.md5((self.appid + text + salt + self.secret).encode("utf-8")).hexdigest()
        params = urllib.parse.urlencode({
            "q": text,
            "from": "auto" if source_lang == "auto" else source_lang,
            "to": target_lang,
            "appid": self.appid,
            "salt": salt,
            "sign": sign,
        })
        url = self.base_url.strip()
        # 百度接口本身无 query 或允许追加，这里用 ? 或 & 连接
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(url + sep + params, method="GET")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        if body.get("error_code"):
            return f"[百度翻译错误 {body.get('error_code')}] {text}"
        return self._extract(body)

    # ---- 微软 Azure：POST + 双请求头 + 数组请求体 ----
    def _request_microsoft(self, text: str, source_lang: str, target_lang: str) -> str:
        from_ = "" if source_lang == "auto" else source_lang
        params = urllib.parse.urlencode({
            "api-version": "3.0",
            "from": from_,
            "to": target_lang,
        })
        url = self.base_url.strip()
        sep = "&" if "?" in url else "?"
        data = json.dumps([{"Text": text}], ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": self.api_key or "",
            "Ocp-Apim-Subscription-Region": self.region or "",
        }
        req = urllib.request.Request(url + sep + params, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return self._extract(body)

    # ---- 谷歌 v2：POST + ?key= 参数 ----
    def _request_google(self, text: str, source_lang: str, target_lang: str) -> str:
        params = urllib.parse.urlencode({"key": self.api_key or ""})
        url = self.base_url.strip()
        sep = "&" if "?" in url else "?"
        payload = {
            "q": text,
            "source": "" if source_lang == "auto" else source_lang,
            "target": target_lang,
            "format": "text",
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url + sep + params, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return self._extract(body)

    # ---- 通用模板（OpenAI 兼容 / 自定义）----
    def _request_generic(self, prompt: str, text: str, source_lang: str, target_lang: str) -> str:
        url = self.base_url.strip()
        body_str = self._render_body(text, source_lang, target_lang, prompt)

        # 解析请求体 JSON（模板可能是 JSON 字符串；若解析失败则直接当纯文本发送）
        try:
            payload = json.loads(body_str)
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        except Exception:
            payload = body_str
            data = body_str.encode("utf-8")
            content_type = "text/plain"

        headers = {"Content-Type": content_type}

        # 鉴权头
        auth = (self.auth_header or "").strip()
        if auth and auth.lower() not in ("无", "none", "无鉴权"):
            auth_value = auth.replace("{api_key}", self.api_key or "")
            headers["Authorization"] = auth_value

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # 尝试按 JSON 解析并走 response_path 提取
        try:
            body = json.loads(raw)
            return self._extract(body)
        except Exception:
            # 非 JSON 响应，原样返回（去首尾空白）
            return raw.strip()

    def translate(self, text: str, source_lang: str = "auto", target_lang: str = "zh") -> str:
        if not self.loaded:
            try:
                self.load()
            except Exception as e:
                return f"[在线模型未配置] {e}"

        if not text or not text.strip():
            return ""

        prompt = self._build_prompt(text, source_lang, target_lang)
        try:
            return self._request(prompt, text, source_lang, target_lang)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            logger.warning("[在线模型] HTTP %s: %s", e.code, detail)
            return f"[在线模型请求失败 {e.code}] {text}"
        except Exception as e:
            logger.warning("[在线模型] 翻译失败: %s", e)
            return text

    def supports_batch(self) -> bool:
        return False

    def hint(self) -> str:
        return "自定义在线模型（可自行导入任意接口，支持自定义请求模板）"

    def name(self) -> str:
        return "自定义在线模型"
