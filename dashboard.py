import os
import sys
import time
import signal
import subprocess
import webbrowser
from typing import Optional

# CRITICAL: Prevent HuggingFace from triggering macOS AuthKit (Keychain) prompts
os.environ["HF_HUB_DISABLE_SYSLOG"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_AUTO_AUTH"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QComboBox,
    QLineEdit,
    QProgressBar,
    QStackedWidget,
    QButtonGroup,
    QCheckBox,
    QSpinBox,
    QDoubleSpinBox,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QFormLayout,
    QApplication,
    QMessageBox,
    QTextEdit,
    QDialog,
    QGroupBox,
    QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QThread, QSignalBlocker
from PyQt6.QtGui import QIcon, QFont, QColor
import sounddevice as sd
from config import config as app_cfg, APP_VERSION
from model_manager import ModelManager
from overlay_window import OverlayWindow
from main import SaveResultNotifier
from update_manager import (
    GitHubReleaseChecker,
    download_release_asset,
    open_release_page,
)


def map_legacy_lang(lang):
    mapping = {
        "Simplified Chinese": "简体中文",
        "Traditional Chinese": "繁体中文",
        "English": "英文",
        "Japanese": "日文",
        "French": "法文",
        "Spanish": "西班牙文",
        "German": "德文",
        "Korean": "韩文",
    }
    return mapping.get(lang, lang)


# Modern Light Theme - Catppuccin Latte
STYLESHEET = """
QMainWindow, QWidget#MainContent {
    background-color: #eff1f5;
    color: #4c4f69;
    font-family: 'Inter', 'SF Pro Display', 'Helvetica Neue', Arial, sans-serif;
}

QWidget#Sidebar {
    background-color: #e6e9ef;
    min-width: 180px;
    max-width: 220px;
}

QPushButton#NavButton {
    background-color: transparent;
    color: #4c4f69;
    border: none;
    padding: 12px 20px;
    border-radius: 10px;
    font-size: 14px;
    text-align: left;
    margin: 4px 8px;
    font-weight: 500;
}
QPushButton#NavButton:hover {
    background-color: #ccd0da;
}
QPushButton#NavButton[active="true"] {
    background-color: #1e66f5;
    color: #ffffff;
    font-weight: bold;
}

QLabel {
    font-size: 13px;
    color: #4c4f69;
}

QPushButton {
    background-color: #ffffff;
    color: #4c4f69;
    border: 1px solid #dce0e8;
    padding: 8px 16px;
    border-radius: 10px;
    font-weight: 600;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #f6f8fa;
    border-color: #bcc0cc;
}

QPushButton[primary="true"], QPushButton#StartButton {
    background-color: #1e66f5;
    color: #ffffff;
    border: 1px solid #1e66f5;
}

QPushButton#StopButton {
    background-color: #d20f39;
    color: #ffffff;
    border: none;
}

QPushButton#PauseButton {
    background-color: #fe640b;
    color: #ffffff;
    border: none;
}

QGroupBox {
    border: 1px solid #dce0e8;
    border-radius: 12px;
    margin-top: 15px;
    padding-top: 20px;
    background-color: #ffffff;
}

QProgressBar {
    border: none;
    background-color: #e6e9ef;
    border-radius: 8px;
    text-align: center;
    font-size: 10px;
    font-weight: bold;
    color: #4c4f69;
}
QProgressBar::chunk {
    background-color: #1e66f5;
    border-radius: 8px;
}
"""


class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.pipeline = None
        self.overlay_window = None
        self.save_result_notifier = None
        self._startup_fake_target = 0
        self._startup_loading_tick = 0
        self._startup_stage = 0
        self._startup_status_cycle = 0
        self._startup_status_cycle_length = 4
        self._startup_stage_texts = [
            "正在加载模型...",
            "正在初始化音频...",
            "正在唤醒引擎...",
            "正在准备界面...",
        ]
        self._startup_extra_phrases = [
            "原神nb",
            "看番不如玩原神",
            "正在努力加载中",
            "马上就好",
        ]
        self.update_worker = None
        self.update_checker = GitHubReleaseChecker()
        self.latest_update_result = None
        self._startup_fake_timer = QTimer(self)
        self._startup_fake_timer.setInterval(120)
        self._startup_fake_timer.timeout.connect(self._advance_startup_progress)
        
        # Vendor Presets: URL and recommended models
        self.VENDOR_TEMPLATES = {
            "OpenAI (官方)": {
                "base_url": "https://api.openai.com/v1",
                "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]
            },
            "DeepSeek": {
                "base_url": "https://api.deepseek.com",
                "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"]
            },
            "Kimi (月之暗面)": {
                "base_url": "https://api.moonshot.cn/v1",
                "models": ["kimi-k2.5", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]
            },
            "Grok (xAI)": {
                "base_url": "https://api.x.ai/v1",
                "models": ["grok-4.20-reasoning", "grok-4"]
            },
            "智谱 AI (BigModel)": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "models": ["glm-4", "glm-4-flash"]
            },
            "SiliconFlow (硅基流动)": {
                "base_url": "https://api.siliconflow.cn/v1",
                "models": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"]
            },
            "通义千问 (DashScope)": {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "models": ["qwen-max", "qwen-plus", "qwen-turbo"]
            }
        }
        self.PROTECTED_PROVIDERS = set(self.VENDOR_TEMPLATES.keys()) | {"[自定义]"}

        self.setWindowTitle(f"译世界 v{APP_VERSION} - 控制中心")
        self.setMinimumSize(850, 650)
        self.setStyleSheet(STYLESHEET)

        # Root Layout
        self.root_layout = QHBoxLayout(self)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        # --- Sidebar ---
        self.sidebar = QWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(10, 20, 10, 20)
        self.sidebar_layout.setSpacing(5)

        logo = QLabel("译世界")
        logo.setStyleSheet(
            "font-size: 18px; font-weight: bold; padding: 10px; color: #1e66f5; margin-bottom: 20px;"
        )
        self.sidebar_layout.addWidget(logo)

        self.nav_buttons = []

        # --- Main Content Area ---
        self.main_content = QWidget()
        self.main_content.setObjectName("MainContent")
        self.main_layout = QVBoxLayout(self.main_content)
        self.main_layout.setContentsMargins(30, 30, 30, 30)
        self.main_layout.setSpacing(20)

        self.root_layout.addWidget(self.sidebar)
        self.root_layout.addWidget(self.main_content)

        # Header Area
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 10)

        title_vbox = QVBoxLayout()
        self.header_title = QLabel("首页")
        self.header_title.setStyleSheet(
            "font-size: 26px; font-weight: bold; color: #1e66f5;"
        )
        header_subtitle = QLabel("AI 赋能的跨平台实时语音转文字及翻译系统")
        header_subtitle.setStyleSheet("font-size: 13px; color: #5c5f77;")
        title_vbox.addWidget(self.header_title)
        title_vbox.addWidget(header_subtitle)

        header_layout.addLayout(title_vbox)
        header_layout.addStretch()

        self.main_layout.addWidget(header_container)

        # Main Stacked Area
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack, stretch=1)

        # Initialize Tabs
        self.add_nav_item("首页", self.init_home_tab())
        self.add_nav_item("音频设置", self.init_audio_tab())
        self.add_nav_item("语音识别", self.init_transcription_tab())
        self.add_nav_item("翻译设置", self.init_translation_tab())
        self.add_nav_item("保存位置", self.init_output_tab())
        self.add_nav_item("更新", self.init_update_tab())
        self.add_nav_item("设备管理", self.init_device_manager_tab())
        self.add_nav_item("模型管理", self.init_model_management_tab())
        self.add_nav_item("提示词", self.init_prompt_tab())

        self.sidebar_layout.addStretch()

        # Environment Banner
        self.env_banner = QFrame()
        self.env_banner.setFixedHeight(40)
        self.env_banner.setStyleSheet(
            "background-color: #fe640b; border-radius: 8px; margin: 5px;"
        )
        env_layout = QHBoxLayout(self.env_banner)
        self.env_label = QLabel("未检测到 BlackHole 虚拟声卡")
        self.env_label.setStyleSheet("color: white; font-weight: bold;")
        self.env_help_btn = QPushButton("解决方案")
        self.env_help_btn.setFixedWidth(80)
        self.env_help_btn.setStyleSheet(
            "background-color: white; color: #fe640b; font-size: 10px;"
        )
        self.env_help_btn.clicked.connect(self.open_driver_help)
        env_layout.addWidget(self.env_label)
        env_layout.addWidget(self.env_help_btn)
        self.sidebar_layout.addWidget(self.env_banner)
        self.env_banner.hide()

        # Footer
        footer = QHBoxLayout()
        self.restart_btn = QPushButton("重启程序")
        self.restart_btn.clicked.connect(self.restart_program)

        self.quit_btn = QPushButton("退出程序")
        self.quit_btn.clicked.connect(self.close)

        self.save_btn = QPushButton("保存设置")
        self.save_btn.setProperty("primary", True)
        self.save_btn.clicked.connect(self.save_config)

        footer.addWidget(self.restart_btn)
        footer.addWidget(self.quit_btn)
        footer.addStretch()
        footer.addWidget(self.save_btn)
        self.main_layout.addLayout(footer, stretch=0)

        self.check_environment()
        if app_cfg.auto_check_updates:
            QTimer.singleShot(1800, self._auto_check_updates)

    def add_nav_item(self, text, widget):
        idx = self.stack.addWidget(widget)
        btn = QPushButton(text)
        btn.setObjectName("NavButton")
        btn.setCheckable(True)
        btn.clicked.connect(lambda: self.switch_page(idx, btn))
        self.sidebar_layout.addWidget(btn)
        self.nav_buttons.append(btn)
        if idx == 0:
            self.switch_page(0, btn)

    def switch_page(self, idx, clicked_btn):
        self.stack.setCurrentIndex(idx)
        self.header_title.setText(clicked_btn.text())
        for btn in self.nav_buttons:
            btn.setProperty("active", btn == clicked_btn)
            btn.setStyle(btn.style())

    def init_home_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)

        self.status_label = QLabel("准备就绪")
        self.status_label.setStyleSheet(
            "font-size: 24px; font-weight: bold; color: #40a02b;"
        )
        layout.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.update_banner = QFrame()
        self.update_banner.setObjectName("UpdateBanner")
        self.update_banner.setStyleSheet(
            """
            QFrame#UpdateBanner {
                background-color: #ffffff;
                border: 1px solid #dce0e8;
                border-radius: 14px;
            }
            """
        )
        banner_layout = QVBoxLayout(self.update_banner)
        banner_layout.setContentsMargins(18, 14, 18, 14)
        banner_layout.setSpacing(8)

        self.update_banner_label = QLabel("更新检查：尚未检查")
        self.update_banner_label.setWordWrap(True)
        self.update_banner_label.setStyleSheet(
            "font-size: 15px; font-weight: 600; color: #4c4f69;"
        )
        banner_layout.addWidget(self.update_banner_label)

        banner_btn_row = QHBoxLayout()
        self.update_check_btn = QPushButton("检查更新")
        self.update_check_btn.clicked.connect(lambda: self.check_for_updates(manual=True))
        self.update_open_btn = QPushButton("打开 Release")
        self.update_open_btn.setEnabled(False)
        self.update_open_btn.clicked.connect(self.open_latest_release)
        banner_btn_row.addWidget(self.update_check_btn)
        banner_btn_row.addWidget(self.update_open_btn)
        banner_btn_row.addStretch()
        banner_layout.addLayout(banner_btn_row)

        layout.addWidget(self.update_banner)

        self.progress_container = QWidget()
        prog_layout = QVBoxLayout(self.progress_container)
        self.progress_status = QLabel("正在初始化...")
        self.progress_bar = QProgressBar()
        prog_layout.addWidget(
            self.progress_status, alignment=Qt.AlignmentFlag.AlignCenter
        )
        prog_layout.addWidget(self.progress_bar)
        self.progress_container.hide()
        layout.addWidget(self.progress_container)

        btns = QHBoxLayout()
        self.start_btn = QPushButton("启动翻译")
        self.start_btn.setFixedSize(160, 55)
        self.start_btn.setObjectName("StartButton")
        self.start_btn.clicked.connect(self.on_start)

        self.stop_btn = QPushButton("停止翻译")
        self.stop_btn.setFixedSize(160, 55)
        self.stop_btn.setObjectName("StopButton")
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.hide()

        self.pause_btn = QPushButton("暂停翻译")
        self.pause_btn.setFixedSize(160, 55)
        self.pause_btn.setObjectName("PauseButton")
        self.pause_btn.clicked.connect(self.on_pause_toggle)
        self.pause_btn.hide()

        btns.addStretch()
        btns.addWidget(self.start_btn)
        btns.addWidget(self.pause_btn)
        btns.addWidget(self.stop_btn)
        btns.addStretch()
        layout.addLayout(btns)

        return tab

    # --- Core Logic ---
    def on_start(self):
        print("[Dashboard] Start triggered.")
        # 1. Sync config
        self.apply_ui_to_config()

        # 2. Language selection
        dialog = LanguageSelectDialog(self)
        if dialog.exec():
            selected_lang = dialog.get_language()
            app_cfg.source_language = selected_lang if selected_lang != "auto" else None

            # 3. Async Startup
            self.progress_container.show()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self._startup_loading_tick = 0
            self._startup_stage = 0
            self._startup_status_cycle = 0
            self._update_startup_status(force=True)
            self._startup_fake_target = 12
            self._startup_fake_timer.start()
            self.start_btn.setEnabled(False)
            self.start_btn.hide()
            self.start_btn.setText("启动翻译")

            self.startup_worker = StartupWorker()
            self.startup_worker.progress.connect(self.on_startup_progress)
            self.startup_worker.finished.connect(self.on_startup_finished)
            self.startup_worker.start()

    def on_startup_progress(self, val, msg):
        if val >= 0:
            if val < 25:
                self._startup_stage = 0
            elif val < 50:
                self._startup_stage = 1
            elif val < 75:
                self._startup_stage = 2
            else:
                self._startup_stage = 3

            if val >= 100:
                self._startup_fake_target = 95
            else:
                self._startup_fake_target = max(self._startup_fake_target, max(8, min(95, val)))
        self._update_startup_status()

    def _advance_startup_progress(self):
        if not self.progress_container.isVisible():
            self._startup_fake_timer.stop()
            return

        self._startup_loading_tick += 1
        current = self.progress_bar.value()
        target = self._startup_fake_target or 0
        if current < target:
            step = 1 if current < 60 else 2
            self.progress_bar.setValue(min(target, current + step))
        elif current > target:
            self.progress_bar.setValue(target)

        if self._startup_loading_tick % self._startup_status_cycle_length == 0:
            self._startup_status_cycle = (self._startup_status_cycle + 1) % len(self._startup_extra_phrases)
            self._update_startup_status()

    def _update_startup_status(self, force=False):
        stage = self._startup_stage_texts[min(self._startup_stage, len(self._startup_stage_texts) - 1)]
        phrase = self._startup_extra_phrases[self._startup_status_cycle]
        text = f"{stage} {phrase}"
        if force or self.progress_status.text() != text:
            self.progress_status.setText(text)

    def on_startup_finished(self, pipeline):
        self._startup_fake_timer.stop()
        self.progress_bar.setValue(100)
        self.progress_container.hide()
        if pipeline:
            self.pipeline = pipeline
            self.pipeline.start()
            
            # Create and show overlay window
            if not self.overlay_window:
                # Pass current model name for display
                current_model = app_cfg.model
                self.overlay_window = OverlayWindow(model_name=current_model)
            
            # Connect pipeline signals to overlay window
            self.pipeline.signals.update_text.connect(self.overlay_window.update_text)
            self.pipeline.signals.stats_updated.connect(self.overlay_window.update_token_stats)
            self.overlay_window.save_requested.connect(
                self.pipeline._on_manual_save_requested,
                Qt.ConnectionType.DirectConnection,
            )
            self.save_result_notifier = SaveResultNotifier(self.overlay_window)
            self.pipeline.save_result.connect(self.save_result_notifier.show_result)
            self.pipeline.save_status.connect(self.save_result_notifier.show_progress)
            self.pipeline.save_status.connect(self.overlay_window.set_save_status)
            self.pipeline.save_result.connect(self.overlay_window.finish_save_status)
            
            # Allow stopping from the overlay window too
            self.overlay_window.stop_requested.connect(self.on_stop)
            self.overlay_window.pause_toggled.connect(self.on_pause_toggle)
            
            self.overlay_window.show()
            
            # Auto-minimize dashboard to clear the screen
            self.showMinimized()
            
            self.status_label.setText("正在运行")
            self.status_label.setStyleSheet("color: #40a02b; font-weight: bold;")
            self.start_btn.hide()
            self.pause_btn.show()
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText("暂停翻译")
            self.stop_btn.show()
            self.stop_btn.setEnabled(True)
        else:
            QMessageBox.critical(self, "错误", "启动失败")
            self.progress_container.hide()
            self.start_btn.setEnabled(True)
            self.start_btn.show()
            self.start_btn.setText("启动翻译")
            self._startup_fake_target = 0

    def on_pause_toggle(self):
        if not self.pipeline:
            return

        if self.pipeline.is_paused():
            self.pipeline.resume()
            paused = False
            self.status_label.setText("正在运行")
            self.status_label.setStyleSheet("color: #40a02b; font-weight: bold;")
            self.pause_btn.setText("暂停翻译")
        else:
            self.pipeline.pause()
            paused = True
            self.status_label.setText("已暂停")
            self.status_label.setStyleSheet("color: #fe640b; font-weight: bold;")
            self.pause_btn.setText("继续翻译")

        if self.overlay_window:
            self.overlay_window.set_paused(paused)

    def on_stop(self):
        print("[Dashboard] Stop triggered.")
        if self.pipeline:
            self.pipeline.stop()
            self.pipeline = None
            
        if self.overlay_window:
            self.overlay_window.close()
            self.overlay_window = None
        
        # Restore dashboard window when translation stops
        self.showNormal()
        self.raise_()
        self.activateWindow()
            
        self.status_label.setText("准备就绪")
        self.status_label.setStyleSheet("color: #40a02b; font-weight: bold;")
        self.pause_btn.hide()
        self.pause_btn.setText("暂停翻译")
        self.stop_btn.hide()
        self.start_btn.show()
        self.start_btn.setEnabled(True)

    def _auto_check_updates(self):
        if app_cfg.auto_check_updates:
            self.check_for_updates(manual=False)

    def check_for_updates(self, manual=False):
        if self.update_worker and self.update_worker.isRunning():
            return

        repo = (self.update_repo_input.text().strip() if hasattr(self, "update_repo_input") else app_cfg.update_repo).strip()
        if hasattr(self, "update_banner_label"):
            self.update_banner_label.setText("正在检查更新..." if manual else "自动检查更新中...")
        if hasattr(self, "update_result_label"):
            self.update_result_label.setText("当前版本：v" + APP_VERSION + "\n正在检查 GitHub Releases ...")
        if hasattr(self, "update_check_btn"):
            self.update_check_btn.setEnabled(False)
        if hasattr(self, "check_update_btn"):
            self.check_update_btn.setEnabled(False)

        self.update_worker = UpdateCheckWorker(repo, APP_VERSION, self)
        self.update_worker.finished.connect(self.on_update_check_finished)
        self.update_worker.start()

    def on_update_check_finished(self, result):
        if hasattr(self, "update_check_btn"):
            self.update_check_btn.setEnabled(True)
        if hasattr(self, "check_update_btn"):
            self.check_update_btn.setEnabled(True)

        self.latest_update_result = result
        if not result:
            if hasattr(self, "update_banner_label"):
                self.update_banner_label.setText("更新检查失败：无返回结果")
            if hasattr(self, "update_result_label"):
                self.update_result_label.setText("当前版本：v" + APP_VERSION + "\n更新检查失败。")
            return

        if result.error:
            message = f"更新检查失败：{result.error}"
            if hasattr(self, "update_banner_label"):
                self.update_banner_label.setText(message)
            if hasattr(self, "update_result_label"):
                self.update_result_label.setText(
                    f"当前版本：v{APP_VERSION}\n{message}"
                )
            if hasattr(self, "update_open_btn"):
                self.update_open_btn.setEnabled(bool(result.release_url))
            if hasattr(self, "open_release_btn"):
                self.open_release_btn.setEnabled(bool(result.release_url))
            return

        if result.has_update:
            message = (
                f"发现新版本：v{result.latest_version}（当前 v{APP_VERSION}）"
            )
            if hasattr(self, "update_banner_label"):
                self.update_banner_label.setText(message)
            if hasattr(self, "update_result_label"):
                body = (result.body or "").strip()
                extra = f"\n\n{body[:180]}..." if body else ""
                self.update_result_label.setText(
                    f"当前版本：v{APP_VERSION}\n最新版本：v{result.latest_version}\n仓库：{result.repo}{extra}"
                )
            if hasattr(self, "update_open_btn"):
                self.update_open_btn.setEnabled(True)
            if hasattr(self, "open_release_btn"):
                self.open_release_btn.setEnabled(True)
        else:
            message = f"当前已是最新版本（v{APP_VERSION}）"
            if hasattr(self, "update_banner_label"):
                self.update_banner_label.setText(message)
            if hasattr(self, "update_result_label"):
                self.update_result_label.setText(
                    f"当前版本：v{APP_VERSION}\n{message}\n仓库：{result.repo}"
                )
            if hasattr(self, "update_open_btn"):
                self.update_open_btn.setEnabled(bool(result.release_url))
            if hasattr(self, "open_release_btn"):
                self.open_release_btn.setEnabled(bool(result.release_url))

    def open_latest_release(self):
        if self.latest_update_result and self.latest_update_result.release_url:
            open_release_page(self.latest_update_result.release_url)
            return

        repo = (
            self.update_repo_input.text().strip()
            if hasattr(self, "update_repo_input")
            else app_cfg.update_repo
        ).strip()
        if repo:
            open_release_page(f"https://github.com/{repo}/releases/latest")

    def apply_ui_to_config(self):
        """Map UI values to the global config object"""
        app_cfg.sample_rate = self.sample_rate.value()
        app_cfg.silence_threshold = self.silence_thresh.value()
        app_cfg.silence_duration = self.silence_dur.value()
        app_cfg.vad_threshold = self.vad_thresh.value()
        app_cfg.keep_source = self.keep_source_combo.currentText() == "是"
        app_cfg.device_name = self.device_combo.currentText()
        # Also update device_index for compatibility
        try:
            import sounddevice as sd

            for i, d in enumerate(sd.query_devices()):
                if d["name"] == app_cfg.device_name and d["max_input_channels"] > 0:
                    app_cfg.device_index = i
                    break
        except:
            pass
        app_cfg.asr_backend = self.asr_backend.currentText()
        app_cfg.whisper_model = self.whisper_model.currentText()
        app_cfg.funasr_model = self.funasr_model.currentText()
        app_cfg.whisper_device = self.device_type.currentText()
        app_cfg.whisper_compute_type = self.compute_type.currentText()
        
        current_p = self.api_provider.currentText()
        api_key = self.api_key.text()
        api_base_url = self.base_url.text().strip()
        model_name = self.model.currentText().strip()

        # Logic for adding a NEW custom provider
        if current_p == "[自定义]":
            new_name = self.custom_name_input.text().strip()
            if not new_name:
                raise ValueError("请先填写自定义配置名称。")
            if new_name in self.PROTECTED_PROVIDERS:
                raise ValueError("自定义配置名称不能和内置服务商重名。")

            app_cfg.active_provider = new_name
            # Refresh the dropdown to include the new provider
            if self.api_provider.findText(new_name) == -1:
                self.api_provider.insertItem(self.api_provider.count() - 1, new_name)

            # setCurrentText 会触发 currentTextChanged；这里屏蔽信号，避免保存前重新加载空配置。
            with QSignalBlocker(self.api_provider):
                self.api_provider.setCurrentText(new_name)
            self.custom_name_input.setVisible(False)
            self.delete_provider_btn.setEnabled(True)
        else:
            app_cfg.active_provider = current_p
            
        app_cfg.api_key = api_key
        app_cfg.api_base_url = api_base_url
        app_cfg.model = model_name
        app_cfg.target_lang = self.target_lang.currentText()
        if hasattr(self, "transcript_save_dir"):
            save_dir = self.transcript_save_dir.text().strip()
            if save_dir:
                app_cfg.transcript_save_dir = save_dir
        if hasattr(self, "update_repo_input"):
            app_cfg.update_repo = self.update_repo_input.text().strip()
        if hasattr(self, "auto_check_updates_check"):
            app_cfg.auto_check_updates = self.auto_check_updates_check.isChecked()
        app_cfg.translation_prompt = self.trans_prompt_text.toPlainText()
        app_cfg.calibration_prompt = self.calib_prompt_text.toPlainText()
        app_cfg.refinement_prompt = self.refine_prompt_text.toPlainText()

    def save_config(self):
        try:
            self.apply_ui_to_config()
            app_cfg.save()
            QMessageBox.information(self, "成功", "设置已保存！")
        except ValueError as e:
            QMessageBox.warning(self, "提示", str(e))
        except Exception as e:
            QMessageBox.critical(self, "失败", f"保存失败: {e}")

    def _browse_transcript_save_dir(self):
        current_dir = self.transcript_save_dir.text().strip() or app_cfg.transcript_save_dir
        selected = QFileDialog.getExistingDirectory(self, "选择日志保存目录", current_dir)
        if selected:
            self.transcript_save_dir.setText(selected)

    def _open_transcript_save_dir(self):
        target_dir = self.transcript_save_dir.text().strip() or app_cfg.transcript_save_dir
        if not target_dir:
            QMessageBox.warning(self, "提示", "请先设置一个保存目录。")
            return

        try:
            os.makedirs(os.path.expanduser(target_dir), exist_ok=True)
            subprocess.run(["open", os.path.expanduser(target_dir)], check=False)
        except Exception as e:
            QMessageBox.warning(self, "失败", f"无法打开目录: {e}")

    def check_environment(self):
        devices = sd.query_devices()
        has_bh = any("BlackHole" in d["name"] for d in devices)
        if not has_bh:
            self.env_banner.show()

    def open_driver_help(self):
        DriverHelpDialog(self).exec()

    def restart_program(self):
        os.execv(sys.executable, ["python"] + sys.argv)

    # --- Tab Initializations ---

    def init_audio_tab(self):
        tab = QWidget()
        audio_layout = QFormLayout(tab)
        audio_layout.setContentsMargins(20, 20, 20, 20)
        audio_layout.setSpacing(15)

        device_wrapper = QHBoxLayout()
        self.device_combo = QComboBox()
        self.populate_devices()
        device_wrapper.addWidget(self.device_combo, stretch=1)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(40)
        refresh_btn.clicked.connect(self.populate_devices)
        device_wrapper.addWidget(refresh_btn)
        
        audio_layout.addRow("输入设备:", device_wrapper)

        self.sample_rate = QSpinBox()
        self.sample_rate.setRange(8000, 48000)
        self.sample_rate.setValue(app_cfg.sample_rate)
        audio_layout.addRow("采样率 (Hz):", self.sample_rate)

        self.silence_thresh = QDoubleSpinBox()
        self.silence_thresh.setRange(0.001, 1.0)
        self.silence_thresh.setDecimals(3)
        self.silence_thresh.setValue(app_cfg.silence_threshold)
        audio_layout.addRow("静音阈值 (0-1):", self.silence_thresh)

        self.silence_dur = QDoubleSpinBox()
        self.silence_dur.setValue(app_cfg.silence_duration)
        audio_layout.addRow("静音时长 (s):", self.silence_dur)

        self.vad_thresh = QDoubleSpinBox()
        self.vad_thresh.setRange(0.0, 1.0)
        self.vad_thresh.setValue(app_cfg.vad_threshold)
        audio_layout.addRow("VAD 阈值 (0-1):", self.vad_thresh)

        self.keep_source_combo = QComboBox()
        self.keep_source_combo.addItems(["是", "否"])
        self.keep_source_combo.setCurrentText("是" if app_cfg.keep_source else "否")
        audio_layout.addRow("保留源音:", self.keep_source_combo)
        
        return tab

    def populate_devices(self):
        self.device_combo.clear()
        for d in sd.query_devices():
            if d["max_input_channels"] > 0:
                self.device_combo.addItem(d["name"])
        idx = self.device_combo.findText(app_cfg.device_name)
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)

    def init_transcription_tab(self):
        tab = QWidget()
        from PyQt6.QtWidgets import QFormLayout
        trans_layout = QFormLayout(tab)
        
        self.asr_backend = QComboBox()
        self.asr_backend.addItems(["whisper", "mlx", "funasr"])
        self.asr_backend.setCurrentText(app_cfg.asr_backend)
        trans_layout.addRow("识别引擎:", self.asr_backend)

        self.whisper_model = QComboBox()
        self.whisper_model.setEditable(True)
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3", "turbo"])
        self.whisper_model.setCurrentText(app_cfg.whisper_model)
        trans_layout.addRow("Whisper 模型:", self.whisper_model)

        self.funasr_model = QComboBox()
        self.funasr_model.setEditable(True)
        self.funasr_model.addItems(["paraformer", "sensevoice"])
        self.funasr_model.setCurrentText(app_cfg.funasr_model)
        trans_layout.addRow("FunASR 模型:", self.funasr_model)

        self.device_type = QComboBox()
        self.device_type.addItems(["auto", "cpu", "cuda", "mps"])
        self.device_type.setCurrentText(app_cfg.whisper_device)
        trans_layout.addRow("推理设备:", self.device_type)

        self.compute_type = QComboBox()
        self.compute_type.addItems(["float16", "float32", "int8"])
        self.compute_type.setCurrentText(app_cfg.whisper_compute_type)
        trans_layout.addRow("计算精度:", self.compute_type)
        
        return tab

    def _on_backend_changed(self, b):
        self.whisper_model.setEnabled(b in ["whisper", "mlx"])
        self.funasr_model.setEnabled(b == "funasr")

    def init_translation_tab(self):
        tab = QWidget()
        from PyQt6.QtWidgets import QFormLayout
        trans_layout = QFormLayout(tab)
        
        provider_wrapper = QHBoxLayout()
        self.api_provider = QComboBox()
        providers = app_cfg.get_all_providers()
        self.api_provider.addItems(providers)
        self.api_provider.setCurrentText(app_cfg.active_provider)
        self.api_provider.currentTextChanged.connect(self._on_provider_changed)
        provider_wrapper.addWidget(self.api_provider)

        self.delete_provider_btn = QPushButton("删除")
        self.delete_provider_btn.setFixedWidth(80)
        self.delete_provider_btn.clicked.connect(self._on_delete_provider)
        
        # Protected list for built-in providers
        self.delete_provider_btn.setEnabled(app_cfg.active_provider not in self.PROTECTED_PROVIDERS)
        
        provider_wrapper.addWidget(self.delete_provider_btn)
        trans_layout.addRow("服务商:", provider_wrapper)

        self.custom_name_input = QLineEdit()
        self.custom_name_input.setPlaceholderText("例如: MyProvider")
        self.custom_name_input.setVisible(app_cfg.active_provider == "[自定义]")
        trans_layout.addRow("配置名称:", self.custom_name_input)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        # Clear default placeholder if it exists
        initial_key = app_cfg.api_key
        if initial_key == "your_api_key_here":
            initial_key = ""
        self.api_key.setText(initial_key)
        trans_layout.addRow("API Key:", self.api_key)

        self.base_url = QLineEdit(app_cfg.api_base_url)
        trans_layout.addRow("API 地址:", self.base_url)

        model_row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        
        # We will use _on_provider_changed to finish initialization
        model_row.addWidget(self.model, stretch=1)
        
        self.fetch_models_btn = QPushButton("获取")
        self.fetch_models_btn.setFixedWidth(80)
        self.fetch_models_btn.setToolTip("从官方 API 动态获取所有可用的模型列表")
        self.fetch_models_btn.clicked.connect(self._fetch_models)
        model_row.addWidget(self.fetch_models_btn)
        
        trans_layout.addRow("模型名称:", model_row)

        self.target_lang = QComboBox()
        self.target_lang.setEditable(True)
        self.target_lang.addItems(["zh", "en", "ja", "ko", "fr", "de"])
        self.target_lang.setCurrentText(app_cfg.target_lang)
        trans_layout.addRow("目标语言:", self.target_lang)

        # Trigger manual update to ensure synergy with VENDOR_TEMPLATES
        self._on_provider_changed(app_cfg.active_provider)

        return tab

    def init_output_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("日志保存位置")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1e66f5;")
        layout.addWidget(title)

        hint = QLabel(
            "保存按钮会把精修后的日志保存到这里。你可以改成桌面、文稿或外接硬盘。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5c5f77;")
        layout.addWidget(hint)

        form = QFormLayout()
        row = QHBoxLayout()

        self.transcript_save_dir = QLineEdit(app_cfg.transcript_save_dir)
        self.transcript_save_dir.setPlaceholderText(
            "~/Documents/RealtimeSubtitle/Transcripts"
        )
        row.addWidget(self.transcript_save_dir, stretch=1)

        browse_btn = QPushButton("选择...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_transcript_save_dir)
        row.addWidget(browse_btn)

        open_btn = QPushButton("打开")
        open_btn.setFixedWidth(70)
        open_btn.clicked.connect(self._open_transcript_save_dir)
        row.addWidget(open_btn)

        form.addRow("保存目录:", row)
        layout.addLayout(form)

        tip_box = QGroupBox("小提示")
        tip_layout = QVBoxLayout(tip_box)
        tip_layout.addWidget(QLabel("如果目录不存在，程序在第一次保存时会自动创建。"))
        tip_layout.addWidget(
            QLabel("保存成功后会弹窗显示文件路径，并支持一键在访达中显示。")
        )
        layout.addWidget(tip_box)
        layout.addStretch()

        return tab

    def init_update_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("自动更新")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1e66f5;")
        layout.addWidget(title)

        desc = QLabel(
            "程序会通过 GitHub Releases 检查这个仓库是否有新版本。"
            "这里只会读取公开发布页，不会上传你的 API Key。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #5c5f77;")
        layout.addWidget(desc)

        form = QFormLayout()

        self.update_repo_input = QLineEdit(app_cfg.update_repo)
        self.update_repo_input.setPlaceholderText("WZXsea/transworld")
        form.addRow("检查仓库:", self.update_repo_input)

        self.auto_check_updates_check = QCheckBox("启动时自动检查更新")
        self.auto_check_updates_check.setChecked(app_cfg.auto_check_updates)
        form.addRow("自动检查:", self.auto_check_updates_check)

        layout.addLayout(form)

        row = QHBoxLayout()
        self.check_update_btn = QPushButton("立即检查")
        self.check_update_btn.clicked.connect(lambda: self.check_for_updates(manual=True))
        row.addWidget(self.check_update_btn)

        self.open_release_btn = QPushButton("打开 Release 页面")
        self.open_release_btn.clicked.connect(self.open_latest_release)
        row.addWidget(self.open_release_btn)

        row.addStretch()
        layout.addLayout(row)

        self.update_result_label = QLabel("当前版本：v" + APP_VERSION)
        self.update_result_label.setWordWrap(True)
        self.update_result_label.setStyleSheet(
            "padding: 12px; background-color: #f6f8fa; border-radius: 10px; color: #4c4f69;"
        )
        layout.addWidget(self.update_result_label)

        layout.addStretch()
        return tab

    def _fetch_models(self):
        """Fetch available models from the provider's API"""
        api_key = self.api_key.text()
        base_url = self.base_url.text().strip()
        if not api_key or not base_url:
            QMessageBox.warning(self, "错误", "请先填写 API Key 和 API 地址")
            return

        try:
            import requests
            # Standard OpenAI compatible models list endpoint
            url = f"{base_url.rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            
            self.header_title.setText("正在获取模型...")
            QApplication.processEvents()
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            models = []
            
            # OpenAI / SiliconFlow / etc. usually return a list in "data"
            if isinstance(data, dict) and "data" in data:
                models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
            elif isinstance(data, list):
                models = [m["id"] for m in data if isinstance(m, dict) and "id" in m]
            
            if models:
                # Deduplicate and sort
                models = sorted(list(set(models)))
                self.model.clear()
                self.model.addItems(models)
                QMessageBox.information(self, "获取完成", f"已成功获取 {len(models)} 个模型")
            else:
                QMessageBox.warning(self, "警告", "未获取到有效的模型列表")
                
        except Exception as e:
            QMessageBox.critical(self, "请求失败", f"无法获取模型列表:\n{str(e)}")
        finally:
            self.header_title.setText("翻译设置")

    def _on_provider_changed(self, provider):
        """Update API fields when provider changes, clearing if '[自定义]' is selected"""
        self.delete_provider_btn.setEnabled(provider not in self.PROTECTED_PROVIDERS)
        self.custom_name_input.setVisible(provider == "[自定义]")
        
        # Clear model options first
        self.model.clear()
        
        if provider == "[自定义]":
            self.api_key.clear()
            self.base_url.clear()
            self.model.clearEditText()
            self.custom_name_input.clear()
            self.custom_name_input.setFocus()
        else:
            settings = app_cfg.get_vendor_settings(provider)
            template = self.VENDOR_TEMPLATES.get(provider, {})
            
            # 1. Fill models from template if available
            if "models" in template:
                self.model.addItems(template["models"])
            
            # 2. Handle API Key: Clean placeholders
            saved_key = settings.get("api_key", "")
            if saved_key == "your_api_key_here":
                saved_key = ""
            self.api_key.setText(saved_key)
            
            # 3. Handle API URL: Smart fallback if saved value looks invalid
            saved_url = settings.get("api_base_url", "").strip()
            template_url = template.get("base_url", "")
            
            # If saved URL is suspiciously short (e.g. "fes") and template exists, use template
            if template_url and (not saved_url or len(saved_url) < 8 or "://" not in saved_url):
                self.base_url.setText(template_url)
            else:
                self.base_url.setText(saved_url)
            
            # 4. Handle Model Name
            saved_model = settings.get("model", "").strip()
            if saved_model and len(saved_model) > 1: # Basic sanity check
                self.model.setCurrentText(saved_model)
            elif "models" in template:
                self.model.setCurrentIndex(0)
                
            # 切换服务商只负责把对应配置加载到界面。
            # 真正写入 config.ini 必须等用户点击“保存”，避免切换时误覆盖其他 API 配置。

    def _on_delete_provider(self):
        provider = self.api_provider.currentText()
        if provider in self.PROTECTED_PROVIDERS:
            return
            
        reply = QMessageBox.question(self, "确认删除", f"确定要永久删除配置 '{provider}' 吗？",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            if app_cfg.remove_provider(provider):
                # Reload list
                self.api_provider.clear()
                self.api_provider.addItems(app_cfg.get_all_providers())
                self.api_provider.setCurrentText(app_cfg.active_provider)
                QMessageBox.information(self, "成功", f"配置 '{provider}' 已删除")

    def init_device_manager_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("<b>音频 MIDI 设置助推器</b>"))
        layout.addWidget(QLabel("如果您无法直接听见声音并翻译，请使用本工具。"))
        self.output_devices_list = QComboBox()
        self.virtual_devices_list = QComboBox()
        layout.addWidget(QLabel("扬声器:"))
        layout.addWidget(self.output_devices_list)
        layout.addWidget(QLabel("BlackHole:"))
        layout.addWidget(self.virtual_devices_list)
        refresh_btn = QPushButton("刷新设备")
        refresh_btn.clicked.connect(self.refresh_audio_devices)
        layout.addWidget(refresh_btn)
        create_btn = QPushButton("创建多输出设备")
        create_btn.clicked.connect(self.create_multi_output_device)
        layout.addWidget(create_btn)
        self.refresh_audio_devices()
        return tab

    def refresh_audio_devices(self):
        self.output_devices_list.clear()
        self.virtual_devices_list.clear()
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0:
                self.output_devices_list.addItem(d["name"], i)
                if "BlackHole" in d["name"]:
                    self.virtual_devices_list.addItem(d["name"], i)

    def create_multi_output_device(self):
        subprocess.run(["open", "-a", "Audio MIDI Setup"])
        QMessageBox.information(
            self,
            "操作指引",
            "已为您打开'音频 MIDI 设置'。\n1. 点击左下角 [+]\n2. 选择'创建多输出设备'\n3. 勾选您的扬声器和 BlackHole",
        )

    def init_model_management_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        header = QHBoxLayout()
        title = QLabel("模型管理")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1e66f5;")
        header.addWidget(title)
        header.addStretch()
        self.model_refresh_btn = QPushButton("刷新")
        self.model_refresh_btn.setFixedWidth(100)
        self.model_refresh_btn.clicked.connect(self._refresh_model_list)
        header.addWidget(self.model_refresh_btn)
        layout.addLayout(header)

        self.model_progress_label = QLabel("")
        self.model_progress_label.setStyleSheet("color: #5c5f77; font-size: 12px;")
        self.model_progress_label.hide()
        layout.addWidget(self.model_progress_label)

        self.model_progress_bar = QProgressBar()
        self.model_progress_bar.setFixedHeight(16)
        self.model_progress_bar.hide()
        layout.addWidget(self.model_progress_bar)

        self.model_scroll = QScrollArea()
        self.model_scroll.setWidgetResizable(True)
        self.model_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.model_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.model_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self.model_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.model_container = QWidget()
        self.model_container.setMinimumWidth(200)
        self.model_container.setStyleSheet("background-color: transparent;")
        self.model_container_layout = QVBoxLayout(self.model_container)
        self.model_container_layout.setContentsMargins(0, 0, 0, 0)
        self.model_container_layout.setSpacing(8)
        self.model_container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.model_scroll.setWidget(self.model_container)
        layout.addWidget(self.model_scroll, stretch=1)

        self.model_widgets = []
        self._build_model_list()

        return tab

    def _build_model_list(self):
        # Clear child widgets
        while self.model_container_layout.count():
            item = self.model_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.model_widgets.clear()
        self._group_boxes = []

        local_models = ModelManager.scan_local_models()
        all_models = ModelManager.get_all_supported_models()

        groups = {}
        for m in all_models:
            groups.setdefault(m["backend"], []).append(m)

        backend_labels = {
            "mlx": "MLX (Apple Silicon)",
            "whisper": "Faster Whisper",
            "funasr": "FunASR",
        }

        for backend in ["mlx", "whisper", "funasr"]:
            if backend not in groups:
                continue

            group_box = QGroupBox(backend_labels.get(backend, backend))
            group_box.setStyleSheet("""
                QGroupBox {
                    font-size: 15px; font-weight: bold; color: #1e66f5;
                    border: none;
                    margin-top: 15px; padding-top: 10px;
                    background-color: transparent;
                }
                QGroupBox::title {
                    subcontrol-origin: margin; left: 8px; padding: 0;
                }
            """)
            group_layout = QVBoxLayout(group_box)
            group_layout.setContentsMargins(0, 10, 0, 15)
            group_layout.setSpacing(10)

            for m in groups[backend]:
                row = self._create_model_row(m, local_models)
                group_layout.addWidget(row)
                self.model_widgets.append(row)

            self.model_container_layout.addWidget(group_box)
            self._group_boxes.append(group_box)

        self.model_container_layout.addStretch()

    def _is_current_model(self, model_info):
        backend = model_info["backend"]
        repo_id = model_info["repo_id"]
        size_key = model_info.get("size_key")

        if backend == "funasr":
            return app_cfg.asr_backend == "funasr" and app_cfg.funasr_model == repo_id
        else:
            return app_cfg.asr_backend == backend and app_cfg.whisper_model == size_key

    def _create_model_row(self, model_info, local_models):
        repo_id = model_info["repo_id"]
        name = model_info["name"]
        backend = model_info["backend"]

        is_downloaded = repo_id in local_models
        is_current = self._is_current_model(model_info)
        size_info = local_models.get(repo_id, {})
        size_str = ModelManager.get_disk_usage_str(
            size_info.get('size_bytes', 0)
        ) if is_downloaded else "--"

        row = QFrame()
        row.repo_id = repo_id
        row.model_info = model_info

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 8, 8, 8)
        row_layout.setSpacing(8)

        if is_current:
            row.setStyleSheet(
                "QFrame { background-color: #eef4ff; border-radius: 12px; border: 1px solid #d6e4ff; }"
            )
        else:
            row.setStyleSheet(
                "QFrame { background-color: #f6f8fa; border-radius: 12px; border: 1px solid transparent; }"
            )

        indicator = QFrame()
        indicator.setFixedWidth(4)
        indicator.setFixedHeight(28)
        indicator.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if is_current:
            indicator.setStyleSheet("background-color: #1e66f5; border-radius: 2px;")
        elif is_downloaded:
            indicator.setStyleSheet("background-color: #40a02b; border-radius: 2px;")
        else:
            indicator.setStyleSheet("background-color: #dce0e8; border-radius: 2px;")
        row_layout.addWidget(indicator)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(0)

        name_label = QLabel(name)
        name_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #4c4f69; border: none; background: transparent;"
        )
        name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        info_layout.addWidget(name_label)

        meta_text = f"{repo_id} · {size_str}"
        if is_current:
            meta_text += " · 使用中"
        meta_label = QLabel(meta_text)
        meta_label.setStyleSheet(
            "font-size: 11px; color: #9ca0b0; border: none; background: transparent;"
        )
        meta_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        info_layout.addWidget(meta_label)

        row_layout.addLayout(info_layout, stretch=1)
        row_layout.addSpacing(4)

        if is_current:
            tag = QLabel("使用中")
            tag.setStyleSheet(
                "font-size: 10px; color: #1e66f5; font-weight: bold;"
                "background-color: #d6e4ff; border-radius: 6px;"
                "padding: 4px 10px; border: none;"
            )
            row_layout.addWidget(tag)

            del_btn = QPushButton("删除")
            del_btn.setFixedSize(55, 28)
            del_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent; color: #9ca0b0; border: 1px solid #dce0e8;
                    border-radius: 6px; font-size: 10px; padding: 0;
                }
                QPushButton:hover { color: #d20f39; border-color: #d20f39; }
            """)
            del_btn.clicked.connect(
                lambda checked, r=repo_id, w=row: self._delete_model(r, w)
            )
            row_layout.addWidget(del_btn)
        elif is_downloaded:
            use_btn = QPushButton("使用")
            use_btn.setFixedSize(55, 28)
            use_btn.setStyleSheet("""
                QPushButton {
                    background-color: #40a02b; color: white; border: none;
                    border-radius: 10px; font-size: 11px; font-weight: bold; padding: 0;
                }
                QPushButton:hover { background-color: #56c03e; }
            """)
            use_btn.clicked.connect(lambda checked, m=model_info: self._switch_model(m))
            row_layout.addWidget(use_btn)

            del_btn = QPushButton("删除")
            del_btn.setFixedSize(55, 28)
            del_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent; color: #9ca0b0; border: 1px solid #dce0e8;
                    border-radius: 6px; font-size: 10px; padding: 0;
                }
                QPushButton:hover { color: #d20f39; border-color: #d20f39; }
            """)
            del_btn.clicked.connect(
                lambda checked, r=repo_id, w=row: self._delete_model(r, w)
            )
            row_layout.addWidget(del_btn)
        else:
            dl_btn = QPushButton("下载")
            dl_btn.setFixedSize(55, 28)
            dl_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1e66f5; color: white; border: none;
                    border-radius: 10px; font-size: 11px; font-weight: bold; padding: 0;
                }
                QPushButton:hover { background-color: #3578f5; }
            """)
            dl_btn.clicked.connect(
                lambda checked, r=repo_id, w=row: self._download_model(r, w)
            )
            row_layout.addWidget(dl_btn)

        row_layout.addSpacing(5)
        return row

    def _switch_model(self, model_info):
        backend = model_info["backend"]
        size_key = model_info.get("size_key")
        repo_id = model_info["repo_id"]

        app_cfg.asr_backend = backend
        if backend == "funasr":
            app_cfg.funasr_model = repo_id
        else:
            app_cfg.whisper_model = size_key

        app_cfg.save()
        self.asr_backend.setCurrentText(backend)
        if backend in ("whisper", "mlx") and size_key:
            self.whisper_model.setCurrentText(size_key)
        elif backend == "funasr":
            self.funasr_model.setCurrentText(repo_id)

        self._on_backend_changed(backend)
        self._refresh_model_list()

    def _download_model(self, repo_id, row_widget):
        self.model_progress_bar.show()
        self.model_progress_bar.setValue(0)
        self.model_progress_label.show()
        self.model_progress_label.setText(f"准备下载 {repo_id}...")

        btn = row_widget.findChild(QPushButton)
        if btn:
            btn.setEnabled(False)
            btn.setText("⏳")

        self._download_worker = ModelDownloadWorker(repo_id)
        self._download_worker.progress.connect(self._on_model_download_progress)
        self._download_worker.finished.connect(
            lambda success, r=repo_id, w=row_widget: self._on_model_download_done(
                success, r, w
            )
        )
        self._download_worker.start()

    def _on_model_download_progress(self, pct, msg):
        if pct >= 0:
            self.model_progress_bar.setValue(pct)
        self.model_progress_label.setText(msg)

    def _on_model_download_done(self, success, repo_id, row_widget):
        self.model_progress_label.hide()
        self.model_progress_bar.hide()
        if success:
            QMessageBox.information(self, "完成", f"{repo_id} 下载完成！")
        else:
            log_path = os.path.expanduser("~/Library/Logs/RealtimeSubtitle/app.log")
            QMessageBox.warning(self, "失败", f"{repo_id} 下载失败。\n\n请检查网络或查看详细日志：\n{log_path}")
        self._refresh_model_list()

    def _delete_model(self, repo_id, row_widget):
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除模型 {repo_id} 吗？\n删除后需要重新下载。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if ModelManager.delete_model(repo_id):
                QMessageBox.information(self, "完成", f"{repo_id} 已删除。")
                self._refresh_model_list()
            else:
                QMessageBox.warning(self, "失败", f"删除 {repo_id} 失败。")

    def _refresh_model_list(self):
        self.model_progress_bar.hide()
        self.model_progress_label.hide()
        self._build_model_list()

    def init_prompt_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        def create_edit(c):
            e = QTextEdit()
            e.setPlainText(c)
            return e

        layout.addWidget(QLabel("极速翻译:"))
        self.trans_prompt_text = create_edit(app_cfg.translation_prompt)
        layout.addWidget(self.trans_prompt_text)
        layout.addWidget(QLabel("智能校对:"))
        self.calib_prompt_text = create_edit(app_cfg.calibration_prompt)
        layout.addWidget(self.calib_prompt_text)
        layout.addWidget(QLabel("全文精修:"))
        self.refine_prompt_text = create_edit(app_cfg.refinement_prompt)
        layout.addWidget(self.refine_prompt_text)
        return tab


# --- External Helpers ---

class LanguageSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("场景语言选择")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请选择源语言 (视频里的语言):"))
        self.lang_box = QComboBox()
        self.lang_box.addItems(["auto", "ja", "en", "ko", "zh"])
        layout.addWidget(self.lang_box)
        btn = QPushButton("确定")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

    def get_language(self):
        return self.lang_box.currentText()


class StartupWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)

    def run(self):
        try:
            self.progress.emit(20, "加载内核...")
            from main import Pipeline
            p = Pipeline(progress_callback=self.progress.emit)
            self.progress.emit(100, "完成")
            self.finished.emit(p)
        except Exception as e:
            print(f"Startup error: {e}")
            self.finished.emit(None)


class UpdateCheckWorker(QThread):
    finished = pyqtSignal(object)

    def __init__(self, repo, current_version, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.current_version = current_version

    def run(self):
        try:
            checker = GitHubReleaseChecker()
            result = checker.check_latest(self.repo, self.current_version)
            self.finished.emit(result)
        except Exception as e:
            self.finished.emit(
                type(
                    "TempUpdateResult",
                    (),
                    {
                        "repo": self.repo,
                        "current_version": self.current_version,
                        "latest_version": "",
                        "release_name": "",
                        "release_url": "",
                        "published_at": "",
                        "body": "",
                        "has_update": False,
                        "error": f"检查更新失败：{e}",
                    },
                )()
            )


class DriverHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("声卡安装建议")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("未检出 BlackHole 驱动。\n您可以选择手动下载或使用 brew 安装。")
        )
        btn = QPushButton("终端一键安装 (brew)")
        btn.clicked.connect(self.install)
        layout.addWidget(btn)

    def install(self):
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Terminal" to do script "brew install blackhole-2ch"',
            ]
        )


class ModelDownloadWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool)

    def __init__(self, repo_id):
        super().__init__()
        self.repo_id = repo_id

    def run(self):
        try:
            from model_manager import GlobalUI_Tqdm
            from huggingface_hub import snapshot_download
            GlobalUI_Tqdm.reset_cancellation()

            def on_progress(pct, msg):
                self.progress.emit(pct, msg)

            GlobalUI_Tqdm.callback = on_progress
            snapshot_download(
                repo_id=self.repo_id,
                tqdm_class=GlobalUI_Tqdm,
                max_workers=4,
                local_dir_use_symlinks=False, # 强制不使用软连接，解决兼容性之王
            )
            GlobalUI_Tqdm.callback = None
            self.finished.emit(True)
        except Exception as e:
            print(f"[ModelDownload] Error: {e}")
            from model_manager import GlobalUI_Tqdm
            GlobalUI_Tqdm.callback = None
            self.finished.emit(False)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    
    app = QApplication(sys.argv)
    window = Dashboard()
    window.show()
    sys.exit(app.exec())
