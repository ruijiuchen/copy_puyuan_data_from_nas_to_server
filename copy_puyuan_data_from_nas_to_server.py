#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import argparse
import sys
import time
import threading


_AUTO_NEXT_NO_FILE_LIMIT = 2  # 连续 2 次（30s × 2 = 1 分钟）未检测到新文件，触发 auto-next

# 无数据通道自动关闭
_NO_DATA_RETIRE_LIMIT = 3  # 连续 3 次（30s × 3 = 90 秒）未检测到序号0文件，判定该通道无数据并关闭

# 磁盘空间阈值
_DISK_FREEZE_PCT = 0.05       # 剩余空间 < 5% 时暂停拷贝
_DISK_WARN_PCT = 0.10         # 剩余空间 < 10% 时发出警告
_DISK_CHECK_INTERVAL = 10      # 磁盘空间检查间隔（多少次拷贝后检查一次）


def _find_next_dir_basename(current_dir: str):
    """
    在 current_dir 的父目录中，根据当前目录的数字前缀找下一个目录。
    如 "8500_TestMode_..." → 找 "8501_...", "8502_..." 中数字最小的。
    返回目录 basename，找不到返回 None。
    """
    parent = os.path.dirname(current_dir)
    cur_basename = os.path.basename(current_dir)
    parts = cur_basename.split('_')
    if not parts or not parts[0].isdigit():
        return None
    cur_num = int(parts[0])

    next_basename = None
    next_num = None
    try:
        for entry in sorted(os.listdir(parent)):
            entry_path = os.path.join(parent, entry)
            if not os.path.isdir(entry_path):
                continue
            e_parts = entry.split('_')
            if not e_parts or not e_parts[0].isdigit():
                continue
            num = int(e_parts[0])
            if num > cur_num and (next_num is None or num < next_num):
                next_num = num
                next_basename = entry
    except Exception:
        pass
    return next_basename


def _find_last_dir_basename(parent_dir: str):
    """
    在 parent_dir 中找数字前缀最大的子目录。
    如 "/mnt/data/ch1/" 下有 "8500_TestMode_...", "8501_TestMode_..."，
    则返回 "8501_TestMode_..."（最大序号）。
    返回目录 basename，找不到返回 None。
    """
    last_basename = None
    last_num = None
    try:
        for entry in sorted(os.listdir(parent_dir)):
            entry_path = os.path.join(parent_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            e_parts = entry.split('_')
            if not e_parts or not e_parts[0].isdigit():
                continue
            num = int(e_parts[0])
            if last_num is None or num > last_num:
                last_num = num
                last_basename = entry
    except Exception:
        pass
    return last_basename

# 线程锁，防止多通道打印内容交错
_print_lock = threading.Lock()


def _tprint(*args, **kwargs):
    """线程安全的 print"""
    with _print_lock:
        print(*args, **kwargs)


def _check_disk_space(target_dir: str, complete_size_gb: float, tag: str) -> bool:
    """
    检查目标目录所在磁盘的剩余空间。
    - 剩余 < 5% 时返回 False（暂停拷贝）
    - 剩余 < 10% 时输出警告
    总是输出剩余空间和可存储文件数。
    返回 True 表示可以继续拷贝，False 表示空间不足需等待。
    """
    try:
        usage = shutil.disk_usage(os.path.dirname(target_dir))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        free_pct = usage.free / usage.total
        can_store = int(usage.free / (complete_size_gb * 1024 ** 3))

        _tprint(f"{tag}   磁盘剩余: {free_gb:.2f} GB / {total_gb:.2f} GB ({free_pct*100:.1f}%)"
                f"，还可存储约 {can_store} 个完整文件")

        if free_pct < _DISK_FREEZE_PCT:
            _tprint(f"{tag}   ⚠️  磁盘剩余空间不足 5%，暂停拷贝，等待释放空间...")
            return False
        elif free_pct < _DISK_WARN_PCT:
            _tprint(f"{tag}   ⚠️  警告：磁盘剩余空间不足 10%")

        return True
    except Exception as e:
        _tprint(f"{tag}   获取磁盘空间信息失败: {e}")
        return True


def copy_files_with_progress(source_dir: str, target_dir: str, complete_size_gb: float,
                             file_prefix: str = "PY82ch1_", auto_next: bool = False,
                             channel_name: str = ""):
    """
    实时拷贝 Puyuan 数据文件（{file_prefix}0.data, {file_prefix}1.data, ...）
    - 文件大小 >= complete_size_gb GB 时直接拷贝
    - 若文件未写满但大小连续 6 次（60 秒）未变化，认定为采集结束的最后一个文件，强制拷贝
    - 支持断点续传
    - 显示每个文件的文件名、源路径、目标路径、拷贝速度和耗时
    - auto_next=True: 当前目录数据拷贝完毕后，自动切到下一个序号目录继续拷贝
    """
    tag = f"[{channel_name}]" if channel_name else ""
    complete_size_bytes = int(complete_size_gb * 1024 * 1024 * 1024)

    # auto-next: 记录原始父目录，用于切换到下一个目录
    source_parent = os.path.dirname(source_dir.rstrip('/\\'))
    target_parent = os.path.dirname(target_dir.rstrip('/\\'))

    while not os.path.isdir(source_dir):
        # 先检查父目录下是否有已创建的新目录，自动切过去
        if os.path.isdir(source_parent):
            last_basename = _find_last_dir_basename(source_parent)
            if last_basename:
                source_dir = os.path.join(source_parent, last_basename)
                target_dir = os.path.join(target_parent, last_basename)
                _tprint(f"{tag} 检测到新目录：{last_basename}，自动切换\n")
                break
        _tprint(f"{tag} 错误：源目录不存在 -> {source_dir}")
        _tprint(f"{tag} 10秒后重新检查...\n")
        time.sleep(10)

    # 启动时总是检查最新目录，如果当前不是最新则直接跳到最新
    if os.path.isdir(source_parent):
        latest_basename = _find_last_dir_basename(source_parent)
        if latest_basename:
            cur_basename = os.path.basename(source_dir.rstrip('/\\'))
            if latest_basename != cur_basename:
                source_dir = os.path.join(source_parent, latest_basename)
                target_dir = os.path.join(target_parent, latest_basename)
                _tprint(f"{tag} 启动时跳转到最新目录：{latest_basename}\n")

    os.makedirs(target_dir, exist_ok=True)

    no_file_count = 0  # 连续未检测到新文件的次数

    progress_file = os.path.join(target_dir, 'progress.txt')

    # 读取上次进度
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r') as f:
                last_index = int(f.read().strip())
            current_index = last_index + 1
            _tprint(f"{tag} 检测到进度文件，从索引 {current_index} 开始（上次已完成至 {last_index}）")
        except Exception:
            current_index = 0
            _tprint(f"{tag} 进度文件异常，从 {file_prefix}0.data 开始")
    else:
        current_index = 0
        _tprint(f"{tag} 未检测到进度文件，从 {file_prefix}0.data 开始")

    _tprint(f"{tag} 开始实时监控并拷贝文件")
    _tprint(f"{tag} 完整文件判定标准：大小 >= {complete_size_gb} GB ({complete_size_bytes:,} 字节)\n")

    # 用于跟踪文件大小是否长时间不变（检测采集系统最后一个未完整写入的文件）
    _size_monitor = {}  # {index: {"last_size": int, "unchanged_count": int}}
    _disk_check_cnt = 0  # 磁盘空间检查计数器
    _no_data_retire_cnt = 0  # 连续未检测到序号0文件的次数

    while True:
        filename = f'{file_prefix}{current_index}.data'
        source_file = os.path.join(source_dir, filename)
        target_file = os.path.join(target_dir, filename)

        # 1. 文件不存在
        if not os.path.exists(source_file):
            _tprint(f"{tag} [{current_index}] 文件尚未生成：{filename}")
            _tprint(f"{tag}   源路径: {source_file}")

            # 序号 0 文件连续多次未出现 → 该通道无数据，关闭
            if current_index == 0:
                _no_data_retire_cnt += 1
                _tprint(f"{tag}   无数据计数器: {_no_data_retire_cnt}/{_NO_DATA_RETIRE_LIMIT}")
                if _no_data_retire_cnt >= _NO_DATA_RETIRE_LIMIT:
                    _tprint(f"{tag} ⚠️  连续 {_NO_DATA_RETIRE_LIMIT} 次未检测到序号0文件，"
                            f"判定该通道无数据，关闭通道")
                    return
            else:
                _no_data_retire_cnt = 0

            no_file_count += 1
            if auto_next and no_file_count >= _AUTO_NEXT_NO_FILE_LIMIT:
                # 切换到下一个目录
                next_basename = _find_next_dir_basename(source_dir)
                if not next_basename:
                    # 顺序查找没找到，再查最新的目录（可能非顺序跳号）
                    next_basename = _find_last_dir_basename(source_parent)
                    cur_basename = os.path.basename(source_dir.rstrip('/\\'))
                    if next_basename == cur_basename:
                        next_basename = None  # 同一个目录，无更新
                if next_basename:
                    source_dir = os.path.join(source_parent, next_basename)
                    target_dir = os.path.join(target_parent, next_basename)
                    os.makedirs(target_dir, exist_ok=True)
                    _tprint(f"{tag}   → 切换到下一个目录：{next_basename}\n")
                    # 重置状态
                    _size_monitor.clear()
                    current_index = 0
                    no_file_count = 0
                    _no_data_retire_cnt = 0
                    progress_file = os.path.join(target_dir, 'progress.txt')
                    continue
                else:
                    # 没有更高序号目录 → 当前实验仍在进行中，继续监控当前目录
                    _tprint(f"{tag}   仍是最新目录，继续等待新文件...")
                    no_file_count = 0
            else:
                # 等待期间也检查是否有新目录出现
                if auto_next and no_file_count > 0:
                    check_next = _find_next_dir_basename(source_dir)
                    if not check_next:
                        check_next = _find_last_dir_basename(source_parent)
                        cur_basename = os.path.basename(source_dir.rstrip('/\\'))
                        if check_next == cur_basename:
                            check_next = None
                    if check_next:
                        # 确实有下一个目录了，快速推进到下一个循环立即处理
                        no_file_count = _AUTO_NEXT_NO_FILE_LIMIT
                _tprint(f"{tag}   等待新文件... (30秒后重新检查)\n")
            time.sleep(30)
            continue

        # 2. 获取当前大小
        try:
            current_size = os.path.getsize(source_file)
            current_size_gb = current_size / (1024 * 1024 * 1024)
        except Exception as e:
            _tprint(f"{tag} [{current_index}] 获取文件大小失败: {e}")
            time.sleep(30)
            continue

        # 3. 判断是否写完整
        has_higher_index = False
        if current_size < complete_size_bytes:
            # 先检查是否存在更大序号的文件（采集系统已进入下一文件，当前不会再增长）
            try:
                for fname in os.listdir(source_dir):
                    if fname.startswith(file_prefix) and fname.endswith('.data'):
                        try:
                            idx_str = fname[len(file_prefix):-5]  # 去掉前缀和 .data
                            if int(idx_str) > current_index:
                                has_higher_index = True
                                break
                        except ValueError:
                            continue
            except Exception:
                pass

            if has_higher_index:
                _tprint(f"{tag} [{current_index}] 检测到更大序号文件，当前文件停止增长，直接拷贝")
                _tprint(f"{tag}   文件名: {filename}")
                _tprint(f"{tag}   源路径: {source_file}")
                _tprint(f"{tag}   当前大小: {current_size_gb:.4f} GB")
            else:
                # 检查该文件的大小是否长时间未变化（采集系统可能已停止）
                monitor = _size_monitor.get(current_index)
                if monitor is None:
                    _size_monitor[current_index] = {"last_size": current_size, "unchanged_count": 0}
                elif current_size == monitor["last_size"]:
                    _size_monitor[current_index]["unchanged_count"] += 1
                else:
                    _size_monitor[current_index] = {"last_size": current_size, "unchanged_count": 0}

                unchanged = _size_monitor[current_index]["unchanged_count"]

                _tprint(f"{tag} [{current_index}] 文件正在写入中...")
                _tprint(f"{tag}   文件名: {filename}")
                _tprint(f"{tag}   源路径: {source_file}")
                _tprint(f"{tag}   当前大小: {current_size_gb:.4f} GB (需 >= {complete_size_gb} GB)")
                if unchanged > 0:
                    _tprint(f"{tag}   文件大小已 {unchanged}/3 次未变化 (持续 {unchanged * 10} 秒)")

                if unchanged >= 3:
                    # 连续 3 次大小不变（30 秒），判定为采集结束的最后一个文件
                    _tprint(f"{tag}   → 文件大小连续 3 次未变化，判定为最后一个文件，开始拷贝")
                else:
                    _tprint(f"{tag}   10秒后重新检查...\n")
                    time.sleep(10)
                    continue

        # 4. 文件已完整（>= 指定大小），开始拷贝
        if current_size >= complete_size_bytes:
            reason = "文件已写完整"
        elif has_higher_index:
            reason = "存在更大序号文件，当前文件停止增长"
        else:
            reason = "文件大小连续 6 次未变化，采集结束，最后一个文件"

        _tprint(f"{tag} [{current_index}] {reason}，准备拷贝")
        _tprint(f"{tag}   文件名: {filename}")
        _tprint(f"{tag}   源文件: {source_file}")
        _tprint(f"{tag}   目标文件: {target_file}")
        _tprint(f"{tag}   文件大小: {current_size_gb:.4f} GB")

        # 检查磁盘空间（每 _DISK_CHECK_INTERVAL 次拷贝检查一次）
        _disk_check_cnt += 1
        if _disk_check_cnt >= _DISK_CHECK_INTERVAL:
            _disk_check_cnt = 0
            while not _check_disk_space(target_dir, complete_size_gb, tag):
                _tprint(f"{tag}   30秒后重新检查磁盘空间...\n")
                time.sleep(30)

        start_time = time.time()

        try:
            shutil.copy2(source_file, target_file)

            elapsed_time = time.time() - start_time
            speed_mbps = (current_size_gb * 1024) / elapsed_time if elapsed_time > 0 else 0.0

            _tprint(f"{tag} [{current_index}] 拷贝成功！")
            _tprint(f"{tag}   耗时: {elapsed_time:.2f} 秒")
            _tprint(f"{tag}   平均速度: {speed_mbps:.2f} MB/s\n")

            # 更新进度
            with open(progress_file, 'w') as f:
                f.write(str(current_index))

            # 清理该文件的大小监控记录
            _size_monitor.pop(current_index, None)

            current_index += 1

        except KeyboardInterrupt:
            _tprint(f"\n\n{tag} 用户中断程序（Ctrl+C），当前进度已保存，可下次继续运行。")
            return
        except Exception as e:
            _tprint(f"{tag} [{current_index}] 拷贝失败: {e}\n")
            time.sleep(1)

def _run_channel(channel: dict):
    """在线程中运行一个通道的拷贝任务"""
    name = channel.get("name", "")
    copy_files_with_progress(
        source_dir=channel["source_dir"],
        target_dir=channel["target_dir"],
        complete_size_gb=channel["complete_size_gb"],
        file_prefix=channel.get("prefix", "PY82ch1_"),
        auto_next=channel.get("auto_next", False),
        channel_name=name,
    )


def main():
    parser = argparse.ArgumentParser(
        description="实时安全拷贝 Puyuan 数据文件，支持多通道并行拷贝"
    )
    parser.add_argument("--channel", action="append", dest="channels", default=None,
                        help="通道配置，格式：name=通道名,source=源目录,target=目标目录,prefix=前缀,size_gb=大小,auto_next=yes/no。可重复使用。")
    parser.add_argument("source_dir", nargs="?", help="源数据目录路径（单通道模式）")
    parser.add_argument("target_dir", nargs="?", help="目标目录路径（单通道模式）")
    parser.add_argument("complete_size_gb", nargs="?", type=float, help="完整文件大小（GB）（单通道模式）")
    parser.add_argument("--prefix", default="PY82ch1_", help="文件前缀，默认 PY82ch1_（单通道模式）")
    parser.add_argument("--auto-next", action="store_true",
                        help="当前目录拷贝完毕后，自动寻找下一个序号目录继续拷贝")

    args = parser.parse_args()

    if args.channels:
        # === 多通道模式：从命令行读取所有通道 ===
        channels = []
        for ch_str in args.channels:
            parts = {}
            for item in ch_str.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    parts[k.strip()] = v.strip()
            if "name" not in parts or "source" not in parts or "target" not in parts:
                print(f"错误：--channel 参数格式无效 -> {ch_str}")
                print("正确格式：name=通道名,source=源目录,target=目标目录,prefix=前缀,size_gb=大小,auto_next=yes/no")
                sys.exit(1)
            channels.append({
                "name": parts["name"],
                "source_dir": parts["source"],
                "target_dir": parts["target"],
                "prefix": parts.get("prefix", "PY82ch1_"),
                "complete_size_gb": float(parts.get("size_gb", "1.0078")),
                "auto_next": parts.get("auto_next", "no").lower() == "yes",
            })

        print("=" * 90)
        print(f"Puyuan 多通道并行拷贝工具")
        print(f"共 {len(channels)} 个通道\n")
        for ch in channels:
            print(f"  [{ch['name']}]")
            print(f"    源目录: {ch['source_dir']}")
            print(f"    目标目录: {ch['target_dir']}")
            print(f"    文件阈值: >= {ch['complete_size_gb']} GB")
            print(f"    前缀: {ch['prefix']}")
            print(f"    自动切目录: {'是' if ch['auto_next'] else '否'}")
            print()
        print("按 Ctrl+C 中断所有通道")
        print("=" * 90)

        threads = []
        for ch in channels:
            t = threading.Thread(target=_run_channel, args=(ch,), daemon=True)
            t.start()
            threads.append(t)

        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            print("\n\n用户中断程序，所有通道停止运行。")

    else:
        # === 单通道模式（向后兼容） ===
        if not args.source_dir or not args.target_dir or args.complete_size_gb is None:
            parser.print_help()
            sys.exit(1)

        print("=" * 90)
        print("Puyuan 数据实时安全拷贝工具")
        print(f"源目录: {args.source_dir}")
        print(f"目标目录: {args.target_dir}")
        print(f"文件前缀: {args.prefix}")
        print(f"完整文件阈值: >= {args.complete_size_gb} GB")
        if args.auto_next:
            print("自动切换目录: 是（当前目录无新文件1分钟后自动切到下一序号目录）")
        print("程序将持续运行，直到手动中断（Ctrl+C）")
        print("=" * 90)

        copy_files_with_progress(args.source_dir, args.target_dir, args.complete_size_gb,
                                 args.prefix, args.auto_next)


if __name__ == "__main__":
    main()
