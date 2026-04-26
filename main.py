import os
import multiprocessing
import re
import numpy as np
from collections import OrderedDict
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import sys
import signal
import threading
import queue
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from audio_capture import AudioCapture
from transcriber import Transcriber
from translator import Translator
from overlay_window import OverlayWindow
from config import config

class WorkerSignals(QObject):
    update_text = pyqtSignal(int, str, str)  # (chunk_id, original, translated)
    stats_updated = pyqtSignal(dict)


class SaveResultNotifier(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_widget = parent
        self._progress_msg = None

    def show_result(self, success, title, final_path, detail):
        if success:
            self._show_success_dialog(title, final_path, detail)
        else:
            self._show_failure_dialog(detail)

    def show_progress(self, message="正在保存..."):
        if self._progress_msg is not None:
            try:
                self._progress_msg.close()
            except Exception:
                pass
        msg = QMessageBox(self.parent_widget)
        self._progress_msg = msg
        msg.setWindowTitle("保存中")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(message)
        msg.setStandardButtons(QMessageBox.StandardButton.NoButton)
        msg.setWindowModality(Qt.WindowModality.ApplicationModal)
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        msg.show()
        msg.raise_()
        msg.activateWindow()
        QTimer.singleShot(700, lambda: self._close_progress(msg))

    def _close_progress(self, msg):
        if self._progress_msg is msg:
            self._progress_msg = None
        try:
            msg.close()
        except Exception:
            pass

    def _show_success_dialog(self, title, final_path, save_dir):
        folder = save_dir or os.path.dirname(final_path)
        msg = QMessageBox(self.parent_widget)
        msg.setWindowTitle("保存完成")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("日志已保存成功")
        msg.setInformativeText(
            f"标题：{title}\n文件：{final_path}\n保存目录：{folder}"
        )
        open_file_btn = msg.addButton("在访达中显示", QMessageBox.ButtonRole.ActionRole)
        open_dir_btn = msg.addButton("打开保存目录", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        msg.setWindowModality(Qt.WindowModality.ApplicationModal)
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == open_file_btn:
            self._reveal_in_finder(final_path)
        elif clicked == open_dir_btn:
            self._open_folder(folder)

    def _show_failure_dialog(self, detail):
        msg = QMessageBox(self.parent_widget)
        msg.setWindowTitle("保存失败")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setText(detail or "日志保存失败，请查看应用日志。")
        msg.setWindowModality(Qt.WindowModality.ApplicationModal)
        msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        msg.exec()

    def _open_folder(self, folder):
        if not folder:
            return
        try:
            import subprocess

            subprocess.run(["open", folder], check=False)
        except Exception as e:
            QMessageBox.warning(self.parent_widget, "打开失败", f"无法打开目录：{e}")

    def _reveal_in_finder(self, path):
        if not path:
            return
        try:
            import subprocess

            subprocess.run(["open", "-R", path], check=False)
        except Exception as e:
            QMessageBox.warning(self.parent_widget, "打开失败", f"无法在访达中显示：{e}")


@dataclass(order=True)
class TranslationJob:
    priority: int
    created_at: float
    seq: int
    mode: str = field(compare=False, default="primary")
    original_text: str = field(compare=False, default="")
    chunk_ids: tuple = field(compare=False, default_factory=tuple)
    extra_context: str = field(compare=False, default="")
    context_limit: int = field(compare=False, default=1)
    max_tokens: int = field(compare=False, default=180)
    timeout: float = field(compare=False, default=8.0)
    temperature: float = field(compare=False, default=0.15)
    snapshot: str = field(compare=False, default="")
    apply_delay: float = field(compare=False, default=0.0)

class Pipeline(QObject):
    save_result = pyqtSignal(bool, str, str, str)
    save_status = pyqtSignal(str)

    def __init__(self, target_lang=None, progress_callback=None):
        super().__init__()
        self.signals = WorkerSignals()
        self.running = True
        self.paused = False
        self._pause_lock = threading.Lock()
        self.progress_callback = progress_callback
        
        # Use provided target_lang or fallback to config
        self.effective_target_lang = target_lang or config.target_lang
        
        # Print config for debugging
        config.print_config()
        if target_lang:
            print(f"[Pipeline] Runtime Target Language Override: {target_lang}")
        
        # Initialize components
        self.audio = AudioCapture(
            device_index=config.device_index,
            sample_rate=config.sample_rate,
            silence_threshold=config.silence_threshold,
            silence_duration=config.silence_duration,
            chunk_duration=config.chunk_duration,
            max_phrase_duration=config.max_phrase_duration,
            streaming_mode=config.streaming_mode,
            streaming_interval=config.streaming_interval,
            streaming_step_size=config.streaming_step_size,
            streaming_overlap=config.streaming_overlap
        )
        
        # Initialize Transcriber
        print(f"[Pipeline] Initializing Transcriber with backend={config.asr_backend}, device={config.whisper_device}...")
        
        # Determine model size based on backend
        if config.asr_backend == "funasr":
            model_size = config.funasr_model
        else:
            model_size = config.whisper_model
            
        self.transcriber = Transcriber(
            backend=config.asr_backend,
            model_size=model_size,
            device=config.whisper_device,
            compute_type=config.whisper_compute_type,
            language=config.source_language,
            progress_callback=self.progress_callback
        )
        
        # Initialize Translator
        print(f"[Pipeline] Initializing Translator (target={self.effective_target_lang})...")
        self.translator = Translator(
            target_lang=self.effective_target_lang,
            base_url=config.api_base_url,
            api_key=config.api_key,
            model=config.model
        )
        
        # Warmup Transcriber (Critical for MLX/GPU)
        self.transcriber.warmup()
        
        # History for final refinement
        self.translated_history = []

        # New: Transcription buffer to avoid fragmentary translations
        self.pending_originals = []
        self.pending_ids = []
        self.pending_since = 0  # timestamp when first item entered buffer
        self.last_final_text = "" # Context for ASR
        self._partial_future = None # Track ongoing partial transcription to avoid queueing backpressure
        self.pending_lock = threading.Lock()

        # Translation scheduling
        self.translation_jobs = queue.PriorityQueue()
        self._translation_seq = 0
        self._translation_seq_lock = threading.Lock()
        self._translation_workers_started = False
        self._translation_workers = []
        self._translation_monitor_stop = threading.Event()
        self._translation_monitor_thread = None
        self._partial_translation_state = {}
        self._partial_translation_lock = threading.Lock()
        self._runtime_memory_lock = threading.Lock()
        self._runtime_translation_memory = OrderedDict()
        self._correction_state_lock = threading.Lock()
        self._queued_correction_targets = set()
        self._correction_counts = {}
        self._usage_lock = threading.Lock()
        self.usage_stats = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_tokens": 0,
        }
        self._token_encoder = None

    def start(self):
        """Start the processing pipeline in a dedicated thread"""
        # self.audio.start() # DISABLE: Generator manages its own stream. calling this causes double-stream error on macOS
        self.thread = threading.Thread(target=self.processing_loop)
        self.thread.daemon = True
        self.thread.start()
        self._start_translation_workers()
        self._start_translation_monitor()

    def stop(self):
        print("\n[Pipeline] Stopping...")
        self.running = False
        self.resume()
        self.audio.stop()
        self._translation_monitor_stop.set()
        
        # New: Automatically saving on exit disabled as per user request.
        # self._finalize_transcripts()
        
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        print("[Pipeline] Stopped.")

    def pause(self):
        with self._pause_lock:
            self.paused = True
        print("[Pipeline] Paused.")

    def resume(self):
        with self._pause_lock:
            was_paused = self.paused
            self.paused = False
        if was_paused:
            print("[Pipeline] Resumed.")

    def is_paused(self):
        with self._pause_lock:
            return self.paused

    def _wait_if_paused(self):
        while self.running and self.is_paused():
            time.sleep(0.05)

    def processing_loop(self):
        """Fully parallel pipeline: multiple concurrent transcription + translation"""
        print("Pipeline processing loop started (FULLY PARALLEL mode).")
        
        # Create multiple transcribers for concurrent processing
        # CHECK: If using MLX, force 1 worker (MLX is not thread-safe for parallel inference in this way)
        is_mlx = (config.asr_backend == "mlx")
        
        if is_mlx:
            print("[Pipeline] MLX backend detected - forcing single worker (MLX uses GPU parallelism internaly)")
            num_transcription_workers = 1
        else:
            num_transcription_workers = config.transcription_workers
            
        print(f"[Pipeline] Using {num_transcription_workers} transcription workers...")
        
        # Determine model size based on backend
        if config.asr_backend == "funasr":
            model_size = config.funasr_model
        else:
            model_size = config.whisper_model
        
        transcribers = [self.transcriber]  # Reuse existing one
        for i in range(num_transcription_workers - 1):
            t = Transcriber(
                backend=config.asr_backend,
                model_size=model_size,
                device=config.whisper_device,
                compute_type=config.whisper_compute_type,
                language=config.source_language
            )
            transcribers.append(t)
        """Accumulating Buffer Processing Loop (Word-by-Word Streaming)"""
        print("[Pipeline] processing loop started (Accumulating Mode).")
        
        import numpy as np
        
        # Executors
        transcribe_executor = ThreadPoolExecutor(max_workers=1) # Serial transcription

        # State
        buffer = np.array([], dtype=np.float32)
        chunk_id = 1
        last_update_time = time.time()
        phrase_start_time = time.time()
        
        # Generator yielding small chunks (e.g. 0.2s)
        audio_gen = self.audio.generator()
        
        # Context Management
        self.last_final_text = ""

        try:
            for audio_chunk in audio_gen:
                if not self.running:
                    break
                if self.is_paused():
                    # Drop live audio while paused so resume starts from a clean phrase boundary.
                    buffer = np.array([], dtype=np.float32)
                    last_update_time = time.time()
                    phrase_start_time = last_update_time
                    if self.pending_originals and self.pending_since > 0:
                        self._flush_pending_translation(reason="pause")
                    continue
                buffer = np.concatenate([buffer, audio_chunk])
                now = time.time()
                buffer_duration = len(buffer) / self.audio.sample_rate
                
                # Check silence for finalization
                # Use configured silence duration/threshold
                is_silence = False
                min_silence_dur = config.silence_duration # e.g. 1.0s
                
                # Only check silence if we have enough buffer
                if buffer_duration > min_silence_dur:
                     # Check tail of silence duration
                    tail = buffer[-int(self.audio.sample_rate * min_silence_dur):]
                    rms = np.sqrt(np.mean(tail**2))
                    if rms < self.audio.silence_threshold:
                        is_silence = True
                        
                # Dynamic VAD Logic
                # 1. Standard: > streaming_interval duration AND > silence_duration silence
                standard_cut = (is_silence and buffer_duration > config.streaming_interval)
                
                # 2. Soft Limit: > 80% of max_phrase_duration AND brief silence
                soft_limit_cut = False
                soft_limit_threshold = self.audio.max_phrase_duration * 0.8
                if buffer_duration > soft_limit_threshold:
                    # Check shorter silence tail (scaled with config)
                    short_silence_dur = max(0.4, self.audio.silence_duration * 0.6)
                    short_tail_samps = int(self.audio.sample_rate * short_silence_dur)
                    if len(buffer) > short_tail_samps:
                        t_rms = np.sqrt(np.mean(buffer[-short_tail_samps:]**2))
                        if t_rms < self.audio.silence_threshold:
                            soft_limit_cut = True
                            
                # 3. Hard Limit: > max_phrase_duration (Force cut)
                hard_limit_cut = (buffer_duration > self.audio.max_phrase_duration)

                should_finalize = standard_cut or soft_limit_cut or hard_limit_cut
                
                if should_finalize and buffer_duration > 0.5:
                    # FINALIZE
                    final_buffer = buffer.copy()
                    cid = chunk_id
                    
                    # 统一语境窗口调用
                    prompt = self._get_context_prompt()
                    
                    # PRE-CHECK: Is the entire buffer actually silence?
                    overall_rms = np.sqrt(np.mean(final_buffer**2))
                    if overall_rms > self.audio.silence_threshold:
                        # Submit Final Task
                        transcribe_executor.submit(self._process_final_chunk, final_buffer, cid, prompt)
                    else:
                        print(f"[Pipeline] Skipped silent chunk {cid} (RMS={overall_rms:.4f})")
                    
                    # ALWAYS reset audio buffer and timers after a phrase end is detected
                    buffer = np.array([], dtype=np.float32)
                    chunk_id += 1
                    phrase_start_time = now
                    last_update_time = now
                    
                # 2. Partial Update if: Interval passed AND not finalizing
                elif now - last_update_time > config.update_interval and buffer_duration > 0.5:
                    # 使用统一的语境窗口
                    prompt = self._get_context_prompt()
                    partial_buffer = buffer.copy()
                    
                    # RMS Check to avoid partial hallucination on silence
                    rms = np.sqrt(np.mean(partial_buffer**2))
                    if rms > self.audio.silence_threshold:
                        # DROP redundant partial updates if background tasks are congested
                        if self._partial_future is None or self._partial_future.done():
                            self._partial_future = transcribe_executor.submit(self._process_partial_chunk, partial_buffer, chunk_id, prompt)
                    
                    last_update_time = now
                
                # 3. Timeout flush: if pending buffer has text sitting > 3s, force translate
                if self.pending_originals and self.pending_since > 0:
                    if now - self.pending_since > 1.5:  # Reduced from 3.0 to 1.5 for snapiness
                        print(f"[Pipeline] 超时刷新: {len(self.pending_originals)} chunks waiting {now - self.pending_since:.1f}s")
                        self._flush_pending_translation(reason="timeout")
                    
        except Exception as e:
            print(f"[Pipeline] Error in loop: {e}")
        finally:
            transcribe_executor.shutdown(wait=False)

    def _start_translation_workers(self):
        if self._translation_workers_started:
            return
        self._translation_workers_started = True

        worker_count = max(1, config.translation_threads)
        print(f"[Pipeline] Starting {worker_count} translation workers...")
        for idx in range(worker_count):
            worker = threading.Thread(
                target=self._translation_worker_loop,
                name=f"translation-worker-{idx + 1}",
                daemon=True,
            )
            worker.start()
            self._translation_workers.append(worker)

    def _start_translation_monitor(self):
        if self._translation_monitor_thread and self._translation_monitor_thread.is_alive():
            return

        self._translation_monitor_thread = threading.Thread(
            target=self._pending_flush_monitor_loop,
            name="translation-flush-monitor",
            daemon=True,
        )
        self._translation_monitor_thread.start()

    def _pending_flush_monitor_loop(self):
        """Background watchdog to make sure stale text always gets translated."""
        while self.running and not self._translation_monitor_stop.is_set():
            try:
                self._flush_pending_translation(reason="watchdog")
            except Exception as e:
                print(f"[Pipeline] Pending flush monitor error: {e}")
            time.sleep(0.12)

    def _next_translation_seq(self):
        with self._translation_seq_lock:
            self._translation_seq += 1
            return self._translation_seq

    def _get_token_encoder(self):
        if self._token_encoder is None:
            try:
                import tiktoken

                self._token_encoder = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self._token_encoder = False
        return self._token_encoder if self._token_encoder else None

    def _estimate_text_tokens(self, text):
        if not text:
            return 0
        encoder = self._get_token_encoder()
        if encoder:
            try:
                return len(encoder.encode(text))
            except Exception:
                pass
        return max(1, len(text) // 2)

    def _usage_to_dict(self, usage):
        if not usage:
            return None
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            "estimated": False,
        }

    def _estimate_usage(self, original_text, output_text, mode="primary", extra_context=None, context_limit=None):
        system_prompt = self.translator._build_system_prompt(
            mode=mode,
            extra_context=extra_context,
            context_limit=context_limit,
        )
        prompt_tokens = self._estimate_text_tokens(system_prompt) + self._estimate_text_tokens(original_text) + 8
        completion_tokens = self._estimate_text_tokens(output_text)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated": True,
        }

    def _record_usage(self, usage, original_text, output_text, mode="primary", extra_context=None, context_limit=None):
        if not usage:
            usage = self._estimate_usage(
                original_text,
                output_text,
                mode=mode,
                extra_context=extra_context,
                context_limit=context_limit,
            )
        else:
            usage = dict(usage)
            usage.setdefault("estimated", False)

        with self._usage_lock:
            self.usage_stats["requests"] += 1
            self.usage_stats["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
            self.usage_stats["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
            self.usage_stats["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
            if usage.get("estimated"):
                self.usage_stats["estimated_tokens"] += int(usage.get("total_tokens", 0) or 0)
            current = self.usage_stats.copy()

        self.signals.stats_updated.emit(current)

    def _language_hint(self):
        lang = getattr(self, "_current_lang", None) or config.source_language or "ja"
        return str(lang).lower()

    def _pending_strategy(self):
        lang = self._language_hint()
        is_fast_lang = any(code in lang for code in ("ja", "jp", "zh", "cn"))
        if is_fast_lang:
            return {
                "soft_timeout": 0.75,
                "hard_timeout": 1.15,
                "soft_char_limit": 16,
                "hard_char_limit": 34,
                "chunk_limit": 2,
                "partial_min_chars": 8,
                "partial_min_interval": 0.45,
                "partial_max_tokens": 96,
                "partial_timeout": 4.5,
            }
        return {
            "soft_timeout": 0.95,
            "hard_timeout": 1.45,
            "soft_char_limit": 24,
            "hard_char_limit": 60,
            "chunk_limit": 2,
            "partial_min_chars": 12,
            "partial_min_interval": 0.6,
            "partial_max_tokens": 120,
            "partial_timeout": 5.0,
        }

    def _remember_translation(self, chunk_id, original_text, translated_text, mode):
        """Keep a rolling in-memory translation context for later corrections."""
        if not original_text or not translated_text or mode == "partial":
            return

        record = {
            "chunk_id": chunk_id,
            "mode": mode,
            "original": original_text.strip(),
            "translated": translated_text.strip(),
            "time": time.time(),
        }
        with self._runtime_memory_lock:
            self._runtime_translation_memory[chunk_id] = record
            while len(self._runtime_translation_memory) > 30:
                self._runtime_translation_memory.popitem(last=False)

        if mode == "correction":
            with self._correction_state_lock:
                self._correction_counts[chunk_id] = self._correction_counts.get(chunk_id, 0) + 1

    def _runtime_memory_context(self, limit=4):
        """Format recent runtime memory for correction prompts."""
        with self._runtime_memory_lock:
            items = list(self._runtime_translation_memory.values())[-limit:]

        lines = []
        for item in items:
            original = item.get("original", "").strip()
            translated = item.get("translated", "").strip()
            if original or translated:
                lines.append(f"原文: {original}\n译文: {translated}")
        return "\n".join(lines)

    def _correction_apply_delay(self, original_text, chunk_id):
        """Delay corrections longer for fast speech or long sentences."""
        lang = self._language_hint()
        is_fast_lang = any(code in lang for code in ("ja", "jp", "zh", "cn"))
        text_len = len(original_text or "")

        with self._runtime_memory_lock:
            record = self._runtime_translation_memory.get(chunk_id)
        age = 0.0
        if record:
            age = max(0.0, time.time() - record.get("time", time.time()))

        base = 2.6 if text_len < 60 else 3.4
        if is_fast_lang:
            base += 0.9
        if text_len >= 90:
            base += 0.8
        elif text_len >= 60:
            base += 0.4
        if age < 2.0:
            base += 0.8
        elif age < 4.0:
            base += 0.3

        with self._correction_state_lock:
            correction_count = self._correction_counts.get(chunk_id, 0)

        if correction_count >= 1:
            base += 1.0
        if correction_count >= 2:
            base += 1.5

        return min(base, 7.0)

    def _schedule_followup_corrections(self, current_chunk_id):
        """Revisit recent translations after a short delay so later context can refine them."""
        strategy = self._pending_strategy()

        with self._runtime_memory_lock:
            recent_ids = list(self._runtime_translation_memory.keys())[-3:]

        if current_chunk_id not in recent_ids:
            recent_ids.append(current_chunk_id)

        targets = []
        for cid in reversed(recent_ids[-3:]):
            targets.append(cid)

        for target_id in targets:
            with self._runtime_memory_lock:
                target_record = self._runtime_translation_memory.get(target_id)
            if not target_record:
                continue
            if not target_record.get("original") or not target_record.get("translated"):
                continue

            with self._correction_state_lock:
                if self._correction_counts.get(target_id, 0) >= 2:
                    continue
                if target_id in self._queued_correction_targets:
                    continue
                self._queued_correction_targets.add(target_id)

            def _enqueue_target(cid=target_id):
                try:
                    if not self.running:
                        return

                    with self._correction_state_lock:
                        self._queued_correction_targets.discard(cid)

                    with self._runtime_memory_lock:
                        record = self._runtime_translation_memory.get(cid)
                        if not record:
                            return
                        original = record.get("original", "").strip()
                    if not original:
                        return

                    self._enqueue_translation_job(
                        original,
                        [cid],
                        mode="correction",
                        reason="retro",
                        extra_context=self._runtime_memory_context(limit=4),
                        context_limit=4,
                        max_tokens=240 if len(original) < 60 else 320,
                        timeout=10.0,
                        temperature=0.07,
                        apply_delay=self._correction_apply_delay(original, cid),
                    )
                finally:
                    with self._correction_state_lock:
                        self._queued_correction_targets.discard(cid)

            delay = self._correction_apply_delay(target_record.get("original", ""), target_id)
            delay = min(max(delay, 1.8), 7.0)
            timer = threading.Timer(delay, _enqueue_target)
            timer.daemon = True
            timer.start()

    def _should_flush_pending(self, now):
        with self.pending_lock:
            if not self.pending_originals or self.pending_since <= 0:
                return False, None

            combined_text = "".join(self.pending_originals)
            age = now - self.pending_since
            strategy = self._pending_strategy()
            if age >= strategy["hard_timeout"]:
                return True, f"hard-timeout({age:.2f}s)"
            if len(combined_text) >= strategy["hard_char_limit"]:
                return True, f"hard-chars({len(combined_text)})"
            if (
                age >= strategy["soft_timeout"]
                and len(combined_text) >= strategy["soft_char_limit"]
            ):
                return True, f"soft-timeout({age:.2f}s)"
            return False, None

    def _drain_pending(self):
        with self.pending_lock:
            if not self.pending_originals:
                return None, None
            combined_text = "".join(self.pending_originals)
            ids_to_update = self.pending_ids.copy()
            self.pending_originals = []
            self.pending_ids = []
            self.pending_since = 0
            return combined_text, ids_to_update

    def _flush_pending_translation(self, reason=""):
        should_flush, flush_reason = self._should_flush_pending(time.time())
        if not should_flush:
            return False

        combined_text, ids_to_update = self._drain_pending()
        if not combined_text:
            return False

        print(
            f"[Pipeline] 强制刷新 pending ({flush_reason or reason}): "
            f"{len(ids_to_update)} chunks, {len(combined_text)} chars"
        )
        self._enqueue_translation_job(
            combined_text,
            ids_to_update,
            mode="primary",
            reason=flush_reason or reason,
        )
        if len(combined_text) >= self._pending_strategy()["soft_char_limit"] or len(ids_to_update) > 1:
            self._enqueue_translation_job(
                combined_text,
                ids_to_update,
                mode="correction",
                reason="history",
                extra_context=self._runtime_memory_context(limit=4),
                context_limit=3,
                max_tokens=240 if len(combined_text) < 60 else 320,
                timeout=10.0,
                temperature=0.08,
                apply_delay=self._correction_apply_delay(combined_text, ids_to_update[-1]),
            )
        return True

    def _enqueue_translation_job(
        self,
        original_text,
        chunk_ids,
        mode="primary",
        reason="",
        extra_context=None,
        context_limit=None,
        snapshot="",
        max_tokens=None,
        timeout=None,
        temperature=None,
        apply_delay=None,
        _skip_correction_guard=False,
    ):
        if not original_text or not chunk_ids:
            return

        if mode == "correction" and len(chunk_ids) == 1 and not _skip_correction_guard:
            target_id = chunk_ids[0]
            with self._correction_state_lock:
                if self._correction_counts.get(target_id, 0) >= 2:
                    return
                if target_id in self._queued_correction_targets:
                    return
                self._queued_correction_targets.add(target_id)

        if mode == "correction" and apply_delay and apply_delay > 0:
            delay = apply_delay

            def _delayed_enqueue():
                if not self.running:
                    if mode == "correction" and len(chunk_ids) == 1:
                        with self._correction_state_lock:
                            self._queued_correction_targets.discard(chunk_ids[0])
                    return
                self._enqueue_translation_job(
                    original_text,
                    chunk_ids,
                    mode=mode,
                    reason=reason,
                    extra_context=extra_context,
                    context_limit=context_limit,
                    snapshot=snapshot,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    temperature=temperature,
                    apply_delay=0.0,
                    _skip_correction_guard=True,
                )

            timer = threading.Timer(delay, _delayed_enqueue)
            timer.daemon = True
            timer.start()
            return

        priority_base = 0 if mode == "primary" else 100
        priority = priority_base
        if reason and reason.startswith("hard"):
            priority = -10 if mode == "primary" else 90

        if context_limit is None:
            context_limit = 0 if mode == "partial" else (1 if mode == "primary" else 3)

        if max_tokens is None:
            if mode == "partial":
                max_tokens = 96 if len(original_text) < 40 else 120
            elif mode == "primary":
                max_tokens = 160 if len(original_text) < 40 else 220
            else:
                max_tokens = 220
        if timeout is None:
            if mode == "partial":
                timeout = 4.5 if len(original_text) < 40 else 5.0
            elif mode == "primary":
                timeout = 6.0 if len(original_text) < 40 else 8.0
            else:
                timeout = 10.0
        if temperature is None:
            temperature = 0.05 if mode == "partial" else (0.1 if mode == "primary" else 0.08)
        if apply_delay is None:
            if mode == "correction":
                apply_delay = self._correction_apply_delay(original_text, chunk_ids[-1])
            elif mode == "partial":
                apply_delay = 0.0
            else:
                apply_delay = 0.0

        job = TranslationJob(
            priority=priority,
            created_at=time.time(),
            seq=self._next_translation_seq(),
            mode=mode,
            original_text=original_text,
            chunk_ids=tuple(chunk_ids),
            extra_context=extra_context or "",
            context_limit=context_limit,
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=temperature,
            snapshot=snapshot or original_text,
            apply_delay=apply_delay,
        )
        self.translation_jobs.put(job)

    def _maybe_enqueue_partial_translation(self, chunk_id, text, prompt=""):
        """Schedule an early draft translation so subtitles appear before the final chunk lands."""
        if not text or not self._is_meaningful(text):
            return

        strategy = self._pending_strategy()
        if len(text) < strategy["partial_min_chars"]:
            return

        now = time.time()
        with self._partial_translation_lock:
            state = self._partial_translation_state.get(chunk_id, {})
            last_text = state.get("text", "")
            last_time = state.get("time", 0.0)
            if text == last_text:
                return
            if now - last_time < strategy["partial_min_interval"]:
                return
            self._partial_translation_state[chunk_id] = {"text": text, "time": now}

        self._enqueue_translation_job(
            text,
            [chunk_id],
            mode="partial",
            reason="draft",
            extra_context=prompt,
            context_limit=0,
            max_tokens=strategy["partial_max_tokens"],
            timeout=strategy["partial_timeout"],
            temperature=0.05,
            snapshot=text,
        )

    def _translation_worker_loop(self):
        """Background lane that always processes the oldest pending translation first."""
        while self.running or not self.translation_jobs.empty():
            try:
                job = self.translation_jobs.get(timeout=0.2)
            except queue.Empty:
                continue

            if job is None:
                continue

            try:
                self._translate_and_update(
                    job.original_text,
                    list(job.chunk_ids),
                    mode=job.mode,
                    extra_context=job.extra_context,
                    context_limit=job.context_limit,
                    max_tokens=job.max_tokens,
                    timeout=job.timeout,
                    temperature=job.temperature,
                    snapshot=job.snapshot,
                    apply_delay=job.apply_delay,
                )
            except Exception as e:
                print(f"[Pipeline] Translation worker error: {e}")
            finally:
                if job.mode == "correction" and len(job.chunk_ids) == 1:
                    with self._correction_state_lock:
                        self._queued_correction_targets.discard(job.chunk_ids[0])

            self.translation_jobs.task_done()

    def _get_context_prompt(self):
        """统一的语境窗口截断逻辑"""
        if not self.last_final_text:
            return ""
            
        # Get language code with strong fallback to avoid NoneType errors
        src_lang = config.source_language
        lang_code = str(src_lang).lower() if src_lang else "ja"
        
        # 日语/中文：保留最后 100 字；其他语种：保留最后 400 字符
        max_ctx = 100 if any(code in lang_code for code in ['ja', 'jp', 'zh', 'cn']) else 400
        return self.last_final_text[-max_ctx:]

    def _is_meaningful(self, text):
        """Check if the text contains actual letters or characters, not just dots/punctuation."""
        if not text:
            return False
        # Remove all common punctuation and symbols
        # \w matches any alphanumeric character and underscores. 
        # For multi-language support (Chinese/Japanese), we just check if there's any non-punctuation character
        cleaned = re.sub(r'[^\w\s\u4e00-\u9fff\u3040-\u30ff]', '', text)
        return len(cleaned.strip()) > 0

    def _is_common_outro(self, text):
        """Filter very common closing phrases that often show up as outro noise."""
        if not text:
            return False

        compact = re.sub(r'[\s\W_]+', '', text).lower()
        if not compact:
            return False

        common_outros = (
            "感谢您的观看",
            "感谢观看",
            "谢谢您的观看",
            "谢谢观看",
            "谢谢大家观看",
            "ご視聴ありがとうございました",
            "ご視聴ありがとう",
            "ご視聴感謝",
            "thankyouforwatching",
            "thanksforwatching",
        )
        normalized = {re.sub(r'[\s\W_]+', '', phrase).lower() for phrase in common_outros}
        if compact in normalized:
            return True
        if len(compact) <= 18 and any(phrase in compact for phrase in normalized):
            return True
        return False

    def _process_partial_chunk(self, audio_data, chunk_id, prompt=""):
        """Transcribe and update UI (No translation)"""
        try:
            # Use accumulated context as prompt
            text = self.transcriber.transcribe(audio_data, prompt=prompt)
            if text and self._is_meaningful(text) and not self._is_common_outro(text):
                self.signals.update_text.emit(chunk_id, text, "")
                self._maybe_enqueue_partial_translation(chunk_id, text, prompt)
        except Exception as e:
            print(f"[Partial {chunk_id}] Error: {e}")

    def _process_final_chunk(self, audio_data, chunk_id, prompt=""):
        """Transcribe and accumulate text until a semantically complete unit is reached"""
        try:
            text = self.transcriber.transcribe(audio_data, prompt=prompt)
            
            if not self._is_meaningful(text):
                print(f"[Pipeline] Final chunk {chunk_id} ignored (no meaningful text)")
                return
            if self._is_common_outro(text):
                print(f"[Pipeline] Final chunk {chunk_id} ignored (common outro): {text}")
                return

            print(f"[Pipeline] Final #{chunk_id} (ASR): {text}")

            # Auto language detection: switch audio profile if language changed
            detected = self.transcriber.detected_language
            if detected and detected != getattr(self, '_current_lang', None):
                self._current_lang = detected
                print(f"[Pipeline] 🌐 Detected language: {detected}")
                if config.apply_language_profile(detected):
                    # Update audio capture thresholds live
                    self.audio.silence_threshold = config.silence_threshold
                    self.audio.silence_duration = config.silence_duration
                    self.audio.max_phrase_duration = config.max_phrase_duration

            # 1. Update UI with original text immediately (waiting for context)
            self.signals.update_text.emit(chunk_id, text, "(积攒语境中...)")

            # 2. Add to accumulation buffer
            with self.pending_lock:
                self.pending_originals.append(text)
                self.pending_ids.append(chunk_id)
                if not self.pending_since:
                    self.pending_since = time.time()  # mark when first item entered
            
            # 3. Use text as prompt for next ASR context
            self.last_final_text = text
            
            # 4. Check if we should trigger translation
            with self.pending_lock:
                combined_text = "".join(self.pending_originals)
                pending_count = len(self.pending_ids)
            
            # Trigger translation if:
            # - Sentence seems ended by punctuation (JP/CN/EN)
            # - OR accumulated text > 20 chars (shorter threshold for responsiveness)
            # - OR we have >= 2 pending fragments
            is_sentence_end = bool(re.search(r'[.!?。！？、…～\n]$', text.strip()))
            strategy = self._pending_strategy()
            is_too_long = len(combined_text) >= strategy["soft_char_limit"]
            is_too_many = pending_count >= strategy["chunk_limit"]
            
            should_translate = is_sentence_end or is_too_long or is_too_many
            
            if should_translate:
                print(f"[Pipeline] 触发翻译: end={is_sentence_end}, len={len(combined_text)}, n={len(self.pending_ids)}")
                text_to_translate, ids_to_update = self._drain_pending()
                if text_to_translate:
                    self._enqueue_translation_job(
                        text_to_translate,
                        ids_to_update,
                        mode="primary",
                        reason="boundary",
                        context_limit=1,
                    )
                    if len(text_to_translate) >= strategy["soft_char_limit"] or pending_count > 1 or not is_sentence_end:
                        self._enqueue_translation_job(
                            text_to_translate,
                            ids_to_update,
                            mode="correction",
                            reason="history",
                            extra_context=self._runtime_memory_context(limit=4),
                            context_limit=3,
                            max_tokens=240 if len(text_to_translate) < 60 else 320,
                            timeout=10.0,
                            temperature=0.08,
                        )
            else:
                print(f"[Pipeline] 继续积累语境... ({len(combined_text)} chars, {len(self.pending_ids)} chunks)")

        except Exception as e:
            print(f"[Pipeline] Error in final chunk processing: {e}")

    def _translate_and_update(
        self,
        original_text,
        chunk_ids,
        mode="primary",
        extra_context=None,
        context_limit=None,
        max_tokens=None,
        timeout=None,
        temperature=None,
        snapshot="",
        apply_delay=0.0,
    ):
        """Translate accumulated text and update UI items"""
        accumulated = ""
        last_id = chunk_ids[-1]

        # Mark intermediate chunks visually
        if len(chunk_ids) > 1:
            for cid in chunk_ids[:-1]:
                self.signals.update_text.emit(cid, "", "(见下文连读)")

        # Run streaming translation on the last chunk ID
        try:
            t_last_emit = 0
            last_emit_len = 0
            for token in self.translator.stream_translate(
                original_text,
                mode=mode,
                extra_context=extra_context,
                context_limit=context_limit,
                max_tokens=max_tokens,
                timeout=timeout,
                temperature=temperature,
            ):
                if not self.running:
                    break
                if mode == "partial" and snapshot:
                    with self._partial_translation_lock:
                        current = self._partial_translation_state.get(last_id, {}).get("text", "")
                    if current != snapshot:
                        return
                accumulated += token
                
                # Throttle UI updates more aggressively for long sentences to avoid flicker.
                now = time.time()
                text_len = len(original_text or "")
                if mode == "partial":
                    emit_gap = 0.06 if text_len < 40 else 0.12
                    min_delta = 4 if text_len < 40 else 6
                elif mode == "correction":
                    emit_gap = 0.18 if text_len < 60 else 0.26
                    min_delta = 6 if text_len < 60 else 10
                else:
                    emit_gap = 0.08 if text_len < 60 else 0.18
                    min_delta = 5 if text_len < 60 else 8

                should_emit = False
                if not t_last_emit:
                    should_emit = True
                elif now - t_last_emit >= emit_gap and len(accumulated) - last_emit_len >= min_delta:
                    should_emit = True
                elif now - t_last_emit >= emit_gap * 1.5 and accumulated[-1:] in "。！？!?、,，；;\n":
                    should_emit = True

                if should_emit:
                    self.signals.update_text.emit(last_id, original_text, accumulated)
                    t_last_emit = now
                    last_emit_len = len(accumulated)
                
            if accumulated:
                # One final emit to show the complete result
                self.signals.update_text.emit(last_id, original_text, accumulated)
                self._record_usage(
                    getattr(self.translator, "last_usage", None),
                    original_text,
                    accumulated,
                    mode=mode,
                    extra_context=extra_context,
                    context_limit=context_limit,
                )
                self._remember_translation(last_id, original_text, accumulated, mode)

                # Log to history for file export
                if mode == "primary":
                    record = f"【原声】：{original_text}\n【译文】：{accumulated}\n"
                    self.translated_history.append(record)
                    self._schedule_followup_corrections(last_id)
                
        except Exception as e:
            print(f"[Pipeline] Translation error for chunk {last_id}: {e}")
            self.signals.update_text.emit(last_id, original_text, f"[翻译错误: {e}]")
    
    def _transcribe_chunk(self, transcriber, audio_chunk, chunk_id):
        """Transcribe a single chunk and log timing"""
        t0 = time.time()
        text = transcriber.transcribe(audio_chunk)
        t1 = time.time()
        print(f"[Chunk {chunk_id}] Transcribed in {t1-t0:.2f}s: {text if text else '(empty)'}")
        return text
    
    def _translate_and_log(self, text, chunk_id=0):
        """Translate text and log result"""
        t0 = time.time()
        translated_text = self.translator.translate(text)
        t1 = time.time()
        print(f"[Chunk {chunk_id}] Translated in {t1-t0:.2f}s: {translated_text}")
        return (text, translated_text)

    def _on_manual_save_requested(self, transcript_data):
        """Triggered when user clicks 'Save' in the UI overlay"""
        print(f"[Pipeline] Save requested from UI: {len(transcript_data)} chunks")
        self.save_status.emit("保存中...")
        # Sort and prepare data - saving tokens by not sending originals to AI
        sorted_ids = sorted(transcript_data.keys())
        
        # 1. Prepare input for AI refinement (only translated/calibrated text)
        refine_items = []
        # 2. Prepare raw log for local storage (bilingual comparison)
        raw_log_items = []
        
        for cid in sorted_ids:
            data = transcript_data[cid]
            ori = data.get('original', '').strip()
            tra = data.get('translated', '').strip()
            
            if tra:
                refine_items.append(tra)
            elif ori:
                refine_items.append(ori) # Fallback if no translation
                
            if ori or tra:
                raw_log_items.append(f"【原声】: {ori}\n【译文】: {tra}")
        
        full_text_for_ai = "\n".join(refine_items)
        bilingual_appendix = "\n\n---\n\n".join(raw_log_items)
        
        # Run the refinement and save process in a separate thread
        threading.Thread(
            target=self._perform_refine_and_save, 
            args=(full_text_for_ai, bilingual_appendix), 
            daemon=True
        ).start()

    def _finalize_transcripts(self):
        """Join history for automatic final save on exit (basic history)"""
        if not self.translated_history:
            return
        full_text = "\n".join(self.translated_history)
        # On auto-exit, we might not have the full data dict easily, 
        # so we just save the translated history for now.
        self._perform_refine_and_save(full_text, "")

    def _perform_refine_and_save(self, full_text, appendix=""):
        """The core logic to refine with AI and save to disk"""
        if not full_text.strip():
            print("[Pipeline] No text content to refine.")
            self.save_result.emit(False, "", "", "没有可保存的内容。")
            return

        # Run AI Refinement (returns title and content)
        title, refined_text = self.translator.refine_document(full_text)
        
        # 1. Sanitize the title for filename safety
        if not title:
            title = f"transcript_{time.strftime('%H%M%S')}"
        else:
            # Remove invalid filename characters
            import re
            title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
            if not title:
                title = f"transcript_{time.strftime('%H%M%S')}"
            
        # 2. Use Absolute Path for consistency
        save_dir = os.path.abspath(os.path.expanduser(config.transcript_save_dir))
        os.makedirs(save_dir, exist_ok=True)
        
        final_path = os.path.join(save_dir, f"{title}.md")
        
        try:
            with open(final_path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(refined_text)
                
                if appendix:
                    f.write("\n\n---\n## 原始双语/校对对照记录 (Bilingual Log)\n\n")
                    f.write(appendix)
                    
            print(f"[Pipeline] Successfully saved refined document to: {final_path}")
            self.save_result.emit(True, title, final_path, save_dir)
        except Exception as e:
            # Log clearly for debugging
            print(f"[Pipeline Error] Failed to write file to {final_path}: {e}")
            self.save_result.emit(False, title, final_path, str(e))

# Global reference for signal handler
_pipeline = None
_app = None
_save_result_notifier = None

def signal_handler(sig, frame):
    """Handle Ctrl-C gracefully"""
    print("\n[Main] Ctrl-C received, force killing...")
    os._exit(0)

def start_overlay_session():
    """Start the overlay and pipeline without blocking (for use in Dashboard)"""
    global _pipeline, _app, _save_result_notifier
    
    # Initialize Overlay Window
    window = OverlayWindow(
        display_duration=config.display_duration,
        window_width=config.window_width,
        model_name=config.model
    )
    window.show()
    
    # Logic
    _pipeline = Pipeline()
    _save_result_notifier = SaveResultNotifier(window)

    # Connect signals
    from PyQt6.QtCore import Qt
    _pipeline.signals.update_text.connect(window.update_text)
    window.save_requested.connect(_pipeline._on_manual_save_requested, Qt.ConnectionType.DirectConnection)
    window.stop_requested.connect(_pipeline.stop, Qt.ConnectionType.DirectConnection)
    _pipeline.save_result.connect(_save_result_notifier.show_result)
    _pipeline.save_status.connect(_save_result_notifier.show_progress)
    _pipeline.save_status.connect(window.set_save_status)
    _pipeline.save_result.connect(window.finish_save_status)
    
    # Start pipeline
    _pipeline.start()
    
    return window, _pipeline

def main():
    multiprocessing.freeze_support()
    global _pipeline, _app
    
    # Set up signal handler for Ctrl-C
    signal.signal(signal.SIGINT, signal_handler)
    
    _app = QApplication.instance()
    if not _app:
        _app = QApplication(sys.argv)
    
    # Start session
    win, pipe = start_overlay_session()
    
    # Timer to let Python interpreter handle signals (Ctrl-C)
    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)
    
    try:
        sys.exit(_app.exec())
    except SystemExit:
        pass
    finally:
        if _pipeline:
            _pipeline.stop()

if __name__ == "__main__":
    main()
