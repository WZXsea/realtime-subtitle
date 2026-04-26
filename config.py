import os
import sys
import configparser

APP_VERSION = "3.0.0"

# CRITICAL: Prevent HuggingFace from triggering macOS AuthKit (Keychain) prompts
# This is a common cause of SIGABRT/EXC_GUARD crashes in sandboxed app bundles.
os.environ["HF_HUB_DISABLE_SYSLOG"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_AUTO_AUTH"] = "1"
# Disable parallel tokenizers to avoid fork() issues in PyInstaller
os.environ["TOKENIZERS_PARALLELISM"] = "false"
# Add KMP_DUPLICATE_LIB_OK here as well for consistency
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# --- STRATEGIC COMPATIBILITY PATCHES FOR BUNDLED APPS ---

# 1. SSL Certificate Patch
# PyInstaller-bundled Python often fails to find system root certificates on macOS.
# We force the path to use 'certifi' which we bundle with the app.
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    print(f"[Config] SSL Certificates patched via certifi: {certifi.where()}")
except ImportError:
    print("[Config] certifi not found. SSL verification might fail in bundled mode.")

# 2. HuggingFace Mirror Optimization (Pre-emptively setting hf-mirror.com)
# This significantly improves download success rate in regions with filtered access.
if not os.getenv("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    print("[Config] HuggingFace Mirror auto-enabled: https://hf-mirror.com")


# 3. Persistent Logging Patch
# Bundled GUI apps don't show a terminal. We redirect stdout/stderr to a file.
def setup_logging():
    log_dir = os.path.expanduser("~/Library/Logs/RealtimeSubtitle")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")
    
    # Simple redirect
    try:
        f = open(log_file, "a", encoding="utf-8")
        sys.stdout = f
        sys.stderr = f
        # Write timestamp to log
        import datetime
        print(f"\n--- SESSION START: {datetime.datetime.now()} ---")
    except Exception as e:
        print(f"[Config] Logging redirect failed: {e}")

# Only redirect if NOT running in interactive Terminal 
# (i.e., when bundled or run from Finder)
if not sys.stdin or not sys.stdin.isatty():
    setup_logging()


class Config:
    """Centralized configuration loaded from config.ini"""

    PROVIDER_ALIASES = {
        "硅基流动 (SiliconFlow)": "SiliconFlow (硅基流动)",
    }

    def __init__(self, config_path=None):
        self.config_path_internal = config_path
        self.config = configparser.ConfigParser()
        self.load()

    def reload(self):
        """Reload configuration from disk"""
        print("[Config] Reloading configuration from disk...")
        self.load()

    def load(self):
        # 1. Determine the path for settings
        config_path = self.config_path_internal
        if config_path is None:
            user_config_dir = os.path.expanduser(
                "~/Library/Application Support/RealtimeSubtitle"
            )
            os.makedirs(user_config_dir, exist_ok=True)
            config_path = os.path.join(user_config_dir, "config.ini")

        bundle_template_path = resource_path("config.ini")

        # 2. Try loading from the writable user path first
        if os.path.exists(config_path):
            self.config.read(config_path, encoding="utf-8")
            print(f"[Config] Loaded from USER path: {config_path}")
        # 3. Fallback to bundled template if user path doesn't exist
        elif os.path.exists(bundle_template_path):
            self.config.read(bundle_template_path, encoding="utf-8")
            print(f"[Config] Initialized from BUNDLE template: {bundle_template_path}")
            # Optional: Write back to user path immediately to ensure permanence
            with open(config_path, "w", encoding="utf-8") as f:
                self.config.write(f)
        # 4. Emergency Fallback: Generate a fresh default config if NOTHING exists
        else:
            print(
                f"[Config] 🆕 No config found. Generating NEW default at {config_path}"
            )
            self._write_default_config(config_path)
            self.config.read(config_path, encoding="utf-8")

        # API settings (env vars take precedence)
        self.active_provider = self._get("api", "active_provider", "DeepSeek").strip()
        self.active_provider = self.PROVIDER_ALIASES.get(
            self.active_provider, self.active_provider
        )

        # Consistent mapping for special vendor names
        mapped_provider = self.active_provider
        if mapped_provider == "[自定义]":
            mapped_provider = "Custom"

        vendor_section = f"api.{mapped_provider}"

        # Load from vendor-specific section first, then fallback to global [api] section
        self.api_base_url = (
            os.getenv("OPENAI_BASE_URL")
            or self._get(vendor_section, "base_url")
            or self._get("api", "base_url")
            or None
        )
        self.api_key = (
            os.getenv("OPENAI_API_KEY")
            or self._get(vendor_section, "api_key")
            or self._get("api", "api_key", "")
        )

        # Translation settings: Priority = Vendor Specific -> [translation] -> fallback "gpt-3.5-turbo"
        self.model = (
            self._get(vendor_section, "model")
            or self._get("translation", "model")
            or "gpt-3.5-turbo"
        )

        # DEBUG BANNER
        print("=" * 60)
        print(f"[Config] 🚀 SYSTEM BOOTSTRAP")
        print(f"[Config] Active Vendor: {self.active_provider}")
        print(f"[Config] Section Used:  {vendor_section}")
        print(f"[Config] Target Model:  {self.model}")
        print("=" * 60)
        self.target_lang = self._get("translation", "target_lang", "Chinese")
        self.translation_threads = self._getint("translation", "threads", 4)

        # Transcription settings
        self.asr_backend = self._get("transcription", "backend", "whisper").lower()
        self.whisper_model = self._get("transcription", "whisper_model", "base")
        self.funasr_model = self._get(
            "transcription",
            "funasr_model",
            "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        )
        self.whisper_device = self._get("transcription", "device", "cpu")
        self.whisper_compute_type = self._get("transcription", "compute_type", "int8")
        self.source_language = self._get("transcription", "source_language", "auto")
        if self.source_language == "auto":
            self.source_language = None  # Whisper uses None for auto-detect
        self.transcription_workers = self._getint(
            "transcription", "transcription_workers", 2
        )

        # Audio settings
        self.sample_rate = self._getint("audio", "sample_rate", 16000)
        self.silence_threshold = self._getfloat("audio", "silence_threshold", 0.01)
        self.silence_duration = self._getfloat("audio", "silence_duration", 1.5)
        self.chunk_duration = self._getfloat("audio", "chunk_duration", 0.5)
        self.vad_threshold = self._getfloat("audio", "vad_threshold", 0.5)
        self.keep_source = self._get("audio", "keep_source", "true").lower() == "true"

        # Device index: 'auto' or empty = auto-detect BlackHole, or set a specific index
        device_idx_str = self._get("audio", "device_index", "auto")
        if device_idx_str.isdigit():
            self.device_index = int(device_idx_str)
        elif device_idx_str.lower() in ("auto", ""):
            self.device_index = self._find_blackhole_device()
        else:
            self.device_index = None

        # Device name for UI display
        try:
            import sounddevice as sd

            if self.device_index is not None and isinstance(self.device_index, int):
                devices = sd.query_devices()
                if 0 <= self.device_index < len(devices):
                    self.device_name = devices[self.device_index]["name"]
                else:
                    self.device_name = "Default"
            else:
                # Fallback to default input device
                default_device = sd.query_devices(kind="input")
                self.device_name = default_device["name"]
        except Exception as e:
            print(f"[Config] Error getting device name: {e}")
            self.device_name = "Default"

        # Max phrase duration - force processing after N seconds
        self.max_phrase_duration = self._getfloat("audio", "max_phrase_duration", 10.0)

        # Streaming mode settings
        self.streaming_mode = (
            self._get("audio", "streaming_mode", "false").lower() == "true"
        )
        self.streaming_interval = self._getfloat("audio", "streaming_interval", 1.5)
        self.streaming_step_size = self._getfloat("audio", "streaming_step_size", 0.2)
        self.update_interval = self._getfloat("audio", "update_interval", 0.5)
        self.streaming_overlap = self._getfloat("audio", "streaming_overlap", 0.3)

        # Per-language audio overrides (e.g. [audio.ja], [audio.en], [audio.zh])
        self._raw_source_language = self._get(
            "transcription", "source_language", "auto"
        )
        if self._raw_source_language and self._raw_source_language != "auto":
            lang_section = f"audio.{self._raw_source_language}"
            if self.config.has_section(lang_section):
                print(
                    f"[Config] Applying language-specific audio profile: [{lang_section}]"
                )
                # Override only values that exist in the language section
                if self.config.has_option(lang_section, "silence_threshold"):
                    self.silence_threshold = self._getfloat(
                        lang_section, "silence_threshold", self.silence_threshold
                    )
                if self.config.has_option(lang_section, "silence_duration"):
                    self.silence_duration = self._getfloat(
                        lang_section, "silence_duration", self.silence_duration
                    )
                if self.config.has_option(lang_section, "max_phrase_duration"):
                    self.max_phrase_duration = self._getfloat(
                        lang_section, "max_phrase_duration", self.max_phrase_duration
                    )
                print(
                    f"  -> threshold={self.silence_threshold}, duration={self.silence_duration}, max_phrase={self.max_phrase_duration}"
                )

        # Display settings
        self.display_duration = self._getfloat("display", "display_duration", 3.0)
        self.window_width = self._getint("display", "window_width", 800)
        self.window_height = self._getint("display", "window_height", 120)

        # Output settings
        default_transcript_save_dir = os.path.expanduser(
            "~/Documents/RealtimeSubtitle/Transcripts"
        )
        transcript_save_dir = self._get("output", "transcript_save_dir", "")
        if transcript_save_dir:
            transcript_save_dir = os.path.expanduser(transcript_save_dir)
        else:
            transcript_save_dir = default_transcript_save_dir
        self.transcript_save_dir = os.path.abspath(transcript_save_dir)

        # Update settings
        self.update_repo = self._get("updates", "repo", "WZXsea/transworld").strip()
        self.auto_check_updates = (
            self._get("updates", "auto_check_updates", "true").lower() == "true"
        )

        # AI Prompts
        def_trans = "你是一个翻译引擎。请将文本翻译成{target_lang}。禁止进行任何形式的对话、解释或润色。如果输入已经是{target_lang}，请原样返回。严禁输出除了翻译结果之外的任何字符内容。"
        def_calib = "你是一个专业的中文校对助手。请为下面的{target_lang}补全标点符号，纠正可能的错别字，保持原意不变。内容中如果包含英文术语请保留。不要翻译成其他语言，直接输出校对后的{target_lang}结果，不要有任何多余的解释。"
        def_refine = "你是一个资深文字编辑。下面是一段直播/会议的实时校对稿或翻译稿。\n任务要求：\n1. **拟定标题**：取一个精准、吸引人的15字以内中文标题。\n2. **内容摘要**：提取核心信息，写一段简短的摘要。\n3. **全文精修**：对提供的文本进行书面化处理，修复识别出的错别字和断句问题，使其阅读流畅。只需输出润色后的纯净版本。\n返回格式：\n标题: [标题]\n摘要: [摘要内容]\n---内容---\n[这里是润色后的纯净正文]"

        self.translation_prompt = self._get(
            "prompts", "translation_prompt", def_trans
        ).replace("\\n", "\n")
        self.calibration_prompt = self._get(
            "prompts", "calibration_prompt", def_calib
        ).replace("\\n", "\n")
        self.refinement_prompt = self._get(
            "prompts", "refinement_prompt", def_refine
        ).replace("\\n", "\n")

    def _get(self, section, key, fallback=""):
        try:
            value = self.config.get(section, key)
            return value if value else fallback
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    def _getint(self, section, key, fallback=0):
        try:
            return self.config.getint(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return fallback

    def _getfloat(self, section, key, fallback=0.0):
        try:
            return self.config.getfloat(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return fallback

    def _find_blackhole_device(self):
        """Auto-detect BlackHole audio device index"""
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0 and "blackhole" in d["name"].lower():
                    print(f"[Config] Auto-detected BlackHole device: [{i}] {d['name']}")
                    return i
            print("[Config] BlackHole not found, using default input device")
            return None
        except Exception as e:
            print(f"[Config] Error detecting audio devices: {e}")
            return None

    def _get_device_name(self):
        """Get device name from device_index for UI display"""
        try:
            import sounddevice as sd

            if self.device_index is not None and isinstance(self.device_index, int):
                devices = sd.query_devices()
                if 0 <= self.device_index < len(devices):
                    return devices[self.device_index]["name"]
            # Fallback to default input device
            default_device = sd.query_devices(kind="input")
            return default_device["name"]
        except Exception as e:
            print(f"[Config] Error getting device name: {e}")
            return "Default"

    def apply_language_profile(self, lang_code):
        """Dynamically apply a language-specific audio profile at runtime.
        Returns True if settings were changed."""
        if not lang_code:
            return False
        lang_section = f"audio.{lang_code}"
        if not self.config.has_section(lang_section):
            return False

        changed = False
        for key in ("silence_threshold", "silence_duration", "max_phrase_duration"):
            if self.config.has_option(lang_section, key):
                new_val = self._getfloat(lang_section, key, getattr(self, key))
                old_val = getattr(self, key)
                if new_val != old_val:
                    setattr(self, key, new_val)
                    changed = True

        if changed:
            print(f"[Config] Auto-switched to [{lang_section}] profile:")
            print(
                f"  -> threshold={self.silence_threshold}, duration={self.silence_duration}, max_phrase={self.max_phrase_duration}"
            )
        return changed

    def save(self):
        """Save current configuration back to disk"""
        try:
            config_path = self.config_path_internal
            if config_path is None:
                user_config_dir = os.path.expanduser(
                    "~/Library/Application Support/RealtimeSubtitle"
                )
                config_path = os.path.join(user_config_dir, "config.ini")

            # [api] section — 只存 active_provider，不存各供应商的具体数据
            if not self.config.has_section("api"):
                self.config.add_section("api")

            self.config.set("api", "active_provider", str(getattr(self, "active_provider", "DeepSeek")))

            # 各供应商数据写入各自独立的 [api.Xxx] 段，彻底隔离
            mapped_p = self.PROVIDER_ALIASES.get(self.active_provider, self.active_provider)
            if mapped_p == "[自定义]":
                mapped_p = "Custom"

            vendor_section = f"api.{mapped_p}"
            if not self.config.has_section(vendor_section):
                self.config.add_section(vendor_section)

            self.config.set(vendor_section, "base_url", getattr(self, "api_base_url", "") or "")
            self.config.set(vendor_section, "api_key", getattr(self, "api_key", ""))
            self.config.set(vendor_section, "model", getattr(self, "model", ""))

            # If an older provider section name exists, fold it into the canonical name on save.
            for old_name, new_name in self.PROVIDER_ALIASES.items():
                if new_name == mapped_p and self.config.has_section(f"api.{old_name}"):
                    self.config.remove_section(f"api.{old_name}")

            # [audio] section
            if not self.config.has_section("audio"):
                self.config.add_section("audio")

            # Convert device_name to device_index for saving
            device_idx_to_save = "auto"
            try:
                import sounddevice as sd
                for i, d in enumerate(sd.query_devices()):
                    if d["name"] == getattr(self, "device_name", "") and d["max_input_channels"] > 0:
                        device_idx_to_save = str(i)
                        break
            except:
                device_idx_to_save = str(self.device_index) if self.device_index is not None else "auto"

            self.config.set("audio", "device_index", device_idx_to_save)
            self.config.set("audio", "sample_rate", str(getattr(self, "sample_rate", 16000)))
            self.config.set("audio", "silence_threshold", str(self.silence_threshold))
            self.config.set("audio", "silence_duration", str(self.silence_duration))
            self.config.set("audio", "vad_threshold", str(getattr(self, "vad_threshold", 0.5)))
            self.config.set("audio", "keep_source", "true" if getattr(self, "keep_source", True) else "false")
            self.config.set("audio", "max_phrase_duration", str(getattr(self, "max_phrase_duration", 10.0)))

            # [transcription] section
            if not self.config.has_section("transcription"):
                self.config.add_section("transcription")
            self.config.set("transcription", "backend", getattr(self, "asr_backend", "whisper"))
            self.config.set("transcription", "whisper_model", getattr(self, "whisper_model", "base"))
            self.config.set("transcription", "funasr_model", getattr(self, "funasr_model", "iic/SenseVoiceSmall"))
            self.config.set("transcription", "device", self.whisper_device)
            self.config.set("transcription", "compute_type", self.whisper_compute_type)

            # [translation] section
            if not self.config.has_section("translation"):
                self.config.add_section("translation")
            self.config.set("translation", "model", getattr(self, "model", "gpt-3.5-turbo"))
            self.config.set("translation", "target_lang", getattr(self, "target_lang", "Chinese"))

            # [output] section
            if not self.config.has_section("output"):
                self.config.add_section("output")
            self.config.set(
                "output",
                "transcript_save_dir",
                getattr(self, "transcript_save_dir", os.path.expanduser("~/Documents/RealtimeSubtitle/Transcripts")),
            )

            # [prompts] section
            if not self.config.has_section("prompts"):
                self.config.add_section("prompts")
            self.config.set("prompts", "translation_prompt", getattr(self, "translation_prompt", "").replace("\n", "\\n"))
            self.config.set("prompts", "calibration_prompt", getattr(self, "calibration_prompt", "").replace("\n", "\\n"))
            self.config.set("prompts", "refinement_prompt", getattr(self, "refinement_prompt", "").replace("\n", "\\n"))

            # [updates] section
            if not self.config.has_section("updates"):
                self.config.add_section("updates")
            self.config.set("updates", "repo", getattr(self, "update_repo", "WZXsea/transworld"))
            self.config.set(
                "updates",
                "auto_check_updates",
                "true" if getattr(self, "auto_check_updates", True) else "false",
            )

            with open(config_path, "w", encoding="utf-8") as f:
                self.config.write(f)
            print(f"[Config] Configuration saved to {config_path}")
            return True
        except Exception as e:
            print(f"[Config] Failed to save config: {e}")
            return False

    def get_vendor_settings(self, name):
        """Get settings for a specific vendor from their section"""
        mapped_name = self.PROVIDER_ALIASES.get(name, name)
        if mapped_name == "[自定义]":
            mapped_name = "Custom"
        
        section = f"api.{mapped_name}"
        if not self.config.has_section(section):
            # Compatibility: read old section names so existing user configs keep working.
            for old_name, new_name in self.PROVIDER_ALIASES.items():
                if new_name == mapped_name and self.config.has_section(f"api.{old_name}"):
                    section = f"api.{old_name}"
                    break

        if not self.config.has_section(section):
            return {}
        
        return {
            "api_key": self._get(section, "api_key", ""),
            "api_base_url": self._get(section, "base_url", ""),
            "model": self._get(section, "model", "")
        }

    def remove_provider(self, name):
        """Remove a dynamic section and switch to default"""
        section = f"api.{name}"
        if self.config.has_section(section):
            self.config.remove_section(section)
            # Switch to DeepSeek as safety fallback if deleting active
            if self.active_provider == name:
                self.active_provider = "DeepSeek"
            self.save()
            return True
        return False

    def get_all_providers(self):
        """Returns ordered list: Hardcoded -> Dynamic -> '[自定义]' at bottom"""
        fixed = [
            "OpenAI (官方)", 
            "DeepSeek", 
            "Kimi (月之暗面)", 
            "Grok (xAI)", 
            "智谱 AI (BigModel)", 
            "SiliconFlow (硅基流动)", 
            "通义千问 (DashScope)"
        ]
        custom = []
        
        # Names to exclude from custom list as they are now built-in or legacy
        legacy_names = ["Custom", "[自定义]", "Moonshot", "OpenAI", "OpenAI 官方", "OpenAI官方", "硅基流动 (SiliconFlow)"]
        
        for section in self.config.sections():
            if section.startswith("api."):
                p_name = section[4:]
                # Avoid duplicates already in the fixed list or legacy items
                if p_name not in fixed and p_name not in legacy_names:
                    custom.append(p_name)
        
        # Consistently return in logical order
        return fixed + sorted(custom) + ["[自定义]"]

    def print_config(self):
        """Print current configuration for debugging"""
        print("[Config] Current settings:")
        print(f"  API Base URL: {self.api_base_url or '(default OpenAI)'}")
        print(
            f"  API Key: {self.api_key[:8]}...{self.api_key[-4:] if len(self.api_key) > 12 else '***'}"
        )
        print(f"  Model: {self.model}")
        print(f"  Target Language: {self.target_lang}")
        print(f"  ASR Backend: {self.asr_backend}")
        print(f"  Whisper Model: {self.whisper_model}")
        print(f"  FunASR Model: {self.funasr_model}")
        print(f"  Sample Rate: {self.sample_rate}")
        print(f"  Source Language: {self._raw_source_language}")

    def _write_default_config(self, save_path):
        """Creates a fully populated default config.ini from scratch."""
        default_config = configparser.ConfigParser()

        # [api] Section — 只存 active_provider，不存具体供应商数据
        default_config["api"] = {
            "active_provider": "DeepSeek",
        }

        # [api.DeepSeek]
        default_config["api.DeepSeek"] = {
            "base_url": "https://api.deepseek.com",
            "api_key": "",
            "model": "deepseek-v4-flash",
        }

        default_config["api.Kimi (月之暗面)"] = {
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "",
            "model": "moonshot-v1-32k",
        }

        default_config["api.OpenAI (官方)"] = {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        }

        default_config["api.SiliconFlow (硅基流动)"] = {
            "base_url": "https://api.siliconflow.cn/v1",
            "api_key": "",
            "model": "deepseek-ai/DeepSeek-V3",
        }

        default_config["api.智谱 AI (BigModel)"] = {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": "",
            "model": "glm-5",
        }

        default_config["api.通义千问 (DashScope)"] = {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "",
            "model": "qwen-plus",
        }

        default_config["api.Grok (xAI)"] = {
            "base_url": "https://api.x.ai/v1",
            "api_key": "",
            "model": "grok-4.20-reasoning",
        }

        # [api.Custom]
        default_config["api.Custom"] = {
            "base_url": "",
            "api_key": "",
            "model": "",
        }

        # [translation]
        default_config["translation"] = {
            "model": "deepseek-v4-flash",
            "target_lang": "Chinese",
            "threads": "4",
        }

        # [transcription]
        default_config["transcription"] = {
            "backend": "whisper",
            "whisper_model": "base",
            "device": "cpu",
            "compute_type": "int8",
            "source_language": "auto",
            "transcription_workers": "2",
        }

        # [audio]
        default_config["audio"] = {
            "sample_rate": "16000",
            "silence_threshold": "0.01",
            "silence_duration": "1.5",
            "chunk_duration": "0.5",
            "device_index": "auto",
            "max_phrase_duration": "10.0",
            "streaming_mode": "false",
        }

        # [updates]
        default_config["updates"] = {
            "repo": "WZXsea/transworld",
            "auto_check_updates": "true",
        }

        # [display]
        default_config["display"] = {
            "display_duration": "3.0",
            "window_width": "800",
            "window_height": "120",
        }

        # [output]
        default_config["output"] = {
            "transcript_save_dir": "~/Documents/RealtimeSubtitle/Transcripts",
        }

        # [prompts]
        default_config["prompts"] = {
            "translation_prompt": "你是一个翻译引擎。请将文本翻译成{target_lang}。禁止进行任何形式的对话、解释或润色。如果输入已经是{target_lang}，请原样返回。",
            "calibration_prompt": "你是一个专业的中文校对助手。请为下面的{target_lang}补全标点符号，纠正可能的错别字，保持原意不变。",
            "refinement_prompt": "你是一个资深文字编辑。请对提供的文本进行书面化处理，修复识别出的错别字和断句问题。只需输出润色后的纯净版本。",
        }

        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as f:
                default_config.write(f)
            print(f"[Config] Successfully generated default config at {save_path}")
        except Exception as e:
            print(f"[Config] Failed to create default config: {e}")


# Global config instance
config = Config()
