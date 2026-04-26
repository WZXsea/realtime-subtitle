import re
import webbrowser
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

from config import APP_VERSION


@dataclass
class UpdateCheckResult:
    repo: str
    current_version: str
    latest_version: str = ""
    release_name: str = ""
    release_url: str = ""
    download_url: str = ""
    download_name: str = ""
    download_size: int = 0
    published_at: str = ""
    body: str = ""
    has_update: bool = False
    error: str = ""
    status_code: Optional[int] = None


@dataclass
class DownloadResult:
    success: bool
    repo: str
    version: str
    file_path: str = ""
    error: str = ""


def normalize_version_tag(tag: str) -> str:
    if not tag:
        return ""
    tag = tag.strip()
    match = re.search(r"(\d+(?:\.\d+)*)", tag)
    if match:
        return match.group(1)
    return tag.lstrip("vV")


def _version_key(version: str) -> Tuple[Tuple[int, ...], str]:
    normalized = normalize_version_tag(version)
    if not normalized:
        return (), ""

    main, _, suffix = normalized.partition("-")
    parts = []
    for segment in main.split("."):
        m = re.match(r"(\d+)", segment)
        parts.append(int(m.group(1)) if m else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts), suffix.lower()


def compare_versions(latest: str, current: str) -> int:
    """Return 1 if latest > current, 0 if equal, -1 if latest < current."""
    latest_key = _version_key(latest)
    current_key = _version_key(current)

    if latest_key[0] != current_key[0]:
        return 1 if latest_key[0] > current_key[0] else -1

    latest_suffix = latest_key[1]
    current_suffix = current_key[1]
    if latest_suffix == current_suffix:
        return 0
    if not latest_suffix and current_suffix:
        return 1
    if latest_suffix and not current_suffix:
        return -1
    return 1 if latest_suffix > current_suffix else -1


class GitHubReleaseChecker:
    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout

    def check_latest(self, repo: str, current_version: str = APP_VERSION) -> UpdateCheckResult:
        repo = (repo or "").strip()
        result = UpdateCheckResult(repo=repo, current_version=current_version)

        if not repo:
            result.error = "未设置更新仓库。"
            return result

        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"RealtimeSubtitle/{current_version}",
        }

        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
            result.status_code = response.status_code
            if response.status_code == 404:
                result.error = "仓库不存在或还没有发布 Release。"
                return result
            if response.status_code == 403:
                result.error = "GitHub 访问受限或触发了频率限制。"
                return result
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            result.error = f"检查更新失败：{exc}"
            return result
        except ValueError as exc:
            result.error = f"更新响应解析失败：{exc}"
            return result

        latest_tag = str(data.get("tag_name") or data.get("name") or "").strip()
        result.latest_version = normalize_version_tag(latest_tag)
        result.release_name = str(data.get("name") or latest_tag or "GitHub Release")
        result.release_url = str(
            data.get("html_url")
            or f"https://github.com/{repo}/releases/tag/{latest_tag.lstrip('vV')}"
        )
        result.published_at = str(data.get("published_at") or "")
        result.body = str(data.get("body") or "")

        asset = self._pick_download_asset(data.get("assets") or [])
        if asset:
            result.download_name = str(asset.get("name") or "")
            result.download_url = str(asset.get("browser_download_url") or "")
            try:
                result.download_size = int(asset.get("size") or 0)
            except (TypeError, ValueError):
                result.download_size = 0

        if not result.latest_version:
            result.error = "Release 未提供可识别的版本号。"
            return result

        if result.has_update is False and compare_versions(result.latest_version, current_version) <= 0:
            # Keep asset info available for manual download anyway.
            pass

        result.has_update = compare_versions(result.latest_version, current_version) > 0
        if result.has_update and not result.download_url:
            result.error = "发现新版本，但该 Release 没有可下载的安装包。"
        return result

    def _pick_download_asset(self, assets):
        if not assets:
            return None

        def score(asset):
            name = str(asset.get("name") or "").lower()
            if name.endswith(".dmg"):
                return 0
            if name.endswith(".zip"):
                return 1
            if name.endswith(".pkg"):
                return 2
            return 9

        return sorted(assets, key=score)[0]


def download_release_asset(download_url: str, target_dir: str, filename: str, timeout: float = 30.0, progress_callback=None):
    target_dir = Path(target_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = filename or Path(download_url).name or "update.dmg"
    target_path = target_dir / safe_name

    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": f"RealtimeSubtitle/{APP_VERSION}",
    }

    try:
        with requests.get(download_url, headers=headers, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            downloaded = 0
            chunk_size = 1024 * 1024

            with open(target_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        pct = int(downloaded * 100 / total)
                        progress_callback(pct, f"正在下载更新... {pct}%")
                    elif progress_callback:
                        progress_callback(-1, f"正在下载更新... {downloaded // (1024 * 1024)}MB")

        return DownloadResult(True, "", "", str(target_path))
    except Exception as exc:
        return DownloadResult(False, "", "", "", str(exc))


def open_release_page(url: str):
    if url:
        webbrowser.open(url)
