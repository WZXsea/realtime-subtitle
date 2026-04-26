# 译世界

**v3.0.0**

这是一个面向 macOS 的实时语音转文字与翻译软件，重点优化了实时字幕的流畅度、稳定性和可用性。

这一版的重点包括：
- 启动提示更清晰
- 悬浮窗堆叠更稳定
- 保存流程更顺滑
- 增加 token 统计
- 支持自动检查更新
- 优化长句与快语速场景下的翻译策略

## 项目特点

- **实时转写**：支持 `faster-whisper`、`mlx-whisper`、`FunASR`
- **多引擎后端**：可在 Whisper、MLX、FunASR 之间切换
- **流式字幕**：支持边说边出字，减少等待感
- **后台翻译**：翻译在后台异步进行，不阻塞界面
- **悬浮窗显示**：可置顶、透明、点击穿透，适合边看视频边使用
- **保存日志**：可将当前会话整理并导出
- **自动检查更新**：可配置自己的 GitHub Releases 仓库，自动发现新版本

## 截图与演示

演示视频：

https://github.com/user-attachments/assets/9982fe5d-3937-42d5-bcfc-e23748c01edf

主界面示意：

![Dashboard](./demo/main_dashboard.png)

## 安装

### 1. 环境要求

- Python 3.10+
- macOS
- `ffmpeg`
- `BlackHole` 虚拟声卡

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. macOS 相关说明

- 建议使用 `BlackHole 2ch`
- 如果需要监听系统声音，请在“音频 MIDI 设置”里正确配置输入输出设备

## 启动方式

### 开发模式

- macOS / Linux：`./start_mac.sh`
- Windows：`start_windows.bat`

### 打包版本

双击 `译世界.app` 或者挂载 `译世界_v3.0.0.dmg` 后拖入 `Applications`

## 使用说明

### 首页

- 点击“启动翻译”即可开始
- 可以暂停、继续、停止
- 可在此检查更新

### 音频设置

- 选择系统声音输入设备
- 调整静音阈值、静音时长、采样率等参数

### 语音识别

- 可选 Whisper / MLX / FunASR
- Whisper 适合通用场景
- MLX 更适合 Apple Silicon
- FunASR 更适合中文场景

### 翻译设置

- 填写 API 地址、API Key、模型名
- 选择目标语言
- 支持保存常用服务商配置

### 保存位置

- 设置字幕日志保存路径
- 支持打开目录

### 更新

- 填写自己的 GitHub 仓库名，例如：`WZXsea/transworld`
- 可启用启动时自动检查更新
- 发现新版本后可自动下载 DMG，用户自行完成拖拽安装

## 配置说明

软件会把设置保存到 `config.ini`。

### `[api]`

| 参数 | 说明 |
| :--- | :--- |
| `active_provider` | 当前使用的服务商 |

### `[api.xxx]`

| 参数 | 说明 |
| :--- | :--- |
| `api_key` | 对应服务商的密钥 |
| `base_url` | 接口地址 |
| `model` | 模型名称 |

### `[translation]`

| 参数 | 说明 |
| :--- | :--- |
| `model` | 主要翻译模型 |
| `target_lang` | 目标语言 |
| `threads` | 并发数 |

### `[transcription]`

| 参数 | 说明 |
| :--- | :--- |
| `backend` | 识别引擎 |
| `whisper_model` | Whisper 模型 |
| `funasr_model` | FunASR 模型 |
| `device` | 推理设备 |

### `[audio]`

| 参数 | 说明 |
| :--- | :--- |
| `device_index` | 输入设备编号 |
| `sample_rate` | 采样率 |
| `silence_threshold` | 静音阈值 |
| `silence_duration` | 静音时长 |
| `streaming_mode` | 是否启用流式识别 |

### `[updates]`

| 参数 | 说明 |
| :--- | :--- |
| `repo` | 用于检查更新的 GitHub 仓库 |
| `auto_check_updates` | 启动时是否自动检查更新 |

## 常见问题

- **为什么安装后不需要重新配置？**  
  因为程序优先读取你本机的 `~/Library/Application Support/RealtimeSubtitle/config.ini`。

- **为什么自动更新不会碰到我的翻译 API Key？**  
  因为更新功能只访问 GitHub Releases，不会读取或上传翻译服务的 API Key。

- **为什么有时需要拖一下窗口才刷出来？**  
  这是窗口布局刷新问题，程序已经在持续优化中。

## 许可证

本项目基于 MIT License。

- 原始项目作者：Van
- 当前二次优化与发布：WZXsea（同样采用 MIT）

请在继续分发、修改或再发布时，保留原作者与当前维护者的 MIT 版权与许可声明。

原始 MIT 许可文本见 [LICENSE](./LICENSE)。
