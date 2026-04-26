import os
import shutil
import threading
from pathlib import Path
from huggingface_hub import scan_cache_dir, snapshot_download

class ModelManager:
    # Supported models configuration
    # Backend -> List of (display_name, repo_id, size_label)
    MODELS = {
        "mlx": [
            ("Whisper Tiny (MLX)", "mlx-community/whisper-tiny-mlx", "tiny"),
            ("Whisper Base (MLX)", "mlx-community/whisper-base-mlx", "base"),
            ("Whisper Small (MLX)", "mlx-community/whisper-small-mlx", "small"),
            ("Whisper Medium (MLX)", "mlx-community/whisper-medium-mlx", "medium"),
            ("Whisper Large-v3 (MLX)", "mlx-community/whisper-large-v3-mlx", "large-v3"),
            ("Whisper Turbo (MLX)", "mlx-community/whisper-turbo-mlx", "turbo"),
        ],
        "whisper": [
            ("Whisper Tiny (v2)", "Systran/faster-whisper-tiny", "tiny"),
            ("Whisper Base (v2)", "Systran/faster-whisper-base", "base"),
            ("Whisper Small (v2)", "Systran/faster-whisper-small", "small"),
            ("Whisper Medium (v2)", "Systran/faster-whisper-medium", "medium"),
            ("Whisper Large-v3", "Systran/faster-whisper-large-v3", "large-v3"),
        ],
        "funasr": [
            ("Paraformer-large", "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "paraformer"),
            ("SenseVoice Small", "FunASR/SenseVoiceSmall", "sensevoice"),
        ]
    }

    @staticmethod
    def get_all_supported_models():
        """Returns a flat list of all supported model objects"""
        flat_list = []
        for backend, models in ModelManager.MODELS.items():
            for name, repo, size in models:
                flat_list.append({
                    "backend": backend,
                    "name": name,
                    "repo_id": repo,
                    "size_key": size
                })
        return flat_list

    @staticmethod
    def scan_local_models():
        """
        全方位扫描 HF 缓存并返回已下载仓库的状态。
        1. 使用官方 scan_cache_dir 获取精确元数据。
        2. 通过文件系统遍历 models--* 文件夹防止漏扫。
        """
        results = {}
        
        # 1. 尝试官方扫描
        try:
            cache_info = scan_cache_dir()
            for repo in cache_info.repos:
                size_on_disk = getattr(repo, 'size_on_disk', 0)
                if size_on_disk == 0:
                    # 某些版本不直接提供 size_on_disk
                    for r in getattr(repo, 'revisions', []):
                         size_on_disk += getattr(r, 'size_on_disk', 0)
                
                results[repo.repo_id] = {
                    "is_downloaded": True,
                    "size_bytes": size_on_disk,
                    "path": str(repo.repo_path)
                }
        except Exception as e:
            print(f"[ModelManager] scan_cache_dir fallback triggered: {e}")

        # 2. 深度文件夹扫描覆盖（解决“下好了但不显示”的痛点）
        try:
            home = str(Path.home())
            hf_hub_base = os.path.join(home, ".cache", "huggingface", "hub")
            if os.path.isdir(hf_hub_base):
                for item in os.listdir(hf_hub_base):
                    if item.startswith("models--"):
                        # 解析 repo_id (models--openai--whisper-small -> openai/whisper-small)
                        parts = item.replace("models--", "").split("--")
                        if len(parts) >= 2:
                            repo_id = "/".join(parts)
                            if repo_id not in results:
                                repo_path = os.path.join(hf_hub_base, item)
                                # 计算总大小
                                total_size = 0
                                snapshots_dir = os.path.join(repo_path, "snapshots")
                                if os.path.isdir(snapshots_dir):
                                    for root, dirs, files in os.walk(snapshots_dir):
                                        for f in files:
                                            try:
                                                total_size += os.path.getsize(os.path.join(root, f))
                                            except: pass
                                
                                if total_size > 1024 * 1024: # 只有大于 1MB 的才算有效模型
                                    results[repo_id] = {
                                        "is_downloaded": True,
                                        "size_bytes": total_size,
                                        "path": repo_path
                                    }
        except Exception as e:
            print(f"[ModelManager] Fallback folder scan error: {e}")
            
        return results

    @staticmethod
    def delete_model(repo_id):
        """Physically deletes the model from HF cache"""
        try:
            # 1. Try official scan first
            cache_info = scan_cache_dir()
            for repo in cache_info.repos:
                if repo.repo_id == repo_id:
                    print(f"[ModelManager] Deleting {repo_id} at {repo.repo_path}")
                    shutil.rmtree(repo.repo_path, ignore_errors=True)
                    return True
            
            # 2. Fallback manual deletion (matches fallback scan logic)
            home = str(Path.home())
            hf_hub_base = os.path.join(home, ".cache", "huggingface", "hub")
            # Escape repo_id: "owner/repo" -> "models--owner--repo"
            escaped_repo = "models--" + repo_id.replace("/", "--")
            repo_path = os.path.join(hf_hub_base, escaped_repo)
            
            if os.path.isdir(repo_path):
                print(f"[ModelManager] Deleting {repo_id} via fallback path: {repo_path}")
                shutil.rmtree(repo_path, ignore_errors=True)
                return True
                
            return False
        except Exception as e:
            print(f"[ModelManager] Delete error for {repo_id}: {e}")
            return False

    @staticmethod
    def get_disk_usage_str(size_bytes):
        """Converts bytes to human readable string"""
        if size_bytes <= 0: return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

class DownloadCanceledError(Exception):
    """自定义异常，用于由用户取消下载任务"""
    pass

class GlobalUI_Tqdm:
    """
    一个完整的 tqdm 兼容类，用于将进度条重定向到全局回调函数中。
    适配 huggingface_hub 各个版本的内部调用。
    """
    callback = None # 格式: function(percent, message)
    _is_canceled = False # 取消标志

    @classmethod
    def cancel_all(cls):
        cls._is_canceled = True

    @classmethod
    def reset_cancellation(cls):
        cls._is_canceled = False

    def __init__(self, iterable=None, desc=None, total=None, unit='it', unit_scale=False, **kwargs):
        # 即使在开始新进度条时也要检查取消标志
        if GlobalUI_Tqdm._is_canceled:
            raise DownloadCanceledError("Cancelled during init")

        self.desc = desc or "正在下载"
        self.total = total
        self.n = 0
        self.last_pct = -1
        self.unit = unit
        
        # 为了计算速度
        import time
        self.start_time = time.time()
        
        if GlobalUI_Tqdm.callback:
            msg = f"{self.desc}: 开始..."
            GlobalUI_Tqdm.callback(0, msg)

    def update(self, n=1):
        # 核心：检查是否取消，抛出异常中止 snapshot_download
        if GlobalUI_Tqdm._is_canceled:
            print(f"[GlobalUI_Tqdm] Raising DownloadCanceledError for {self.desc}")
            raise DownloadCanceledError("User canceled the download.")

        self.n += n
        import time
        now = time.time()
        elapsed = now - self.start_time
        speed_str = ""
        if elapsed > 0:
            speed = self.n / elapsed
            speed_str = ModelManager.get_disk_usage_str(speed) + "/s"

        if GlobalUI_Tqdm.callback and self.total and self.total > 0:
            pct = int((self.n / self.total) * 100)
            if pct > self.last_pct or elapsed > 0.5:
                self.last_pct = pct
                msg = f"{self.desc}: {pct}%|{speed_str}"
                GlobalUI_Tqdm.callback(pct, msg)
        elif GlobalUI_Tqdm.callback:
            # 如果没有总大小，显示已下载量
            msg = f"{self.desc}: 已下载 {ModelManager.get_disk_usage_str(self.n)}|{speed_str}"
            GlobalUI_Tqdm.callback(-1, msg)

    def set_description(self, desc, refresh=True):
        self.desc = desc
        if GlobalUI_Tqdm.callback:
            GlobalUI_Tqdm.callback(-1, self.desc)

    @classmethod
    def get_lock(cls):
        # 返回一个简单的 RLock 模拟，防止 huggingface_hub 在实例化前调用时报错
        import threading
        if not hasattr(cls, '_lock'):
            cls._lock = threading.RLock()
        return cls._lock

    @classmethod
    def set_lock(cls, lock): pass
    
    def set_postfix(self, *args, **kwargs): pass
    def display(self, *args, **kwargs): pass
    def clear(self, *args, **kwargs): pass
    def refresh(self, *args, **kwargs): pass
    
    def close(self):
        if GlobalUI_Tqdm.callback:
            GlobalUI_Tqdm.callback(100, f"{self.desc}: 完成")

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
    def __iter__(self):
        if hasattr(self, 'iterable') and self.iterable is not None:
            for obj in self.iterable:
                yield obj
                self.update(1)
        else:
            return iter([])
