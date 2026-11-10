# 绿梦切服

《绿梦：时空之声》Windows PC 渠道切换工具。一份游戏目录可以在官服和哔哩哔哩服之间切换，已有的大型热更新资源可以继续共用。登录由所选渠道的游戏启动器处理。

**[下载 Windows x64 版（ZIP）](https://github.com/Stephendafen/LuminChannelSwitch/releases/latest/download/LuminChannelSwitch-Windows-x64.zip)** · [版本记录](https://github.com/Stephendafen/LuminChannelSwitch/releases)

## 第一次使用

1. 完整解压 ZIP，打开文件夹里的 `LuminChannelSwitch.exe`。Windows x64 发行包已内置 Python 运行环境。
2. 点「选择目录」，选择自己的绿梦 PC 官服安装目录。游戏版号须为 **1.0.2，公测构建 12450**。本版按发行方的文件指纹识别 `zhuzi` 和 `zhuzi_gf` 两种官服基础包。首次配置从受支持的官服安装开始。
3. 点「准备 B 服组件」。首次使用需要联网下载数百 MB 的发行方组件和 B 服启动器。原版游戏启动器使用 [.NET 8 桌面运行时（Windows x64）](https://dotnet.microsoft.com/zh-cn/download/dotnet/8.0)。
4. 退出游戏和托盘里的游戏启动器，点「切换到 B 服」。显示 B 服后，点「启动当前渠道」并用 B 站账号登录。回官服时按相同步骤点「切换到官服」。

切换前会核对当前版本并保存原文件和本机渠道配置。遇到上次操作中断，可点「恢复上次操作」。遇到游戏本体更新，建议先切回原官服，再用原启动器更新。新构建需要匹配的新清单。

首发版在两种官服的临时副本中验证了切换、回切和中断恢复。B 服的 183 个基础文件、回切后的 109 个或 112 个官服基础文件均通过 SHA-256 比对。验证范围到文件与启动器配置，实际账号登录由使用者完成。

## 更新与转发

点「检查更新」可查看本项目的最新正式版。下载成功后，工具会验证压缩包的 SHA-256。退出旧工具，解压新版，运行新文件夹中的程序。工具数据保存在当前 Windows 用户的数据目录，与程序文件夹分开。

发到 QQ 群时可直接转发 Releases 里的整个 ZIP。压缩包中附有中文使用说明，群友首次准备时从发行方获取所需游戏组件。

## 构建发行包

在 Windows x64 上安装 Python 3.11 或更高版本和支持 .NET 8 的 SDK，在项目根目录运行：

```powershell
python -m pip install -r requirements-build.txt
python tools/build_release.py
```

`release/` 中会生成 Windows 便携包以及 SHA-256 校验文件。发布新版时更新 `src/version.py` 的版本号，使用对应的 `vX.Y.Z` 标签创建 GitHub 正式 Release，并上传生成的 ZIP 和同名 `.sha256` 文件。固定资产名供旧版工具查找更新。

这是玩家制作的独立工具。游戏及渠道组件由相应发行方提供。
