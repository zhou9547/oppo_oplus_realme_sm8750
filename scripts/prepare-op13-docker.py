import argparse
import hashlib
from pathlib import Path
import subprocess


PATCH_SHA256 = "1776e9229f228f06dd4d01b4ff14afeb6fd4fd268f55c20cf5e6df5e5f6e2868"


def replace(path, old, new, count=1):
    contents = path.read_text()
    actual = contents.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} matches, found {actual}: {old!r}")
    path.write_text(contents.replace(old, new))


def prepare(kernel, limits_patch):
    if hashlib.sha256(limits_patch.read_bytes()).hexdigest() != PATCH_SHA256:
        raise RuntimeError("Unexpected cgroup limits patch checksum")
    command = ["git", "apply", "--exclude=include/linux/cgroup-defs.h"]
    subprocess.run([*command, "--check", str(limits_patch)], cwd=kernel, check=True)
    subprocess.run([*command, str(limits_patch)], cwd=kernel, check=True)
    scheduler = kernel / "include/linux/sched.h"
    replace(scheduler, "#ifdef CONFIG_SYSVIPC\n\tstruct sysv_sem\t\t\tsysvsem;\n"
            "\tstruct sysv_shm\t\t\tsysvshm;\n#endif\n", "")
    replace(scheduler, "\tANDROID_KABI_RESERVE(6);\n\tANDROID_KABI_RESERVE(7);\n"
            "\tANDROID_KABI_RESERVE(8);", """#ifdef CONFIG_SYSVIPC
	ANDROID_KABI_USE(6, struct sysv_sem sysvsem);
	_ANDROID_KABI_REPLACE(ANDROID_KABI_RESERVE(7); ANDROID_KABI_RESERVE(8), struct sysv_shm sysvshm);
#else
	ANDROID_KABI_RESERVE(6);
	ANDROID_KABI_RESERVE(7);
	ANDROID_KABI_RESERVE(8);
#endif""")

    definitions = kernel / "include/linux/cgroup-defs.h"
    replace(definitions, "enum cgroup_subsys_id {", "#define CGROUP_SUBSYS_KABI\nenum cgroup_subsys_id {")
    replace(definitions, "#undef SUBSYS\n", """#undef CGROUP_SUBSYS_KABI
#undef SUBSYS

#if !defined(CONFIG_CGROUP_PIDS) || !defined(CONFIG_CGROUP_DEVICE) || !defined(CONFIG_CGROUP_NET_CLASSID)
#error OnePlus Docker KMI patch requires PIDS, DEVICE and NET_CLASSID controllers
#endif
enum {
    pids_cgrp_id = CGROUP_SUBSYS_COUNT,
    devices_cgrp_id = CGROUP_SUBSYS_COUNT + 1,
    net_cls_cgrp_id = CGROUP_SUBSYS_COUNT + 2,
};
#define CGROUP_SUBSYS_COUNT_ACTIVE (CGROUP_SUBSYS_COUNT + 3)
""")
    subsystems = kernel / "include/linux/cgroup_subsys.h"
    replace(subsystems, "#if IS_ENABLED(CONFIG_CGROUP_DEVICE)\nSUBSYS(devices)\n#endif\n", "")
    replace(subsystems, "#if IS_ENABLED(CONFIG_CGROUP_NET_CLASSID)\nSUBSYS(net_cls)\n#endif\n", "")
    replace(subsystems, "SUBSYS(pids)\n#endif", "SUBSYS(pids)\nSUBSYS(devices)\nSUBSYS(net_cls)\n#endif")
    replace(definitions, "#ifdef CONFIG_CGROUP_NET_CLASSID\n\tu32\t\tclassid; /* v1 */\n#endif\n", "")
    replace(definitions, "\tu16\t\tprioidx; /* v1 */\n#endif\n};", """	u16		prioidx; /* v1 */
#endif
#if defined(CONFIG_CGROUP_NET_CLASSID) && !defined(__GENKSYMS__)
	u32		classid;
#endif
};""")

    public = kernel / "include/linux/cgroup.h"
    replace(public, "struct cgroup_subsys_state *css_set_pids(struct css_set *cset);",
            "struct cgroup_subsys_state *css_set_pids(struct css_set *cset);\n"
            "struct cgroup_subsys_state *css_set_devices(struct css_set *cset);\n"
            "struct cgroup_subsys_state *css_set_net_cls(struct css_set *cset);")
    replace(public, "\t\treturn css_set_pids(cset);", "\t\treturn css_set_pids(cset);\n"
            "\tif (ssid == devices_cgrp_id)\n\t\treturn css_set_devices(cset);\n"
            "\tif (ssid == net_cls_cgrp_id)\n\t\treturn css_set_net_cls(cset);")
    internal = kernel / "kernel/cgroup/cgroup-internal.h"
    replace(internal, "#define CGROUP_PIDS_KABI_SIZE sizeof(struct cgroup_pids_kabi)",
            "#define CGROUP_PIDS_KABI_SIZE (3 * sizeof(struct cgroup_pids_kabi))")
    core = kernel / "kernel/cgroup/cgroup.c"
    replace(core, "static struct css_set_pids_kabi init_css_set_pids;",
            "static struct css_set_pids_kabi init_css_set_pids[3];")
    replace(core, "static struct cgroup_pids_kabi cgrp_dfl_root_pids;",
            "static struct cgroup_pids_kabi cgrp_dfl_root_pids[3];")
    replace(core, "return &init_css_set_pids;", "return init_css_set_pids;")
    replace(core, "return &cgrp_dfl_root_pids;", "return cgrp_dfl_root_pids;")
    replace(core, "EXPORT_SYMBOL_GPL(css_set_pids);", """EXPORT_SYMBOL_GPL(css_set_pids);

struct cgroup_subsys_state *css_set_devices(struct css_set *cset)
{
	return css_set_pids_storage(cset)[1].subsys;
}
EXPORT_SYMBOL_GPL(css_set_devices);

struct cgroup_subsys_state *css_set_net_cls(struct css_set *cset)
{
	return css_set_pids_storage(cset)[2].subsys;
}
EXPORT_SYMBOL_GPL(css_set_net_cls);""")
    replace(core, "#define CSS_SET_PIDS_KABI_SIZE sizeof(struct css_set_pids_kabi)",
            "#define CSS_SET_PIDS_KABI_SIZE (3 * sizeof(struct css_set_pids_kabi))")
    for storage, member in (("css_set_pids_storage(cset)", "subsys"),
                            ("cgroup_pids_storage(cgrp)", "subsys"),
                            ("css_set_pids_storage(cset)", "e_cset_node"),
                            ("cgroup_pids_storage(cgrp)", "e_csets")):
        replace(core, f"if (ssid == pids_cgrp_id)\n\t\treturn &{storage}->{member};",
                f"if (ssid >= CGROUP_SUBSYS_COUNT)\n"
                f"\t\treturn &{storage}[ssid - CGROUP_SUBSYS_COUNT].{member};")
    replace(core, "if (ssid == pids_cgrp_id) {", "if (ssid >= CGROUP_SUBSYS_COUNT) {")
    replace(core, "pids = container_of(node, struct css_set_pids_kabi, e_cset_node);",
            "pids = container_of(node, struct css_set_pids_kabi, e_cset_node);\n"
            "\t\tpids -= ssid - CGROUP_SUBSYS_COUNT;")
    replace(core, "if (pids == &init_css_set_pids)", "if (pids == init_css_set_pids)")

    nf_namespace = kernel / "include/net/netns/netfilter.h"
    replace(nf_namespace, "#ifdef CONFIG_NETFILTER_FAMILY_BRIDGE\n"
            "\tstruct nf_hook_entries __rcu *hooks_bridge[NF_INET_NUMHOOKS];\n#endif\n", "")
    nf_header = kernel / "include/linux/netfilter.h"
    replace(nf_header, "static inline int NF_DROP_GETERR", """#ifdef CONFIG_NETFILTER_FAMILY_BRIDGE
typedef struct nf_hook_entries __rcu *nf_bridge_hook_array[NF_INET_NUMHOOKS];
nf_bridge_hook_array *nf_bridge_hooks_get(const struct net *net);
#define nf_bridge_hooks(net) (*nf_bridge_hooks_get(net))
#endif

static inline int NF_DROP_GETERR""")
    bridge_files = ["include/linux/netfilter.h", "net/bridge/br_input.c",
                    "net/bridge/br_netfilter_hooks.c", "net/netfilter/core.c",
                    "net/netfilter/nf_queue.c", "net/netfilter/nfnetlink_hook.c"]
    for filename in bridge_files:
        path = kernel / filename
        occurrences = path.read_text().count("net->nf.hooks_bridge")
        if not occurrences:
            raise RuntimeError(f"Missing bridge hooks in {filename}")
        replace(path, "net->nf.hooks_bridge", "nf_bridge_hooks(net)", occurrences)
    nf_core = kernel / "net/netfilter/core.c"
    replace(nf_core, "#include <net/net_namespace.h>",
            "#include <net/net_namespace.h>\n#ifndef __GENKSYMS__\n#include <net/netns/generic.h>\n#endif")
    replace(nf_core, "static int __net_init netfilter_net_init", """#ifdef CONFIG_NETFILTER_FAMILY_BRIDGE
static unsigned int nf_bridge_net_id;

nf_bridge_hook_array *nf_bridge_hooks_get(const struct net *net)
{
	return net_generic(net, nf_bridge_net_id);
}
EXPORT_SYMBOL_GPL(nf_bridge_hooks_get);
#endif

static int __net_init netfilter_net_init""")
    replace(nf_core, "static struct pernet_operations netfilter_net_ops = {", """static struct pernet_operations netfilter_net_ops = {
#ifdef CONFIG_NETFILTER_FAMILY_BRIDGE
	.id = &nf_bridge_net_id,
	.size = sizeof(nf_bridge_hook_array),
#endif""")

    queue_header = kernel / "include/net/netfilter/nf_queue.h"
    replace(queue_header, "#if IS_ENABLED(CONFIG_BRIDGE_NETFILTER)\n"
            "\tstruct net_device\t*physin;\n\tstruct net_device\t*physout;\n#endif\n", "")
    queue_core = kernel / "net/netfilter/nf_queue.c"
    replace(queue_core, "static const struct nf_queue_handler __rcu *nf_queue_handler;", """#if IS_ENABLED(CONFIG_BRIDGE_NETFILTER)
struct nf_queue_bridge_devices {
	struct net_device *physin;
	struct net_device *physout;
};

static struct nf_queue_bridge_devices *nf_queue_bridge(struct nf_queue_entry *entry)
{
	return (void *)entry + entry->size - sizeof(struct nf_queue_bridge_devices);
}

#define NF_QUEUE_ENTRY_SIZE(route_size) \\
	(ALIGN(sizeof(struct nf_queue_entry) + (route_size), \\
	       __alignof__(struct nf_queue_bridge_devices)) + \\
	 sizeof(struct nf_queue_bridge_devices))
#else
#define NF_QUEUE_ENTRY_SIZE(route_size) (sizeof(struct nf_queue_entry) + (route_size))
#endif

static const struct nf_queue_handler __rcu *nf_queue_handler;""")
    for field in ("physin", "physout"):
        needle = "entry->" + field
        replace(queue_core, needle, "nf_queue_bridge(entry)->" + field,
                queue_core.read_text().count(needle))
    replace(queue_core, "sizeof(*entry) + route_key_size",
            "NF_QUEUE_ENTRY_SIZE(route_key_size)", 2)

    net_namespace = kernel / "include/net/net_namespace.h"
    replace(net_namespace, "#if defined(CONFIG_NF_TABLES) || defined(CONFIG_NF_TABLES_MODULE)\n"
            "\tstruct netns_nftables\tnft;\n#endif\n", "")
    nft_header = kernel / "include/net/netfilter/nf_tables.h"
    contents = nft_header.read_text()
    start = contents.index("struct nftables_pernet {")
    end = contents.index("static inline u64 nft_net_tstamp", start)
    pernet = contents[start:end]
    replace(nft_header, pernet, "")
    pernet = pernet.replace("\tu8\t\t\tvalidate_state;", "\tu8\t\t\tvalidate_state;\n\tu8\t\t\tgencursor;")
    if "\tgencursor;" not in pernet:
        raise RuntimeError("Could not extend nftables private state")
    replace(nft_header, "static inline unsigned int nft_gencursor_next", pernet + "static inline unsigned int nft_gencursor_next")
    for filename in ("include/net/netfilter/nf_tables.h", "net/netfilter/nf_tables_api.c",
                     "net/netfilter/nf_tables_core.c"):
        path = kernel / filename
        replace(path, "net->nft.gencursor", "nft_pernet(net)->gencursor",
                path.read_text().count("net->nft.gencursor"))

    replace(net_namespace, "#if IS_ENABLED(CONFIG_IP_VS)\n\tstruct netns_ipvs\t*ipvs;\n#endif\n", "")
    ipvs_header = kernel / "include/net/ip_vs.h"
    replace(ipvs_header, "/* Generic access of ipvs struct */", "#include <net/netns/generic.h>\n"
            "extern unsigned int ip_vs_net_id;\n\n/* Generic access of ipvs struct */")
    replace(ipvs_header, "return net->ipvs;", "return net_generic(net, ip_vs_net_id);")
    ipvs_core = kernel / "net/netfilter/ipvs/ip_vs_core.c"
    replace(ipvs_core, "static unsigned int ip_vs_net_id __read_mostly;",
            "unsigned int ip_vs_net_id __read_mostly;\nEXPORT_SYMBOL_GPL(ip_vs_net_id);")
    replace(ipvs_core, "\tnet->ipvs = ipvs;\n", "")
    replace(ipvs_core, "\tnet->ipvs = NULL;\n", "", 2)

    socket_buffer = kernel / "include/linux/skbuff.h"
    replace(socket_buffer, "#if IS_ENABLED(CONFIG_IP_VS)\n\t__u8\t\t\tipvs_property:1;\n#endif\n", "")
    replace(socket_buffer, "#if defined(CONFIG_NET_SCHED) || defined(CONFIG_NET_XGRESS)\n"
            "\t__u16\t\t\ttc_index;", """#if IS_ENABLED(CONFIG_IP_VS) && !defined(__GENKSYMS__)
	__u8			ipvs_property:1;
#endif

#if defined(CONFIG_NET_SCHED) || defined(CONFIG_NET_XGRESS)
	__u16			tc_index;""")
    replace(socket_buffer, "#if IS_ENABLED(CONFIG_BRIDGE_NETFILTER)\n\tSKB_EXT_BRIDGE_NF,\n#endif\n", "")
    replace(socket_buffer, "\tSKB_EXT_NUM, /* must be last */", """	SKB_EXT_NUM,
#if IS_ENABLED(CONFIG_BRIDGE_NETFILTER) && !defined(__GENKSYMS__)
	SKB_EXT_BRIDGE_NF = SKB_EXT_NUM,
#endif""")
    replace(socket_buffer, "\tu8 chunks;\t\t/* same */", """	u8 chunks;
#if IS_ENABLED(CONFIG_BRIDGE_NETFILTER) && !defined(__GENKSYMS__)
	u8 bridge_offset;
#endif""")
    replace(socket_buffer, "struct skb_ext *__skb_ext_alloc(gfp_t flags);", """static inline const u8 *skb_ext_offset_const(const struct skb_ext *ext,
					 enum skb_ext_id id)
{
#if IS_ENABLED(CONFIG_BRIDGE_NETFILTER)
	if (id == SKB_EXT_BRIDGE_NF)
		return &ext->bridge_offset;
#endif
	return &ext->offset[id];
}

static inline u8 *skb_ext_offset(struct skb_ext *ext, enum skb_ext_id id)
{
	return (u8 *)skb_ext_offset_const(ext, id);
}

struct skb_ext *__skb_ext_alloc(gfp_t flags);""")
    replace(socket_buffer, "return !!ext->offset[i];", "return !!*skb_ext_offset_const(ext, i);")
    replace(socket_buffer, "(ext->offset[id] << 3)", "(*skb_ext_offset_const(ext, id) << 3)")
    skb_core = kernel / "net/core/skbuff.c"
    replace(skb_core, "(ext->offset[id] * SKB_EXT_ALIGN_VALUE)",
            "(*skb_ext_offset_const(ext, id) * SKB_EXT_ALIGN_VALUE)")
    replace(skb_core, "ext->offset[id] = newoff;", "*skb_ext_offset(ext, id) = newoff;")
    replace(skb_core, "new->offset[id] = newoff;", "*skb_ext_offset(new, id) = newoff;")
    replace(skb_core, "memset(new->offset, 0, sizeof(new->offset));", """memset(new->offset, 0, sizeof(new->offset));
#if IS_ENABLED(CONFIG_BRIDGE_NETFILTER)
		new->bridge_offset = 0;
#endif""")
    replace(skb_core, "BUILD_BUG_ON(SKB_EXT_NUM >= 8);", "BUILD_BUG_ON(ARRAY_SIZE(skb_ext_type_len) >= 8);")

    ipv4 = kernel / "include/net/netns/ipv4.h"
    for protocol in ("raw", "tcp", "udp"):
        replace(ipv4, f"#ifdef CONFIG_NET_L3_MASTER_DEV\n\tu8 sysctl_{protocol}_l3mdev_accept;\n#endif\n", "")
    replace(ipv4, "\tu8 sysctl_icmp_errors_use_inbound_ifaddr;", """	u8 sysctl_icmp_errors_use_inbound_ifaddr;
#if defined(CONFIG_NET_L3_MASTER_DEV) && !defined(__GENKSYMS__)
	u8 sysctl_raw_l3mdev_accept;
	u8 sysctl_tcp_l3mdev_accept;
	u8 sysctl_udp_l3mdev_accept;
#endif""")

    netdevice = kernel / "include/linux/netdevice.h"
    replace(netdevice, "#ifdef CONFIG_NET_L3_MASTER_DEV\n"
            "\tconst struct l3mdev_ops\t*l3mdev_ops;\n#endif\n", "")
    replace(netdevice, "\tstruct hlist_node\tindex_hlist;", """	struct hlist_node	index_hlist;
#if defined(CONFIG_NET_L3_MASTER_DEV) && !defined(__GENKSYMS__)
	const struct l3mdev_ops	*l3mdev_ops;
#endif""")

    conntrack = kernel / "include/net/netfilter/nf_conntrack.h"
    replace(conntrack, "#ifdef CONFIG_NF_CONNTRACK_ZONES\n\tstruct nf_conntrack_zone zone;\n#endif\n", "")
    replace(conntrack, "\tANDROID_KABI_RESERVE(2);", """#ifdef CONFIG_NF_CONNTRACK_ZONES
	ANDROID_KABI_USE(2, struct nf_conntrack_zone zone);
#else
	ANDROID_KABI_RESERVE(2);
#endif""")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kernel", type=Path)
    parser.add_argument("limits_patch", type=Path)
    arguments = parser.parse_args()
    prepare(arguments.kernel.resolve(), arguments.limits_patch.resolve())
    print("OnePlus 13 Docker KMI adaptations applied")


if __name__ == "__main__":
    main()
