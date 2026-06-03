# Recursive Unzip Tool

一个面向桌面的递归解压工具。选择目标目录后，先扫描目录中的压缩包，再勾选需要处理的文件并执行解压。

## 功能

- PySide6 图形界面，采用类似 FreeFileSync 的两阶段工作流。
- 递归扫描目标目录和子目录。
- 支持 `.zip`、`.7z`、`.tar`、`.tar.gz`、`.tgz`、`.tar.bz2`、`.tbz2`、`.tar.xz`、`.txz`。
- 扫描完成后以树形视图显示文件，压缩包默认勾选。
- 文件列表显示压缩包大小和预计解压后大小。
- 右键文件可打开、打开文件位置或查看详细属性。
- 可选显示所有文件；普通文件只用于浏览，不会被执行。
- 可配置扫描深度、文件类型过滤、执行线程数、是否删除源压缩包。
- 执行阶段支持多线程、进度条、取消和失败重试。
- 日志会记录扫描、执行、失败原因和建议。
- 默认不会删除源压缩包；只有开启设置并解压成功后才删除。

## 安装

推荐使用 `uv`：

```powershell
uv sync --group dev
```

`uv` 默认使用项目目录下的 `.venv`。如果当前 PowerShell 激活的是其他环境，例如 `rut1`，`uv` 仍会优先同步 `.venv`。

或使用 `pip`：

```powershell
python -m pip install -e .
```

## 运行

```powershell
uv run python main.py
```

也可以直接使用 uv 创建的解释器：

```powershell
.\.venv\Scripts\python.exe main.py
```

安装为可编辑包后，也可以运行：

```powershell
uv run recursive-unzip-tool
```

## 使用

1. 点击“浏览”选择目标目录。
2. 点击“设置”按需调整扫描深度、文件类型、是否显示所有文件、执行线程数和删除源文件选项。
3. 点击“扫描”，等待树形列表生成。
4. 在“执行”列查看 `√`，点击该列可切换是否执行。
5. 点击“执行”。
6. 如果执行完成后存在失败项，点击“重试失败”只重新处理失败文件。

右键列表中的文件可以：

- 打开：使用系统默认程序打开文件。
- 打开文件位置：在文件浏览器中定位该文件。
- 属性：查看路径、格式、压缩包大小、解压后大小、修改时间、状态和错误建议。

## 错误处理

程序会保留原始错误信息，并根据常见异常给出建议：

- 权限问题：检查文件和目录权限，关闭占用文件的程序。
- 文件缺失：重新扫描目录。
- 文件损坏或 CRC 校验失败：重新下载或复制压缩包后重试。
- 不安全路径：程序会阻止解压写出目标目录。
- 不支持格式：当前版本不支持 RAR，请转换为 ZIP、7Z 或 TAR 系列。

## 开发与测试

```powershell
uv sync --group dev
uv run pytest
```

如果默认临时目录有权限问题，可以指定测试临时目录：

```powershell
uv run pytest --basetemp C:\tmp\recursive-unzip-tool-pytest -o cache_dir=C:\tmp\recursive-unzip-tool-pytest-cache
```
