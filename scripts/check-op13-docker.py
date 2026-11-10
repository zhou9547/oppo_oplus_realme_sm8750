import argparse
import json
from pathlib import Path
import struct


STRUCTURES = (
    "task_struct", "signal_struct", "sched_entity", "task_group", "cfs_rq", "rq",
    "css_set", "cgroup", "cgroup_root", "cgroup_subsys_state", "cred", "nsproxy",
    "net", "netns_nf", "netns_ct", "nf_conn", "sock", "sock_common", "net_device",
    "sk_buff", "inet_sock", "inet_connection_sock", "request_sock", "file", "inode",
    "super_block", "user_struct", "sock_cgroup_data", "netns_ipv4", "skb_ext",
    "nf_queue_entry",
)


def read_btf(path):
    data = path.read_bytes()
    magic, version, flags, header_size, type_offset, type_size, string_offset, string_size = struct.unpack_from("<HBBIIIII", data)
    if magic != 0xEB9F or version != 1 or flags:
        raise ValueError("Expected raw little-endian BTF v1")
    strings = data[header_size + string_offset:header_size + string_offset + string_size]

    def name_at(offset):
        return strings[offset:strings.index(b"\0", offset)].decode()

    types = [None]
    position = header_size + type_offset
    finish = position + type_size
    while position < finish:
        name_offset, info, size_type = struct.unpack_from("<III", data, position)
        position += 12
        kind = (info >> 24) & 31
        count = info & 65535
        kind_flag = info >> 31
        entry = {"kind": kind, "name": name_at(name_offset), "size_type": size_type}
        if kind in (4, 5):
            members = []
            for index in range(count):
                member_name, member_type, offset = struct.unpack_from("<III", data, position + index * 12)
                members.append((name_at(member_name), member_type,
                                offset & 0xFFFFFF if kind_flag else offset,
                                offset >> 24 if kind_flag else 0))
            entry["members"] = members
            length = count * 12
        elif kind == 6:
            entry["values"] = {name_at(struct.unpack_from("<I", data, position + index * 8)[0]):
                               struct.unpack_from("<i", data, position + index * 8 + 4)[0]
                               for index in range(count)}
            length = count * 8
        elif kind in (1, 14, 17):
            length = 4
        elif kind == 3:
            length = 12
        elif kind in (13,):
            length = count * 8
        elif kind in (15, 19):
            length = count * 12
        elif kind in (2, 7, 8, 9, 10, 11, 12, 16, 18):
            length = 0
        else:
            raise ValueError(f"Unsupported BTF kind {kind}")
        types.append(entry)
        position += length
    if position != finish:
        raise ValueError("Invalid BTF type section length")

    def flatten(type_id, base_offset=0):
        result = {}
        entry = types[type_id]
        if entry["kind"] not in (4, 5):
            return result
        for member_name, member_type, offset, bit_size in entry["members"]:
            if member_name:
                if not member_name.startswith("__UNIQUE_ID"):
                    result[member_name] = f"{base_offset + offset}:{bit_size}"
            else:
                result.update(flatten(member_type, base_offset + offset))
        return result

    result = {}
    for type_id, entry in enumerate(types[1:], 1):
        if entry["kind"] == 4 and entry["name"] in STRUCTURES:
            result[entry["name"]] = {"size": entry["size_type"], "members": flatten(type_id)}
        elif entry["kind"] == 6 and entry["name"] == "cgroup_subsys_id":
            result["cgroup_subsys_id"] = entry["values"]
    missing = set(STRUCTURES) - result.keys()
    if missing or "cgroup_subsys_id" not in result:
        raise ValueError(f"Missing BTF ABI types: {sorted(missing)}")
    return result


def check_abi(expected, actual):
    errors = []
    for name, original in expected.items():
        current = actual[name]
        if name == "cgroup_subsys_id":
            if original != current:
                errors.append(f"cgroup subsystem IDs changed: {original} != {current}")
            continue
        if original["size"] != current["size"]:
            errors.append(f"{name}: size {original['size']} -> {current['size']}")
        for member, offset in original["members"].items():
            if current["members"].get(member) != offset:
                errors.append(f"{name}.{member}: {offset} -> {current['members'].get(member)}")
    if errors:
        raise ValueError("KMI layout check failed; do not flash:\n" + "\n".join(errors))
    print(f"KMI layouts match the running PJZ110 kernel ({len(STRUCTURES)} structures)")


def check_config(config, fragment):
    current = {}
    for line in config.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            current[key] = value
    errors = []
    for line in fragment.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            if current.get(key) != value:
                errors.append(f"{key}: expected {value}, got {current.get(key, 'unset')}")
    if current.get("CONFIG_MODVERSIONS") != "y":
        errors.append("CONFIG_MODVERSIONS must remain enabled")
    required = {
        "CONFIG_MODULE_SIG": "y",
        "CONFIG_MODULE_SIG_PROTECT": "y",
        "CONFIG_SYSTEM_TRUSTED_KEYRING": "y",
        "CONFIG_SYSTEM_TRUSTED_KEYS": '"certs/oneplus13-stock-module-cert.pem"',
        "CONFIG_CFI_CLANG": "y",
        "CONFIG_SECURITY_SELINUX": "y",
    }
    for key, value in required.items():
        if current.get(key) != value:
            errors.append(f"{key}: expected {value}, got {current.get(key, 'unset')}")
    if errors:
        raise ValueError("Docker configuration check failed:\n" + "\n".join(errors))
    print("All requested Docker options survive Kconfig dependency resolution")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("btf", type=Path)
    snapshot.add_argument("output", type=Path)
    abi = commands.add_parser("abi")
    abi.add_argument("baseline", type=Path)
    abi.add_argument("btf", type=Path)
    config = commands.add_parser("config")
    config.add_argument("config", type=Path)
    config.add_argument("fragment", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "snapshot":
        arguments.output.write_text(json.dumps(read_btf(arguments.btf), indent=2, sort_keys=True) + "\n")
    elif arguments.command == "abi":
        check_abi(json.loads(arguments.baseline.read_text()), read_btf(arguments.btf))
    else:
        check_config(arguments.config, arguments.fragment)


if __name__ == "__main__":
    main()
