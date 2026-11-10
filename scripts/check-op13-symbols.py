import argparse
from pathlib import Path
import struct


def module_symbols(path):
    data = path.read_bytes()
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if header[0][:6] != b"\x7fELF\x02\x01" or header[2] != 183:
        raise ValueError(f"Not a little-endian ARM64 ELF module: {path}")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, header[6] + index * header[11])
                for index in range(header[12])]

    def section_data(section):
        return data[section[4]:section[4] + section[5]]

    def string_at(strings, offset):
        return strings[offset:strings.index(b"\0", offset)].decode()

    section_names = section_data(sections[header[13]])
    versions = {}
    exports = set()
    for section in sections:
        name = string_at(section_names, section[0])
        content = section_data(section)
        if name == "__versions":
            if len(content) % 64:
                raise ValueError(f"Unexpected modversion record size: {path}")
            for offset in range(0, len(content), 64):
                checksum = struct.unpack_from("<Q", content, offset)[0]
                symbol = content[offset + 8:offset + 64].split(b"\0", 1)[0].decode()
                versions[symbol] = checksum & 0xFFFFFFFF
        elif section[1] == 2:
            strings = section_data(sections[section[6]])
            for offset in range(0, len(content), section[9]):
                name_offset = struct.unpack_from("<I", content, offset)[0]
                symbol = string_at(strings, name_offset)
                if symbol.startswith("__ksymtab_"):
                    exports.add(symbol.removeprefix("__ksymtab_"))
    if not versions:
        raise ValueError(f"No module version table in {path}")
    if not exports and not any(section[1] == 2 for section in sections):
        raise ValueError(f"Cannot identify module-provided symbols: {path}")
    return versions, exports


def snapshot(directories, output):
    paths = {path.resolve() for directory in directories for path in directory.rglob("*.ko")}
    if not paths:
        raise ValueError("No modules found")
    versions = {}
    exports = set()
    for path in sorted(paths):
        imported, provided = module_symbols(path)
        exports.update(provided)
        for symbol, checksum in imported.items():
            if symbol in versions and versions[symbol] != checksum:
                raise ValueError(f"Conflicting stock CRC for {symbol}: {path}")
            versions[symbol] = checksum
    required = {name: checksum for name, checksum in versions.items() if name not in exports}
    output.write_text("".join(f"0x{checksum:08x}\t{name}\n" for name, checksum in sorted(required.items())))
    print(f"Recorded {len(required)} kernel symbol CRCs imported by {len(paths)} stock modules")


def check(expected, symvers):
    baseline = {}
    for line in expected.read_text().splitlines():
        checksum, name = line.split()
        baseline[name] = int(checksum, 16)
    current = {}
    for line in symvers.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[2] == "vmlinux":
            current[fields[1]] = int(fields[0], 16)
    if not baseline or not current:
        raise ValueError("Empty symbol version manifest")
    errors = []
    for symbol, checksum in baseline.items():
        actual = current.get(symbol)
        if actual is None:
            errors.append(f"{symbol}: kernel export missing")
        elif checksum != actual:
            errors.append(f"{symbol}: stock=0x{checksum:08x}, built=0x{actual:08x}")
    if errors:
        raise ValueError("Stock module CRC mismatch; do not flash:\n" + "\n".join(errors))
    print(f"All {len(baseline)} kernel symbol CRCs required by stock modules match")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot")
    capture.add_argument("output", type=Path)
    capture.add_argument("directories", type=Path, nargs="+")
    verify = commands.add_parser("check")
    verify.add_argument("baseline", type=Path)
    verify.add_argument("symvers", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "snapshot":
        snapshot(arguments.directories, arguments.output)
    else:
        check(arguments.baseline, arguments.symvers)


if __name__ == "__main__":
    main()
