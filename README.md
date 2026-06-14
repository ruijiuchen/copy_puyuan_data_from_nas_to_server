# Puyuan 数据实时安全拷贝工具

实时监控 NAS 上的 Puyuan（谱元）数据采集文件，仅在文件**写完整**后才将其安全拷贝到目标服务器，避免拷贝未写完的残缺文件。支持**多通道并行拷贝**，可同时处理多个数据采集通道。

## 功能特性

- **文件完整性检测** — 文件大小 ≥ 指定阈值时启动拷贝，确保不拷贝尚未写完的数据
- **智能尾文件处理** — 文件大小连续 60 秒未变化，或检测到更大序号的下一文件已生成时，判定为采集结束的最后一个文件，强制拷贝
- **多通道并行拷贝** — 通过 `--channel` 参数配置多个采集通道，各通道在独立线程中并行拷贝
- **自动切换目录（auto-next）** — 当前目录连续 1 分钟无新文件时，自动切换到下一序号目录继续拷贝
- **断点续传** — 通过 `progress.txt` 记录已拷贝的文件索引，程序中断后重新运行时从中断处继续
- **自定义文件前缀** — 通过 `--prefix` 参数指定文件名前缀（默认 `PY82ch1_`），适配不同实验的数据文件命名
- **实时进度显示** — 显示文件名、源路径、目标路径、文件大小、拷贝耗时、平均速度
- **Shell 包装脚本** — 提供预配置的 shell 脚本，直接运行即可启动多通道拷贝，避免每次输入冗长的命令行参数

## 工作原理

### 文件完整性判定

| 情况 | 条件 | 行为 |
|------|------|------|
| **正常写完整** | 文件大小 ≥ 阈值（如 1.0 GB） | 直接拷贝 |
| **存在下一文件** | 当前文件未写满，但目录中已存在更大序号的文件 | 当前文件已停止写入，直接拷贝 |
| **采集结束尾文件** | 文件大小连续 6 次（60 秒）未变化，且无更大序号文件 | 判定为最后一块未写满的数据，强制拷贝 |

### 自动切换目录（auto-next）

当开启 `--auto-next` 后，若当前目录连续 2 次检测（约 1 分钟）未发现新文件，工具会自动：

1. 解析当前目录的数字前缀（如 `8500_TestMode` → `8500`）
2. 在同一父目录下寻找比当前序号更大的最小序号目录（如 `8501_...`）
3. 自动切换到新目录继续拷贝，在新目录中重新开始记录 `progress.txt`

### 断点续传

程序在 **目标目录** 下维护一个 `progress.txt` 文件，记录最后一次成功拷贝的文件索引：

1. **启动时** — 检查 `progress.txt`，若存在则跳转到 `last_index + 1` 继续
2. **拷贝成功后** — 将当前索引写入 `progress.txt`
3. **中断时（Ctrl+C）** — 进度已保存，重新运行自动从断点处继续

> 若更换 `--prefix` 重新运行，建议先删除目标目录下的旧 `progress.txt`，避免索引混淆。

## 使用方法

### 方式一：直接运行 Python 脚本（灵活配置）

#### 单通道模式（向后兼容）

```bash
python copy_puyuan_data_from_nas_to_server.py <源目录> <目标目录> <文件大小阈值(GB)> [--prefix <前缀>] [--auto-next]
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source_dir` | 路径 | 是 | 源数据目录（NAS 挂载路径） |
| `target_dir` | 路径 | 是 | 目标目录（服务器本地路径） |
| `complete_size_gb` | 浮点数 | 是 | 完整文件大小阈值，单位 GB |
| `--prefix` | 字符串 | 否 | 文件前缀，默认 `PY82ch1_` |
| `--auto-next` | 标志 | 否 | 当前目录无新文件 1 分钟后，自动切到下一序号目录 |

```bash
# 默认前缀 PY82ch1_，文件写满 1 GB 才拷贝
python copy_puyuan_data_from_nas_to_server.py /mnt/nas/data /data/experiment 1.0

# 自定义前缀，开启自动切目录
python copy_puyuan_data_from_nas_to_server.py /mnt/nas/8500_TestMode /data/exp 1.0 --prefix "PY82ch1_" --auto-next
```

#### 多通道并行模式

```bash
python copy_puyuan_data_from_nas_to_server.py \
  --channel "name=名称,source=源目录,target=目标目录,prefix=前缀,size_gb=阈值,auto_next=yes/no" \
  [--channel ...]
```

`--channel` 参数可重复使用，每个定义一条采集通道。

| 配置项 | 必填 | 说明 |
|--------|------|------|
| `name` | 是 | 通道名称，如 `ch1` |
| `source` | 是 | 源数据目录路径 |
| `target` | 是 | 目标目录路径 |
| `prefix` | 否 | 文件前缀，默认 `PY82ch1_` |
| `size_gb` | 否 | 完整文件阈值，默认 `1.0078` |
| `auto_next` | 否 | 是否自动切目录，`yes`/`no`，默认 `no` |

```bash
# 两个通道并行拷贝，均开启自动切目录
python copy_puyuan_data_from_nas_to_server.py \
  --channel "name=PY84ch1,source=/mnt/nas84/raw_data/puyuan84_test/Data/ch1/8478_TestModePY84_26-06-14_13-53-07,target=/mnt/data1/raw_data/puyuan84_test/Data/ch1/8478_TestModePY84_26-06-14_13-53-07,prefix=PY84ch1_,size_gb=1.0078,auto_next=yes" \
  --channel "name=PY84ch2,source=/mnt/nas84/raw_data/puyuan84_test/Data/ch2/8478_TestModePY84_26-06-14_13-53-07,target=/mnt/data1/raw_data/puyuan84_test/Data/ch2/8478_TestModePY84_26-06-14_13-53-07,prefix=PY84ch2_,size_gb=1.0078,auto_next=yes"
```

### 方式二：使用 Shell 包装脚本（快速启动）

提供了预配置的 shell 脚本，内置了完整的 `--channel` 参数，直接运行即可：

```bash
# 启动 puyuan84 数据拷贝（两通道并行，自动切目录）
bash copy_puyuan_data_from_nas_to_server_puyuan84.sh
```

脚本内容示例（`copy_puyuan_data_from_nas_to_server_puyuan84.sh`）：

```bash
python copy_puyuan_data_from_nas_to_server.py \
  --channel "name=PY84ch1,source=...,target=...,prefix=PY84ch1_,size_gb=1.0078,auto_next=yes" \
  --channel "name=PY84ch2,source=...,target=...,prefix=PY84ch2_,size_gb=1.0078,auto_next=yes"
```

每个 shell 脚本对应一套固定的实验配置，开箱即用，适合日常生产运行。

## 运行输出示例

### 单通道模式

```
==========================================================================================
Puyuan 数据实时安全拷贝工具
源目录: /mnt/nas/data
目标目录: /data/experiment
文件前缀: PY82ch1_
完整文件阈值: >= 1.0 GB
程序将持续运行，直到手动中断（Ctrl+C）
==========================================================================================

未检测到进度文件，从 PY82ch1_0.data 开始

开始实时监控并拷贝文件
完整文件判定标准：大小 >= 1.0 GB (1,073,741,824 字节)

[0] 文件尚未生成：PY82ch1_0.data
   源路径: /mnt/nas/data/PY82ch1_0.data
   等待新文件... (30秒后重新检查)

[0] 文件正在写入中...
   文件名: PY82ch1_0.data
   源路径: /mnt/nas/data/PY82ch1_0.data
   当前大小: 0.6324 GB (需 >= 1.0 GB)
   10秒后重新检查...

[0] 文件已写完整，准备拷贝
   文件名: PY82ch1_0.data
   源文件: /mnt/nas/data/PY82ch1_0.data
   目标文件: /data/experiment/PY82ch1_0.data
   文件大小: 1.0000 GB

[0] 拷贝成功！
   耗时: 25.34 秒
   平均速度: 40.42 MB/s
```

### 多通道模式

```
==========================================================================================
Puyuan 多通道并行拷贝工具
共 2 个通道

  [PY84ch1]
    源目录: /mnt/nas84/raw_data/puyuan84_test/Data/ch1/8478_TestModePY84_...
    目标目录: /mnt/data1/raw_data/puyuan84_test/Data/ch1/8478_TestModePY84_...
    文件阈值: >= 1.0078 GB
    前缀: PY84ch1_
    自动切目录: 是

  [PY84ch2]
    源目录: /mnt/nas84/raw_data/puyuan84_test/Data/ch2/8478_TestModePY84_...
    目标目录: /mnt/data1/raw_data/puyuan84_test/Data/ch2/8478_TestModePY84_...
    文件阈值: >= 1.0078 GB
    前缀: PY84ch2_
    自动切目录: 是

按 Ctrl+C 中断所有通道
==========================================================================================
```

## 文件结构

```
copy_puyuan_data_from_nas_to_server/
├── copy_puyuan_data_from_nas_to_server.py                # Python 主程序
├── copy_puyuan_data_from_nas_to_server.sh                 # Shell 包装脚本（puyuan84 快速启动）
├── copy_puyuan_data_from_nas_to_server_puyuan84.sh        # Shell 包装脚本（puyuan84 另一实验配置）
├── README.md                                              # 本文件
└── .gitignore                                             # 忽略运行生成的 progress.txt
```

## 依赖

- Python 3.6+
- 标准库（无第三方依赖）：`os`, `shutil`, `argparse`, `sys`, `time`, `threading`
