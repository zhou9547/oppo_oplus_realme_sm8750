# 一加 13 原生 Docker 内核

## 适配范围

本配置取自 2026-11-10 直接读取的实机，而不是沿用旧 Ace 5 Pro 分支：

| 项目 | 实机值 |
| --- | --- |
| 型号 / device | PJZ110 / OP5D0DL1 |
| 系统 | Android 16，PJZ110_16.0.10.501(CN01) |
| 内核 | 6.6.118-android15-8-g93e223c276e7-abogki500782043-4k |
| Root 管理器 | KernelSU 3.3.1 |

`configs/oneplus13-stock.config` 来自 `/proc/config.gz`，`configs/oneplus13-stock-abi.json` 来自 `/sys/kernel/btf/vmlinux`。`configs/oneplus13-stock-symbols.tsv` 从本机 vendor_dlkm、system_dlkm 和当前槽位 vendor_boot ramdisk 内共 1068 份 stock 模块文件的 ELF 版本表提取 3831 个内核导入符号 CRC，并剔除模块之间提供的符号；计数包含分区间同名副本。vendor_boot 中的早期模块不能漏掉，否则会将它们提供的符号误认为内核缺少导出。这些提交文件只有编译选项、类型布局和校验值，不包含模块代码、分区镜像、密钥或用户数据。系统显示 Android 16 而 GKI 内核名称保留 android15 是本机的实际情况。

工作流为 `.github/workflows/oneplus13-docker.yml`，目前默认 `diagnostics=true`，产物位于 Actions 的 `OnePlus13-6.6.118-Docker-Debug-AnyKernel3` 工件内；关闭后为 `Release`。下载工件后，刷入内部的 `AnyKernel3-*.zip`，不要刷 GitHub 外层的工件打包 ZIP。先按 [启动诊断与日志采集](oneplus13-boot-debug.md) 在电脑上备份并启动采集器；确认实机和 Docker 正常后再关闭临时日志重建，不提前发布已验证的无日志版。

## 2026-11-10 启动专项复核

**不要刷第 6 次构建的 `AnyKernel3-OnePlus13-6.6.118-Docker-6.zip`。** 它通过了结构和 CRC 检查，但遗漏了原厂模块签名信任。该 ZIP 的 SHA-256 为 `6007de20ab0dbb4298e531cd085efc5576cf890ba1923ce8d2605791fc8e08e2`；Actions run 为 `42771774380`，源提交为 `2474e4a24a7d2b3faa52ee5d1de56704bae8de67`。

实机只读复核及下载包检查确认：

- 手机仍为上述固件，当前槽位 `_a`，bootloader 为 unlocked/orange。
- `boot_a` 为 96 MiB、header v4、无 ramdisk；原 kernel 为 36,866,560 字节，第 6 包为 38,005,248 字节，均为未压缩的 4 KiB ARM64 Image。本地使用包内同版本 magiskboot 重打包副本成功，未刷写分区。
- 实际 root 为 KernelSU LKM，`init_boot_a` 的 ramdisk 含 `ksuinit`、`init.real`、`kernelsu.ko`。实机 ksuinit 包含内置 KernelSU 已存在则跳过加载的逻辑；对应固定版本源码也在加载前调用 `has_kernelsu()`。因此没有仅因 LKM 改为内置版就必然双重加载的证据，但这不等于实测启动通过。
- 第 6 包内嵌配置为 `CONFIG_MODULE_SIG_PROTECT=y`、`CONFIG_SYSTEM_TRUSTED_KEYS=""`，内核仅包含构建时新生成的证书，不含原厂模块公钥。实机全部 96 个 system_dlkm 模块均已用从当前 boot 提取的原厂公钥完成 OpenSSL CMS 验签；其中 30 个模块导出保护符号，并且当前均已加载。新内核不信任其签名时会将其视为 vendor 模块，`verify_exported_symbols()` 会因保护导出而返回 `-EACCES`。这是 CRC 校验无法覆盖的真实拒载缺陷，可能导致启动异常或设备功能失效，不能仅凭它断言每次都会卡开机。
- 从第 6 包实际 Image 提取 BTF，除原先 31 项门禁外，扩大比较了与实机同名的 10,569 个结构的大小及原字段偏移，发现 3 项差异：`hmbird_entity` 的原 `swap_cpu`/`reserve` 区域在源码中为 `kf_mask`，`hmbird_dispatch_q` 缺少原 `is_fatal_timeout` 字段；两者总大小及其余既有字段偏移相同，说明底层源码并非当前固件的逐字段一致版本，但不能仅凭字段名证明运行时冲突。`net_bridge` 因开启 BRIDGE_NETFILTER 从 1720 增至 2024 字节，是桥接内部结构的条件扩展，厂商模块若直接访问该私有布局仍有风险。这三项不属于原先 31 项检查的覆盖范围，也不代表已发现三处必然开机崩溃。证书修复并未消除这些待验证项。
- 当前机器安装了 Android A / Ubuntu B 双启动模块，不能把切换 B 槽当作恢复 Android 的通用办法。恢复应使用本机对应槽位和固件的备份，并预先验证恢复入口。

修复使用 `configs/oneplus13-stock-module-cert.pem`，仅包含从当前 boot 提取并验证的 **X.509 公共证书，不包含私钥**。DER SHA-256 为 `9be2c27bf7671d3935bfa2379bbd534d7ca3f46a31531b3db80f52aa051775d3`。通过 `CONFIG_SYSTEM_TRUSTED_KEYS` 将其加入系统可信密钥环，不关闭签名保护、不改成强制加载；打包前检查证书指纹、实际构建的可信证书表及实际 Image 均包含此证书。同时明确关闭 stock 未启用而源码默认新增的 ADIOS 模块选项。

新包仍必须重新完成所有构建门禁；这些修复和静态核查不构成“保证开机”。CFS/cgroup 私有尾部分配、SYSVIPC/conntrack 保留空间及第三方 root 模块的运行时兼容性，仍需可恢复条件下的真实启动与日志验证。检查过程不会自动刷机、重启、切换槽位或禁用手机模块。

证书修复已纳入重建流程；是否通过构建以对应提交的 Actions 结果为准，不等同于实机测试。旧第 6 包不会因为源码更新自动获得修复，不能继续使用它验证修复效果。

## 为什么不直接全开配置

旧 `lvyc1234` 的 `2` 分支是 6.6.66 的多功能试验流程，并没有完整启用 Docker。它将追加 NET_NS 的命令注释掉，但不能据此判断最终内核是否禁用该功能；CGROUP_NS、DEVPTS_MULTIPLE_INSTANCES 已不是本版内核的有效选项，IPVLAN_L3S 则是由依赖自动决定的隐藏选项，不能靠追加名字确认功能生效。实机原内核的 NET_NS、VETH 已经开启，因此不能直接归因于 VETH。

以下变化具有可从源码确认的厂商模块 ABI 风险，但旧机具体哪次不开机仍需对应 pstore/ramoops 日志才能定论：

- SYSVIPC 在 `task_struct` 中间增加字段；将其迁移至 KABI 6/7/8 保留空间。
- 增加 cgroup 控制器会改变既有 ID、`css_set`/`cgroup` 数组及后续字段；保留原 ID 和数组长度，新增 PIDS/DEVICE/NET_CLASSID 状态放入私有分配尾部，静态初始对象使用单独存储。NET_CLASSID 是 xt_cgroup 自动选择的依赖，也必须处理。
- CFS_BANDWIDTH 会扩展 `task_group`、`cfs_rq`、`rq`；使用独立带宽状态，不能占用已可能被厂商 schedtune 使用的保留槽。
- bridge netfilter、nftables、IPVS 会扩展网络命名空间；改用 per-net 私有存储，保留既有 `net`/`netns_nf` 布局。
- IPVS 默认会插入 `sk_buff` 位域中部，改变后续网络标记；迁至本机位域末端的空闲位，保留既有位偏移和符号版本视图。
- IPVLAN_L3S 会隐式开启 NET_L3_MASTER_DEV，在 `net_device` 中间插入指针；迁至本机发送缓存行之前的对齐空隙，不占用厂商保留槽，并保持原有符号版本视图。
- NET_CLASSID 的 `sock_cgroup_data.classid` 迁至本机 `prioidx` 之后的对齐空隙，避免改变已有网络优先级字段和符号 CRC。
- 保留 `skb_ext` 原有扩展编号、offset 数组和 chunks 偏移；桥接扩展编号追加，索引单独放入原有对齐空隙。`netns_ipv4` 的三个 L3 master sysctl 标志也迁入原有对齐空隙。这些嵌套类型会影响模块 CRC，即便外层结构大小未变化，也必须处理。
- `nf_queue_entry` 的桥接设备引用置于路由键之后的私有分配尾部，保留原字段、路由键位置及复制/释放语义；netfilter core 仅在编译时引入 pernet 实现，保持原有不透明类型的符号版本视图。
- conntrack zone 放入 `nf_conn` 的 KABI 2 保留空间。

CFS/PIDS 的基础补丁固定为 UlasuNoka/curio-kernel-patches 提交 `9641ce0540cac0017d5373018e1352183df56827` 的 `common/droidspaces/cgroup_limits/fix_cgroup_limits_kabi_6.6.89.patch`，下载后校验 SHA-256，再严格适配本项目固定的 6.6.118 源码。SYSVIPC 的布局方案沿用本仓库已有 `droidspaces_patch/fix_sysvipc_kabi_6_7_8.patch`，适配 6.6.118 的上下文。上述源码修改由 `scripts/prepare-op13-docker.py` 执行；上下文或校验和不符立即失败，不使用 `patch || true`。

编译后验证 fragment 中每个选项真正保留为内建 `y`；随后对比实机 31 个关键结构的大小、原字段偏移/位域宽度及既有 cgroup ID，并严格核验 stock 模块需要的内核符号 CRC 是否与新内核导出表一致。保留 CONFIG_MODVERSIONS、SELinux、厂商 hook 和原有调度配置。不改为 permissive，不关闭模块版本检查，也不依赖强制加载厂商模块。

这不是完整的 KMI 形式证明：检查没有穷举所有类型、未采集模块的符号 CRC、厂商对保留槽的非标准使用或运行时行为。它降低已知布局风险，不能替代实机开机和 Docker 功能测试。

## 功能范围

`configs/oneplus13-docker.fragment` 包含：

- PID/IPC/UTS/USER/NET 命名空间、SysV IPC、POSIX 消息队列。
- cgroup v1/v2、CPU 配额、cpuset、memory、pids、devices、freezer、块设备限速、BPF device filtering。
- seccomp、keys、overlayfs、ext4 ACL/xattr、tmpfs、devtmpfs。
- bridge/veth、IPv4/IPv6 NAT、iptables legacy、iptables-nft/nftables、macvlan/ipvlan、VXLAN、IPVS。

这为 rootful Docker、Compose 和相关网络驱动提供内核前提。不是把 Android 自动变成 Linux 发行版，也不保证所有 Docker 插件、rootless、CRIU/checkpoint、AppArmor、实时 CPU 配额、Swarm 的每种部署方式都已经实测。

## 刷入前后

1. 先备份与当前系统完全对应的 boot、init_boot 等启动分区，并准备已验证的恢复方式；本项目不会自动刷入任何分区。不要在没有可用备份和恢复条件时测试。
2. 首包默认内置原版 KernelSU v3.3.1，不同时叠加 SUSFS、KPM、额外 LZ4、ADIOS、ReKernel 等改动；关闭 KernelSU 输入时必须自行保留其他 root 方式。
3. 安装器仅接受上表实机型号、固件和正在运行的 6.6.118 内核，适合在已启动的匹配系统中使用 root 内核刷写工具。不匹配会拒绝刷写，不应删除检查强刷其他版本。
4. 刷入并重启后先验证 Android 基本功能、厂商模块、Wi-Fi/蜂窝网络和 root，再测试 Docker。如果不能启动，先恢复备份，保存 pstore/ramoops 日志用于定位，不要反复强刷。

手机需另行准备 root 下的 Linux chroot 和 Docker/containerd/runc 用户态；内核本身不安装 Docker。不要在 Android host 随意重新挂载或重排 cgroup 层级，以免干扰 Android task profiles；为容器服务规划独立 cgroup 子树，按实际 v1/v2 挂载和 SELinux 策略处理权限。不要把关闭 SELinux 当作默认修复。

Docker 数据目录建议使用独立 ext4 文件系统。Android `/data` 的实际 f2fs/加密/挂载特性是否满足 overlay2 仍需实测，不应仅凭 CONFIG_OVERLAY_FS=y 就断言 overlay2 可以工作。Android 网络服务可能重写防火墙规则，需在用户态处理，不修改手机全局规则作为构建的一部分。

在已正确配置 Docker 的 root Linux 环境中检查：

```sh
docker info
docker run --rm hello-world
docker run --rm --cpus=0.5 --memory=128m --pids-limit=64 alpine sh -c 'cat /proc/self/cgroup; echo limits-ok'
docker run --rm alpine nslookup example.com
docker run -d --name docker-port-test -p 18080:80 nginx:alpine
curl http://127.0.0.1:18080
docker rm -f docker-port-test
```

上述命令会下载镜像，仅供刷入并确认系统正常之后自行验证。`docker info` 中应检查实际 storage driver、cgroup driver/version、CPU quota、memory、pids 及设备限制；还需单独验证实际需要的 Compose、IPv6、macvlan/ipvlan 或 Swarm 场景。ABI 检查通过或 Actions 构建成功都不代表已经完成这些实机测试。
