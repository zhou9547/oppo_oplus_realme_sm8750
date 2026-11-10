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

工作流为 `.github/workflows/oneplus13-docker.yml`，手动触发后产物位于 Actions 的 `OnePlus13-6.6.118-Docker-AnyKernel3` 工件内。下载工件后，刷入内部的 `AnyKernel3-*.zip`，不要刷 GitHub 外层的工件打包 ZIP。

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
- conntrack zone 放入 `nf_conn` 的 KABI 2 保留空间。

CFS/PIDS 的基础补丁固定为 UlasuNoka/curio-kernel-patches 提交 `9641ce0540cac0017d5373018e1352183df56827` 的 `common/droidspaces/cgroup_limits/fix_cgroup_limits_kabi_6.6.89.patch`，下载后校验 SHA-256，再严格适配本项目固定的 6.6.118 源码。SYSVIPC 的布局方案沿用本仓库已有 `droidspaces_patch/fix_sysvipc_kabi_6_7_8.patch`，适配 6.6.118 的上下文。上述源码修改由 `scripts/prepare-op13-docker.py` 执行；上下文或校验和不符立即失败，不使用 `patch || true`。

编译后验证 fragment 中每个选项真正保留为内建 `y`；随后对比实机 28 个关键结构的大小、原字段偏移/位域宽度及既有 cgroup ID，并严格核验 stock 模块需要的内核符号 CRC 是否与新内核导出表一致。保留 CONFIG_MODVERSIONS、SELinux、厂商 hook 和原有调度配置。不改为 permissive，不关闭模块版本检查，也不依赖强制加载厂商模块。

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
