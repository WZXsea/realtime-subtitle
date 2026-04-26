#!/bin/bash
# 译世界 - 强力打包脚本 (macOS)
# 解决环境冲突、权限锁定以及递归启动 Bug

# 1. 自动进入脚本所在的文件夹 (项目根目录)
cd "$(dirname "$0")"
PROJECT_ROOT=$(pwd)

echo "--------------------------------------------------------"
echo "正在初始化强力打包流程..."

# 2. 强制激活本地虚拟环境 (3.9 环境最稳定)
if [ -d "./.venv" ]; then
    echo "激活本地虚拟环境 (.venv)..."
    source ./.venv/bin/activate
else
    echo "错误: 未检测到 .venv。请确保在项目根目录下存在 Python 3.9 的虚拟环境。"
    exit 1
fi

# 3. 彻底清理缓存与残留 (解决 PermissionError)
echo "正在执行地毯式清理..."
rm -rf build dist *.spec_repo

# 尝试清理 PyInstaller 的系统级别缓存 (index.dat 经常出现在这里)
PYI_STORAGE="$HOME/Library/Application Support/pyinstaller"
if [ -d "$PYI_STORAGE" ]; then
    echo "发现 PyInstaller 系统缓存，正在清理以防止权限锁定..."
    rm -rf "$PYI_STORAGE"/* 2>/dev/null
fi

# 4. 检查并确保核心打包依赖
echo "检查并补全关键依赖..."
# 强制根据最新的 requirements.txt 安装，确保 certifi, hf_xet 等全部入场
python -m pip install -U pyinstaller
python -m pip install -r requirements.txt

# 5. 执行打包命令
echo "正在开始正式构建 (入口: dashboard.py)..."
# 使用 --clean 确保没有任何旧逻辑残留
pyinstaller --clean \
    --noconfirm \
    --log-level=INFO \
    realtime_subtitle.spec

# 6. 结果反馈
if [ $? -eq 0 ]; then
    echo "--------------------------------------------------------"
    echo "恭喜！'译世界' 打包成功！"
    echo "App 路径: $PROJECT_ROOT/dist/译世界.app"
    echo "--------------------------------------------------------"
    
    if [ -f "./make_dmg.sh" ]; then
        echo "提示：您可以接着运行 'sh make_dmg.sh' 来生成最终的磁盘镜像。"
    fi
else
    echo "--------------------------------------------------------"
    echo "打包过程中出现错误。"
    echo "建议处理: 如果仍然出现 'Operation not permitted', 请前往"
    echo "   '系统设置 -> 隐私与安全性 -> 完全磁盘访问权限', 给您的终端加上权限后再试。"
    echo "--------------------------------------------------------"
    exit 1
fi
