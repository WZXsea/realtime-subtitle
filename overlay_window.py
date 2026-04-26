from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QTextEdit,
    QVBoxLayout,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QSizeGrip,
    QHBoxLayout,
    QScrollArea,
    QLabel,
    QFrame,
    QSizePolicy,
)
from PyQt6.QtCore import (
    Qt,
    QPoint,
    QTimer,
    pyqtSignal,
    pyqtProperty,
    QPropertyAnimation,
    QParallelAnimationGroup,
    QEasingCurve,
    QRectF,
)
from PyQt6.QtGui import (
    QFont,
    QColor,
    QPalette,
    QFontMetrics,
    QPainter,
    QTextDocument,
    QTextCursor,
    QTextCharFormat,
)

import sys
import time
from ctypes import c_void_p

class AnimatedLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._text_offset = 0

    def _get_text_offset(self):
        return self._text_offset

    def _set_text_offset(self, value):
        self._text_offset = max(0, int(value))
        self.setIndent(self._text_offset)
        self.update()

    textOffset = pyqtProperty(int, fget=_get_text_offset, fset=_set_text_offset)


class RevealLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._reveal_progress = 1.0

    def _get_reveal_progress(self):
        return self._reveal_progress

    def _set_reveal_progress(self, value):
        self._reveal_progress = max(0.0, min(1.0, float(value)))
        self.update()

    revealProgress = pyqtProperty(float, fget=_get_reveal_progress, fset=_set_reveal_progress)

    def _build_document(self):
        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setDocumentMargin(0)
        doc.setPlainText(self.text())
        color = self._extract_text_color()
        if color is not None:
            cursor = QTextCursor(doc)
            cursor.select(QTextCursor.SelectionType.Document)
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            cursor.mergeCharFormat(fmt)
        rect = self.contentsRect()
        doc.setTextWidth(max(1, rect.width()))
        return doc

    def _extract_text_color(self):
        sheet = self.styleSheet() or ""
        import re

        match = re.search(r"color:\s*([^;]+);", sheet)
        if match:
            raw = match.group(1).strip()
            color = QColor(raw)
            if color.isValid():
                return color
        color = self.palette().color(self.foregroundRole())
        return color if color.isValid() else None

    def sizeHint(self):
        doc = self._build_document()
        size = doc.size().toSize()
        margins = self.contentsMargins()
        return size + margins.topLeft() + margins.bottomRight()

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = self.contentsRect()
        if rect.isEmpty():
            return

        doc = self._build_document()
        reveal_width = max(1, int(rect.width() * self._reveal_progress))
        painter.save()
        painter.setClipRect(QRectF(rect.x(), rect.y(), reveal_width, rect.height()))
        painter.translate(rect.topLeft())
        doc.drawContents(painter, QRectF(0, 0, rect.width(), rect.height()))
        painter.restore()


class LogItem(QFrame):
    """A widget representing a single chunk of transcription/translation"""
    WAITING_TEXTS = {"(翻译中...)", "(积攒语境中...)"}

    def __init__(self, chunk_id, timestamp, original_text, translated_text=""):
        super().__init__()
        self.chunk_id = chunk_id
        self._insert_animation = None
        self._slide_offset = 0
        self._pending_original_text = None
        self._pending_translated_text = None
        self._original_update_timer = QTimer(self)
        self._original_update_timer.setSingleShot(True)
        self._original_update_timer.timeout.connect(self._flush_original_update)
        self._translated_update_timer = QTimer(self)
        self._translated_update_timer.setSingleShot(True)
        self._translated_update_timer.timeout.connect(self._flush_translated_update)
        self._translated_fade = None
        self._original_opacity = None
        self._translated_anim = None
        self._original_anim = None
        
        # Style
        self.setStyleSheet("background-color: transparent;")
        self.layout = QVBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 15)
        self.layout.setSpacing(2)
        self.setLayout(self.layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        # Original Text Label
        self.original_label = RevealLabel(f"[{timestamp}] {original_text}")
        self.original_label.setWordWrap(True)
        self.original_label.setMinimumWidth(10)
        self.original_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.original_label.setStyleSheet(
            "color: #aaaaaa; font-family: '仓耳今楷03', 'TsangerJinKai03', 'Inter', 'Arial'; font-size: 16px;"
        )
        self.original_label.setMinimumHeight(QFontMetrics(self.original_label.font()).lineSpacing() * 2)
        self.layout.addWidget(self.original_label)
        
        visible_translation = "" if translated_text in self.WAITING_TEXTS else translated_text
        self.translated_label = AnimatedLabel(visible_translation)
        self.translated_label.setWordWrap(True)
        self.translated_label.setMinimumWidth(10)
        self.translated_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.translated_label.setStyleSheet(
            "color: #ffffff; font-family: '仓耳今楷03', 'TsangerJinKai03', 'Inter', 'Arial'; font-size: 19px; font-weight: bold;"
        )
        # Reserve a small stable area so streaming tokens do not resize the block on every character.
        self.translated_label.setMinimumHeight(QFontMetrics(self.translated_label.font()).lineSpacing() * 2)
        self._translated_opacity = QGraphicsOpacityEffect(self.translated_label)
        self._translated_opacity.setOpacity(1.0)
        self.translated_label.setGraphicsEffect(self._translated_opacity)
        self.layout.addWidget(self.translated_label)

    def update_translated(self, text, available_width=None, animate=True):
        if text in self.WAITING_TEXTS:
            return
        current = self.translated_label.text()
        if current == text and not self._pending_translated_text:
            return
        # Keep the first visible translation responsive, but coalesce repeated
        # corrections so long sentences do not flash on every token.
        if not current or current in self.WAITING_TEXTS:
            self._pending_translated_text = None
            self._translated_update_timer.stop()
            self._apply_translated_text(text, animate=False)
            return

        self._pending_translated_text = text
        delay = 180 if len(text) < 50 else 260
        self._translated_update_timer.start(delay)
        self._request_overlay_refresh()

    def update_original(self, text, available_width=None, animate=True):
        new_text = f"[{time.strftime('%H:%M:%S')}] {text}"
        if self.original_label.text() == new_text and not self._pending_original_text:
            return
        current = self.original_label.text()
        if current == new_text:
            return

        # Original text also grows in chunks; delay repeated rewrites a little
        # so the block stays visually steady while the sentence is still open.
        if current and current != new_text:
            self._pending_original_text = new_text
            delay = 140 if len(text) < 50 else 220
            self._original_update_timer.start(delay)
            return

        self._pending_original_text = None
        self._original_update_timer.stop()
        self.original_label.setText(new_text)
        self.original_label.update()
        if animate:
            self._animate_original_refresh()

    def _apply_translated_text(self, text, animate=True):
        if self.translated_label.text() == text:
            return
        self.translated_label.setText(text)
        self.updateGeometry()
        self._request_overlay_refresh()
        if animate:
            self._animate_translated_refresh()
        else:
            self._translated_opacity.setOpacity(1.0)
            self.translated_label.setIndent(0)

    def _flush_translated_update(self):
        if not self._pending_translated_text:
            return
        text = self._pending_translated_text
        self._pending_translated_text = None
        self._apply_translated_text(text, animate=True)

    def _flush_original_update(self):
        if not self._pending_original_text:
            return
        text = self._pending_original_text
        self._pending_original_text = None
        if self.original_label.text() == text:
            return
        self.original_label.setText(text)
        self.original_label.update()
        self._animate_original_refresh()

    def _animate_original_refresh(self):
        """Reveal original text from left to right while keeping the block fixed."""
        if self._original_anim is not None:
            try:
                self._original_anim.stop()
            except Exception:
                pass
        self.original_label.revealProgress = 0.0
        reveal_anim = QPropertyAnimation(self.original_label, b"revealProgress", self)
        reveal_anim.setDuration(1000)
        reveal_anim.setStartValue(0.0)
        reveal_anim.setEndValue(1.0)
        reveal_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def finish_anim():
            self.original_label.revealProgress = 1.0

        reveal_anim.finished.connect(finish_anim)
        reveal_anim.start()
        self._original_anim = reveal_anim

    def _animate_translated_refresh(self):
        """Keep translated text stable: only a soft fade, no slide-in jitter."""
        self._translated_opacity.setOpacity(0.3)
        fade_anim = QPropertyAnimation(self._translated_opacity, b"opacity", self)
        fade_anim.setDuration(320)
        fade_anim.setStartValue(0.3)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def finish_anim():
            self._translated_opacity.setOpacity(1.0)
            self.translated_label.setIndent(0)

        fade_anim.finished.connect(finish_anim)
        fade_anim.start()
        self._translated_anim = fade_anim

    def _request_overlay_refresh(self):
        top = self.window()
        if top and hasattr(top, "_refresh_overlay_layout"):
            top._schedule_overlay_refresh()

    def _get_slide_offset(self):
        return self._slide_offset

    def _set_slide_offset(self, value):
        self._slide_offset = max(0, int(value))
        self.layout.setContentsMargins(self._slide_offset, 0, 0, 15)
        self.updateGeometry()

    slideOffset = pyqtProperty(int, fget=_get_slide_offset, fset=_set_slide_offset)

    def animate_insert(self, slide_distance=0, keep_bottom=None):
        """Slide in from the right while expanding upward in the subtitle stack."""
        target_height = max(self.sizeHint().height(), 48)
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)
        self.slideOffset = slide_distance

        height_animation = QPropertyAnimation(self, b"maximumHeight", self)
        height_animation.setDuration(560)
        height_animation.setStartValue(0)
        height_animation.setEndValue(target_height)
        height_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        if keep_bottom:
            height_animation.valueChanged.connect(lambda _value: keep_bottom())

        slide_animation = QPropertyAnimation(self, b"slideOffset", self)
        slide_animation.setDuration(620)
        slide_animation.setStartValue(slide_distance)
        slide_animation.setEndValue(0)
        slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._insert_animation = QParallelAnimationGroup(self)
        self._insert_animation.addAnimation(height_animation)
        self._insert_animation.addAnimation(slide_animation)

        def finish_animation():
            self.setMaximumHeight(16777215)
            self.setMinimumHeight(0)
            self.slideOffset = 0
            self.updateGeometry()
            if keep_bottom:
                keep_bottom()

        self._insert_animation.finished.connect(finish_animation)
        self._insert_animation.start()

class ResizeHandle(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_window = parent
        self.setText("◢")
        self.setStyleSheet("color: rgba(255, 255, 255, 100); font-size: 16px;")
        self.setFixedSize(20, 20)
        self.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        
        self.startPos = None
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.startPos = event.globalPosition().toPoint()
            event.accept()
            
    def mouseMoveEvent(self, event):
        if self.startPos:
            delta = event.globalPosition().toPoint() - self.startPos
            new_width = max(self.parent_window.minimumWidth(), self.parent_window.width() + delta.x())
            new_height = max(self.parent_window.minimumHeight(), self.parent_window.height() + delta.y())
            
            self.parent_window.resize(new_width, new_height)
            self.startPos = event.globalPosition().toPoint()
            event.accept()
            
    def mouseReleaseEvent(self, event):
        self.startPos = None

class OverlayWindow(QWidget):
    stop_requested = pyqtSignal()
    pause_toggled = pyqtSignal()
    save_requested = pyqtSignal(dict)

    def __init__(self, display_duration=None, window_width=400, window_height=400, model_name="Unknown"):
        super().__init__()
        self.window_width = window_width
        self.window_height = window_height
        self.model_name = model_name
        
        self.initUI()
        self.oldPos = self.pos()
        self._macos_window_enhanced = False
        self._welcome_shown = False
        self._layout_refresh_pending = False
        self._follow_latest = True

    def initUI(self):
        # Window flags for transparency and staying on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.WindowDoesNotAcceptFocus |
            Qt.WindowType.ToolTip  # ToolTip is more persistent on macOS than Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        # Layout
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.setLayout(self.main_layout)
        
        # Stack viewport for LogItems. The subtitles still live in one visual
        # plane, but the viewport can scroll so the window itself does not keep
        # growing forever.
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; }
            QScrollBar:vertical { width: 0px; }
        """)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Container for LogItems.
        self.container = QFrame()
        self.container.setStyleSheet("background-color: rgba(0, 0, 0, 150); border-radius: 10px;")
        self.container_layout = QVBoxLayout()
        self.container_layout.setContentsMargins(10, 10, 10, 10)
        # Anchor content to the bottom so newer items push older ones upward.
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.container.setLayout(self.container_layout)
        
        self.scroll_area.setWidget(self.container)
        self.main_layout.addWidget(self.scroll_area, stretch=1)
        
        # Initialize Buttons
        from PyQt6.QtWidgets import QPushButton, QStyle 
        self.save_btn = QPushButton("💾 保存")
        self._save_btn_default_text = "💾 保存"
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setFixedWidth(80)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 50);
                color: white;
                border-radius: 5px;
                padding: 5px;
                border: none;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 100);
            }
        """)
        self.save_btn.clicked.connect(self._save_transcript)
        
        # Clear Button
        self.clear_btn = QPushButton("🗑 清除")
        self.clear_btn.setToolTip("清除屏幕上的所有记录")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setFixedWidth(80)
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 50);
                color: white;
                border-radius: 5px;
                padding: 5px;
                border: none;
            }
            QPushButton:hover {
                background-color: rgba(243, 139, 168, 150);
            }
        """)
        self.clear_btn.clicked.connect(self._clear_history)
        
        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setToolTip("停止翻译")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setFixedSize(30, 30)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(243, 139, 168, 150);
                color: white;
                border-radius: 15px;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(243, 139, 168, 200);
            }
        """)
        self.stop_btn.clicked.connect(self.stop_requested.emit)

        self.pause_btn = QPushButton("⏸")
        self.pause_btn.setToolTip("暂停/继续翻译")
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.setFixedSize(30, 30)
        self.pause_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 102, 245, 150);
                color: white;
                border-radius: 15px;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(30, 102, 245, 210);
            }
        """)
        self.pause_btn.clicked.connect(self.pause_toggled.emit)
        
        # Control Bar Container (for stable positioning)
        self.control_bar = QFrame()
        self.control_bar.setFixedHeight(50)
        self.control_bar.setStyleSheet("""
            QFrame {
                background-color: rgba(20, 20, 20, 220); 
                border-top: 1px solid rgba(255, 255, 255, 30);
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
            }
        """)
        control_layout = QHBoxLayout(self.control_bar)
        control_layout.setContentsMargins(15, 0, 15, 0)
        
        # Visual Grip Indicator
        self.grip_label = ResizeHandle(self)
        
        # Add buttons to control layout
        control_layout.addWidget(self.save_btn)
        control_layout.addWidget(self.clear_btn)
        control_layout.addWidget(self.pause_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addStretch()
        
        # Attribution Label (Model Info)
        display_model = self.model_name if self.model_name and self.model_name != "Unknown" else "AI"
        self.model_label = QLabel(f"Powered by {display_model}")
        self.model_label.setStyleSheet("""
            color: rgba(255, 255, 255, 100); 
            font-size: 11px; 
            font-family: 'Inter', 'Arial';
            margin-right: 5px;
        """)
        control_layout.addWidget(self.model_label)
        
        control_layout.addWidget(self.grip_label)
        
        self.main_layout.addWidget(self.control_bar)

        # Token stats badge in the top-right of the translation panel.
        self.stats_panel = QFrame(self)
        self.stats_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.stats_panel.setStyleSheet("""
            QFrame {
                background-color: rgba(20, 20, 20, 135);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 8px;
            }
        """)
        stats_layout = QHBoxLayout(self.stats_panel)
        stats_layout.setContentsMargins(10, 4, 10, 4)
        stats_layout.setSpacing(8)

        self.input_token_label = QLabel("输入token 0")
        self.output_token_label = QLabel("输出token 0")
        self.total_token_label = QLabel("总token 0")
        for label in (self.input_token_label, self.output_token_label, self.total_token_label):
            label.setStyleSheet("""
                color: rgba(255, 255, 255, 175);
                font-size: 10px;
                font-family: 'Inter', 'Arial';
            """)
            stats_layout.addWidget(label)

        self.stats_panel.adjustSize()
        self.stats_panel.raise_()

        # Keep the overlay resizable from a practical minimum, even when there
        # are lots of subtitle blocks in the stack.
        self.setMinimumSize(320, 180)

        self.scroll_area.verticalScrollBar().valueChanged.connect(self._handle_scrollbar_change)
        
        # Set initial window size
        self.resize(self.window_width, self.window_height)
        
        # Position: Right side of screen, full height
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + screen.width() - self.window_width - 20 # 20px padding from right
        y = screen.y()
        self.move(x, y)
        
        # Data storage: list of (chunk_id, widget) inclusive
        self.items = [] # Sorted by chunk_id
        
        # History for saving (list of dicts)
        self.transcript_data = {} # chunk_id -> {timestamp, original, translated}
        
        # State
        self.is_moving = False
        
        # Enable mouse tracking for cursor update without click
        self.setMouseTracking(True)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "darwin":
            QTimer.singleShot(0, self._apply_macos_fullscreen_overlay_behavior)
        if not self._welcome_shown:
            self._welcome_shown = True
            QTimer.singleShot(250, self._show_welcome_message)
        QTimer.singleShot(0, self._position_stats_panel)

    def _show_welcome_message(self):
        display_model = self.model_name if self.model_name and self.model_name != "Unknown" else "当前 AI"
        self._insert_log_item(
            -1,
            "系统提示",
            f"欢迎使用，本服务由 {display_model} 模型提供",
            save_to_transcript=False,
        )

    def _apply_macos_fullscreen_overlay_behavior(self):
        """Make the overlay visible above fullscreen apps on macOS.

        Qt's WindowStaysOnTopHint is enough for normal desktops, but fullscreen apps
        live in separate Spaces. The native NSWindow flags below opt this window into
        those Spaces and mark it as a fullscreen auxiliary overlay.
        """
        if self._macos_window_enhanced:
            return

        try:
            import objc
            from AppKit import (
                NSFloatingWindowLevel,
                NSScreenSaverWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
                NSWindowCollectionBehaviorStationary,
            )
        except Exception as e:
            print(f"[Overlay] macOS fullscreen overlay enhancement unavailable: {e}")
            return

        try:
            view = objc.objc_object(c_void_p=c_void_p(int(self.winId())))
            ns_window = view.window()
            if ns_window is None:
                QTimer.singleShot(100, self._apply_macos_fullscreen_overlay_behavior)
                return

            behavior = (
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorIgnoresCycle
            )
            ns_window.setCollectionBehavior_(behavior)
            ns_window.setLevel_(max(NSFloatingWindowLevel, NSScreenSaverWindowLevel - 1))
            ns_window.setCanHide_(False)
            ns_window.setHidesOnDeactivate_(False)
            ns_window.setReleasedWhenClosed_(False)
            ns_window.orderFrontRegardless()
            self._macos_window_enhanced = True
            print("[Overlay] macOS fullscreen overlay behavior enabled")
        except Exception as e:
            print(f"[Overlay] Failed to apply macOS fullscreen overlay behavior: {e}")

    def update_text(self, chunk_id, original_text, translated_text):
        """Append new text or update existing text"""
        if original_text or translated_text:
            print(f"[Overlay] Received update for #{chunk_id}: {original_text} -> {translated_text}")
            
        # Update data store
        if chunk_id not in self.transcript_data:
            self.transcript_data[chunk_id] = {
                'timestamp': time.strftime("%H:%M:%S"),
                'original': original_text,
                'translated': translated_text if translated_text not in LogItem.WAITING_TEXTS else ""
            }
        else:
            # IMPORTANT: Update original even if chunk exists (it grows during partial updates)
            if original_text:
                self.transcript_data[chunk_id]['original'] = original_text
            
            if translated_text and translated_text not in LogItem.WAITING_TEXTS:
                self.transcript_data[chunk_id]['translated'] = translated_text
        
        # Check if widget exists
        existing_widget = None
        for cid, widget in self.items:
            if cid == chunk_id:
                existing_widget = widget
                break
        
        if existing_widget:
            # Update existing
            if original_text:
                existing_widget.update_original(original_text)
            
            if translated_text:
                existing_widget.update_translated(translated_text)
                
            print(f"[Overlay] Updated existing widget #{chunk_id}")
        else:
            self._insert_log_item(chunk_id, original_text, translated_text)

    def _insert_log_item(
        self,
        chunk_id,
        original_text,
        translated_text,
        save_to_transcript=True,
    ):
        timestamp = (
            self.transcript_data.get(chunk_id, {}).get("timestamp")
            if save_to_transcript
            else time.strftime("%H:%M:%S")
        )
        if not timestamp:
            timestamp = time.strftime("%H:%M:%S")

        new_widget = LogItem(chunk_id, timestamp, original_text, translated_text)

        insert_idx = len(self.items)
        for i, (cid, _widget) in enumerate(self.items):
            if cid > chunk_id:
                insert_idx = i
                break

        self.items.insert(insert_idx, (chunk_id, new_widget))
        self.container_layout.insertWidget(insert_idx, new_widget)
        slide_distance = max(120, self.width() // 2)
        new_widget.animate_insert(
            slide_distance=slide_distance,
            keep_bottom=self._scroll_to_bottom if self._follow_latest else None,
        )
        self._schedule_overlay_refresh()
        print(f"[Overlay] Inserted new widget #{chunk_id} at index {insert_idx}")

    def _scroll_to_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _is_at_bottom(self):
        sb = self.scroll_area.verticalScrollBar()
        return sb.value() >= max(0, sb.maximum() - 20)

    def _handle_scrollbar_change(self, value):
        sb = self.scroll_area.verticalScrollBar()
        self._follow_latest = value >= max(0, sb.maximum() - 20)

    def _schedule_overlay_refresh(self):
        if self._layout_refresh_pending:
            return
        self._layout_refresh_pending = True
        QTimer.singleShot(0, self._refresh_overlay_layout)

    def _refresh_overlay_layout(self):
        """Force Qt to recompute the stack after text changes.

        Without this, long wrapped lines can lag behind until a manual resize
        triggers a full relayout.
        """
        self._layout_refresh_pending = False
        if not hasattr(self, "scroll_area"):
            return

        self.container_layout.invalidate()
        self.container.adjustSize()
        self.container.updateGeometry()
        self.container.update()
        self.scroll_area.widget().adjustSize()
        self.scroll_area.viewport().update()
        self._position_stats_panel()
        if self._follow_latest:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _clear_history(self):
        """Clear all transcript items from the overlay"""
        # Remove all widgets from container
        for cid, widget in self.items:
            self.container_layout.removeWidget(widget)
            widget.deleteLater()
        
        self.items.clear()
        self.transcript_data.clear()
        print("[Overlay] 🗑 History cleared")

    def _save_transcript(self):
        """Request the pipeline to save and refine the current transcript"""
        if not self.transcript_data:
            print("[Overlay] 没有可保存的内容。")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "提示", "当前没有可保存的字幕内容。")
            return

        print("[Overlay] Save requested from UI...")
        self.set_save_status("保存中...")
        self.save_requested.emit(self.transcript_data.copy())

    def set_save_status(self, message):
        self.save_btn.setEnabled(False)
        self.save_btn.setText(message or "保存中...")

    def finish_save_status(self, success=True, title="", final_path="", detail=""):
        self.save_btn.setEnabled(True)
        self.save_btn.setText(self._save_btn_default_text)

    def set_paused(self, paused):
        self.pause_btn.setText("▶" if paused else "⏸")
        self.pause_btn.setToolTip("继续翻译" if paused else "暂停翻译")

    def update_token_stats(self, stats):
        prompt = int(stats.get("prompt_tokens", 0) or 0)
        completion = int(stats.get("completion_tokens", 0) or 0)
        total = int(stats.get("total_tokens", 0) or 0)
        estimated = int(stats.get("estimated_tokens", 0) or 0)
        self.input_token_label.setText(f"输入token {prompt}")
        self.output_token_label.setText(f"输出token {completion}")
        self.total_token_label.setText(f"总token {total}")
        if estimated > 0:
            self.stats_panel.setToolTip(f"本次累计估算 token: {estimated}")
        else:
            self.stats_panel.setToolTip("本次累计 token 使用来自接口返回")

    def _position_stats_panel(self):
        if not hasattr(self, "stats_panel"):
            return

        self.stats_panel.adjustSize()
        margin = 10
        panel_w = self.stats_panel.sizeHint().width()
        panel_h = self.stats_panel.sizeHint().height()

        scroll_geo = self.scroll_area.geometry()
        x = scroll_geo.x() + scroll_geo.width() - panel_w - margin
        y = scroll_geo.y() + margin

        self.stats_panel.setGeometry(x, y, panel_w, panel_h)
        self.stats_panel.raise_()

    # Window Moving Logic (Resize is handled by ResizeHandle widget)
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_moving = True
            self.oldPos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        # Update cursor shape based on position (reset to arrow)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        
        # Handle dragging
        if self.is_moving:
            delta = event.globalPosition().toPoint() - self.oldPos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.oldPos = event.globalPosition().toPoint()
            
    def mouseReleaseEvent(self, event):
        self.is_moving = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_stats_panel()
        if self._follow_latest:
            QTimer.singleShot(0, self._scroll_to_bottom)

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    import sys
    app = QApplication(sys.argv)
    window = OverlayWindow(model_name="DeepSeek-Chat")
    window.show()
    # Test update
    window.update_text(1, "Hello world", "")
    QTimer.singleShot(1000, lambda: window.update_text(1, "Hello world", "你好，世界"))
    window.update_text(2, "Sequence test", "")
    sys.exit(app.exec())
