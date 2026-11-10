# 一加 13：刷入前准备与不开机日志采集

本工具面向 PJZ110 / OP5D0DL1，Android A 槽固件 `PJZ110_16.0.10.501(CN01)`。当前手机 B 槽用于 Ubuntu；不要执行通用“双槽都刷”“切换另一槽恢复 Android”或清空数据的操作。

## 先说明 USB 的限制

| 失败阶段 | 普通 USB 是否能取日志 | 应对方式 |
| --- | --- | --- |
| Android 已启动 adbd，卡动画或桌面异常 | 已授权 ADB 通常可采集 logcat；内核日志/pstore 通常还需 root | 刷前启动电脑采集器，自动等待连接、重连并记录 |
| recovery 提供 ADB shell | 可采集 recovery 当前日志；若原有 ramoops 记录仍在且内存布局兼容，也可提取上次日志 | 尽快采集 pstore，再做其他操作 |
| 内核早期崩溃、尚未启动 adbd | 不能仅凭插入 USB 实时读取内核日志 | 若 ramoops 已初始化并保留了记录，恢复已验证的可启动镜像后立即提取 |
| fastboot/bootloader | fastboot 可用于已有恢复流程，但标准 fastboot 不提供 Linux dmesg | 不把 fastboot 连接成功误认为已抓到内核日志 |
| 黑屏、Qualcomm 9008/EDL | ADB 不可用；本项目不提供通用读取 minidump/EDL 的办法 | 使用机型适用且已验证的恢复方案；不要随意刷 programmer 或分区 |

本机现有命令行包含 `console=ttynull`，串口控制台亦被禁用，不能将普通 USB 当成调试串口。Debug 构建不改 USB、adbd 授权、AVB、设备树或串口开关，也不保证每一种不开机都能留下日志。

## 本机 pstore 实际情况

2026-11-10 只读检查确认：原内核已有 `PSTORE`、`PSTORE_RAM`、`PSTORE_CONSOLE`、`PSTORE_PMSG`，`/sys/fs/pstore` 可访问，但当时为空。ramoops 参数为：

```text
mem_size=2359296
console_size=262144
pmsg_size=2097152
record_size=0
ftrace_size=0
```

**本机只有 256 KiB 的持久控制台区，没有独立 dmesg/panic 记录区。不要保证一定出现 `dmesg-ramoops-0`；重点查看实际存在的 `console-ramoops-*` 和 `pmsg-ramoops-*`。** 记录可能被环形覆盖、后续启动覆盖、系统服务搬走/清理，或者在冷启动/断电时丢失；ramoops 尚未初始化就崩溃，也可能没有任何记录。目录为空不能证明没有内核崩溃。

为避免增加启动风险，当前不重新划分这块厂商保留内存，不修改设备树，也不故意触发 panic 来测试。采集器不会挂载文件系统、删除 pstore 文件、清空 logcat、写 SysRq、刷机、重启或切换槽位。

## 刷入前，先在电脑上完成

1. 安装官方 Android SDK Platform-Tools 和 Python 3.8+；Windows 需要可用的 ADB/fastboot 驱动。使用可靠 USB 数据线，并确认已有可用的 bootloader/recovery 恢复入口。仅有备份文件不等于恢复流程已验证。
2. 手机保持当前正常系统，开启 USB 调试，授权这台电脑；在电脑上运行 `adb devices`，状态必须为 `device`，不能是 `unauthorized`、`offline` 或空列表。
3. 运行 `adb shell su -c id`，在手机 KernelSU 中允许 ADB shell 的 root 请求。应返回 `uid=0`；先完成授权，避免出问题后无法操作屏幕。采集工具本身不能绕过 root/ADB 授权或 SELinux 权限。
4. 在仓库根目录执行一次备份及基线采集：

```sh
python scripts/collect-op13-bootlogs.py --once --backup-boot --output op13-before-flash
```

下载 `OnePlus13-Boot-Diagnostic-Kit` 工件后，脚本在解压目录根部，此时去掉命令中的 `scripts/`。Windows 如果 `python` 命令不可用，可用 `py -3`；若 adb 不在 PATH，传入 `--adb` 指定 `adb.exe` 完整路径。多个 ADB 设备连接时必须用 `--serial` 指定本机。

备份仅允许上述固件的 Android A 槽，读取 `boot_a`、`init_boot_a`、`vendor_boot_a` 到电脑，核对分区字节数、镜像 magic 并生成 `SHA256SUMS`；失败的文件保留 `.partial`，不能用来恢复。脚本使用二进制写文件，避免 Windows PowerShell 文本重定向破坏镜像。检查日志中的 `Verified read-only backup` 和三份镜像/校验文件，另存一份，不要把它们上传到公开仓库。

5. 确认基线输出有 `root-access.txt`、`dmesg.txt`、`metadata.txt`、`pstore.tar`/`pstore-index.json`。检查非空的 `.stderr.txt` 和 `commands.jsonl`；`/proc/last_kmsg` 不存在是允许的，本工具会保存报错而不伪造日志。pstore 空归档本身也可能是正常现状。
6. 在电脑另开终端，**先启动持续采集，再自行按已验证流程刷入 Debug 包**：

```sh
python scripts/collect-op13-bootlogs.py --duration 1800 --output op13-debug-flash
```

此命令等待/采集 30 分钟；用 `--duration 0` 可一直等待，按 Ctrl+C 停止。工具先保留当前启动的一份快照，再按 ADB 断连/重连及 boot ID 变化创建新目录。它不会自动刷写或要求重启，不会执行 `adb root`。

## 如果刷后没有开机

- 先保留采集器和 USB 连接，记录卡住阶段、等待时长、是否自动重启、是否有 fastboot/ADB/9008 设备。没有 ADB 连接时，采集器只能记录“未连接”，不能编造启动日志。
- 如果已有 ADB，保存当前输出；如果能进入已验证的 recovery 且有 ADB shell，尽快另开目录做 `--once` 采集。recovery 的 pstore 未挂载或不可读时，先确认该 recovery 是否支持并使用相同 reserved-memory 布局，不要盲目修改设备树。
- 若必须恢复，按刷前已验证流程恢复**本机 A 槽对应备份**，不要格式化 userdata，不要写入 Ubuntu B 槽。脚本不包含自动恢复/刷写命令。
- 恢复到能连接 ADB 后立即执行一次 `--once` 采集到新目录。**这里 dmesg/logcat 是恢复后的当前启动日志，不一定是失败启动日志；上一次的证据应主要在 pstore/last_kmsg 中查找。** 尽量避免反复启动、断电或继续强刷，以免覆盖旧记录。
- 如果 pstore 也没有内容，只能说明可用记录不足；需要结合 bootloader/recovery 可用信息、厂商 ramdump 或更早期调试方法继续调查，不能直接判断“内核没问题”。

## 输出与定位

每次连接/启动的目录包含：

- `pstore.tar`：最先尝试采集的持久记录，只归档，不删除、不自动解压；`pstore-index.json` 为其中实际普通文件列表。
- `dmesg.txt` / `kernel-live.txt`：当前启动内核快照/持续输出，权限不足时可能只有报错或空文件。
- `logcat-all.txt`：当前 Android/recovery 的可读日志缓冲区，含连接后持续输出；日志采集不执行 `logcat -c`。
- `metadata.txt`：型号/固件、boot ID、启动原因、槽位、ramoops 参数、模块与挂载状态。
- `commands.jsonl` / `*.stderr.txt` / `collector-events.log`：采集是否成功、命令退出码、授权或连接失败信息。

Debug 内核额外输出 `OP13-DIAG:` 标记，覆盖早期 cgroup、调度器初始化、pre-SMP/basic setup、每个 initcall 的函数名与返回值、转交用户态 init 的位置。最后一个 `begin` 后没有对应完成记录可作为排查线索，**不是单独定罪依据**，因为并发、重启或环形覆盖也会造成不完整日志。原有 panic/Oops、模块加载/签名错误继续保留。

检索 `OP13-DIAG:`、`Kernel panic`、`Unable to handle`、`Call trace`、`Unknown symbol`、`disagrees about version`、`exports protected symbol`、`Required key not available`、`f2fs`、`fs_mgr` 和 `avc: denied`。日志可能含设备序列号、地址、应用活动、文件路径和其他私人信息；先离线筛选脱敏，仅提供相关片段，勿公开上传整套日志或启动分区备份。

## 正常开机后再移除临时日志

当前手动构建输入 `diagnostics=true`，产物名含 `Docker-Debug-<run>.zip`。Debug 增加启动输出及一定时序/性能开销，但不启用 permissive、不关闭 CFI/模块签名、不增加 tracing 字段、不更改厂商 ramoops 内存布局。

先确认 Android 启动稳定、root、Wi-Fi/蜂窝/蓝牙、存储、休眠唤醒和所需 Docker 功能都正常，并保留这一轮日志。**收到实际使用通过的反馈后，再使用相同源码提交、相同 KernelSU 输入，将 `diagnostics` 改为 `false` 重建 Release。** 该路径不应用临时日志补丁，打包前检查实际 Image 不含 `OP13-DIAG:` 标记；原厂已有 printk/pstore 等能力保留，不能把它们一起关闭。当前不会自动刷机测试或提前宣称 Release 已实机验证。
