#!/bin/bash
# 实时字幕翻译 - 生产环境打包脚本 (macOS)
# 请在您已激活的 Conda/虚拟环境中运行此脚本

# 1. 自动进入脚本所在的文件夹 (项目根目录)
cd "$(dirname "$0")"

# 1.5 强制激活本地虚拟环境 (如果存在)
if [ -d "./.venv" ]; then
    echo "🐍 检测到本地虚拟环境，正在激活..."
    source ./.venv/bin/activate
else
    echo "⚠️ 未检测到 .venv，将尝试使用当前 Shell 环境..."
fi

echo "--------------------------------------------------------"
echo "🔍 检查打包环境..."
echo "Python: $(python --version)"
echo "From: $(which python)"

# 2. 补全核心依赖
echo "📦 正在检查并补全关键依赖..."
python -m pip install -U pyinstaller requests httpx httpcore configparser tqdm huggingface_hub

# 3. 清理旧的构建残留
echo "🧹 正在清理旧的构建残留..."
rm -rf build dist

# 4. 执行正式打包
echo "🏗️ 正在使用 Spec 文件进行正式打包 (v2.0.0)..."
# 注意：使用 --clean 确保不使用旧缓存
pyinstaller --clean realtime_subtitle.spec

if [ $? -eq 0 ]; then
    echo "--------------------------------------------------------"
    echo "✅ 恭喜！实时字幕翻译 v2.0.0.app 打包成功！"
    echo "📂 输出目录: $(pwd)/dist/实时字幕翻译.app"
    echo "--------------------------------------------------------"
    
    # 5. 自动引导下一步
    if [ -f "./make_dmg.sh" ]; then
        echo "💡 提示：运行 sh ./make_dmg.sh 即可生成最终的 DMG 安装包。"
    fi
else
    echo "❌ 打包失败，请检查上方报错信息。"
    exit 1
fi
