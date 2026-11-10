import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import time


METADATA = """uname -a
getprop ro.product.model
getprop ro.product.device
getprop ro.build.display.id
getprop ro.boot.slot_suffix
getprop ro.boot.bootreason
getprop sys.boot.reason
getprop sys.boot_completed
cat /proc/sys/kernel/random/boot_id
cat /proc/cmdline
cat /proc/bootconfig
cat /proc/sys/kernel/printk
cat /proc/sys/kernel/panic
cat /proc/sys/kernel/panic_on_oops
cat /proc/modules
cat /proc/mounts
ls -l /sys/fs/pstore
for parameter in /sys/module/ramoops/parameters/*; do
    echo "${parameter##*/}=$(cat "$parameter")"
done
ls -ld /data/vendor/ramdump /data/vendor/ssrdump /data/vendor/pstore
"""


class Collector:
    def __init__(self, arguments):
        self.arguments = arguments
        self.output = arguments.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.serial = None
        self.directory = None
        self.root_mode = "none"
        self.streams = []
        self.session = 0

    def event(self, message):
        line = f"{datetime.now(timezone.utc).isoformat()} {message}"
        print(line, flush=True)
        with (self.output / "collector-events.log").open("a", encoding="utf-8") as output:
            output.write(line + "\n")

    def adb(self, *arguments):
        return [self.arguments.adb, *(["-s", self.serial] if self.serial else []), *arguments]

    def text(self, *arguments, timeout=10):
        try:
            result = subprocess.run(self.adb(*arguments), capture_output=True, timeout=timeout)
            return result.returncode, result.stdout.decode(errors="replace").strip()
        except subprocess.TimeoutExpired:
            return -1, "timeout"

    def remote(self, command, privileged=False):
        if privileged and self.root_mode == "su":
            return "su -c " + shlex.quote(command)
        return command

    def capture(self, filename, command, privileged=False, timeout=25):
        target = self.directory / filename
        with target.open("wb") as output, target.with_name(target.name + ".stderr.txt").open("wb") as errors:
            try:
                result = subprocess.run(self.adb("exec-out", self.remote(command, privileged)),
                                        stdout=output, stderr=errors, timeout=timeout)
                status = result.returncode
            except subprocess.TimeoutExpired:
                status = -1
        with (self.directory / "commands.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"file": filename, "command": command,
                                     "privileged": privileged, "status": status}) + "\n")
        if status:
            self.event(f"Capture incomplete: {filename}, status={status}")
        return status

    def start_stream(self, filename, command, privileged=False):
        output = (self.directory / filename).open("ab")
        try:
            process = subprocess.Popen(self.adb("shell", self.remote(command, privileged)),
                                       stdout=output, stderr=subprocess.STDOUT)
        finally:
            output.close()
        self.streams.append(process)

    def stop_streams(self):
        for process in self.streams:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.streams.clear()

    def backup(self):
        if self.root_mode == "none":
            raise RuntimeError("Boot backup needs an authorized su session or a root recovery shell")
        _, model = self.text("shell", "getprop ro.product.model")
        _, firmware = self.text("shell", "getprop ro.build.display.id")
        _, slot = self.text("shell", "getprop ro.boot.slot_suffix")
        if model != "PJZ110" or firmware != "PJZ110_16.0.10.501(CN01)" or slot != "_a":
            raise RuntimeError("Backup guard: expected this PJZ110 firmware on Android slot A")
        checksums = []
        for name, magic in (("boot", b"ANDROID!"), ("init_boot", b"ANDROID!"),
                            ("vendor_boot", b"VNDRBOOT")):
            block = f"/dev/block/by-name/{name}{slot}"
            status, size = self.text("shell", self.remote(f"blockdev --getsize64 {block}", True))
            if status or not size.isdigit():
                raise RuntimeError(f"Cannot determine partition size: {block}")
            filename = f"{name}{slot}.img"
            status = self.capture(filename + ".partial", f"cat {block}", privileged=True, timeout=180)
            target = self.directory / (filename + ".partial")
            with target.open("rb") as source:
                header = source.read(8)
                source.seek(0)
                digest = hashlib.file_digest(source, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else None
                if digest is None:
                    source.seek(0)
                    checksum = hashlib.sha256()
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        checksum.update(chunk)
                    digest = checksum.hexdigest()
            if status or target.stat().st_size != int(size) or header != magic:
                raise RuntimeError(f"Incomplete or invalid backup; do not use: {target}")
            target.rename(self.directory / filename)
            checksums.append(f"{digest}  {filename}\n")
            self.event(f"Verified read-only backup: {filename}, {size} bytes, sha256={digest}")
        (self.directory / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")

    def snapshot(self):
        self.session += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        self.directory = self.output / f"session-{stamp}-{self.session:03d}"
        self.directory.mkdir()
        self.event(f"Connected; collecting into {self.directory.name}")
        self.root_mode = "none"
        status, identity = self.text("shell", "id -u")
        if status == 0 and identity == "0":
            self.root_mode = "direct"
        else:
            status, identity = self.text("shell", "su -c 'id -u'", timeout=8)
            if status == 0 and identity == "0":
                self.root_mode = "su"
        self.event(f"Root access: {self.root_mode}")
        self.start_stream("logcat-all.txt", "logcat -b all -v threadtime")
        self.capture("pstore.tar", "tar -c -C /sys/fs/pstore .", privileged=True)
        try:
            with tarfile.open(self.directory / "pstore.tar") as archive:
                records = [{"name": member.name, "size": member.size} for member in archive.getmembers() if member.isfile()]
            (self.directory / "pstore-index.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
            self.event(f"Pstore archive validated; entries={len(records)} (empty does not prove no crash)")
        except (tarfile.TarError, OSError):
            self.event("Pstore unavailable or archive invalid; check root access and pstore mount")
        self.capture("dmesg.txt", "dmesg", privileged=True)
        self.capture("metadata.txt", METADATA, privileged=True)
        self.capture("last-kmsg.txt", "cat /proc/last_kmsg", privileged=True)
        if not self.arguments.once:
            self.start_stream("kernel-live.txt", "dmesg -w", privileged=True)
        self.capture("root-access.txt", "id", privileged=True)
        if self.arguments.backup_boot:
            self.backup()

    def run(self):
        started = time.monotonic()
        active = None
        previous_devices = None
        self.event("Read-only collector started; it never flashes, reboots, changes slots or clears logs")
        try:
            while self.arguments.duration == 0 or time.monotonic() - started < self.arguments.duration:
                self.serial = None
                status, listing = self.text("devices")
                devices = [tuple(line.split()[:2]) for line in listing.splitlines()
                           if len(line.split()) >= 2 and not line.startswith(("List of devices", "*"))]
                if self.arguments.serial:
                    devices = [device for device in devices if device[0] == self.arguments.serial]
                if devices != previous_devices:
                    self.event("ADB states: " + repr(devices))
                    previous_devices = devices
                if not self.arguments.serial and len(devices) > 1:
                    raise RuntimeError("Multiple ADB devices; specify --serial to avoid collecting the wrong phone")
                ready = [device for device in devices if device[1] in ("device", "recovery")]
                if status == 0 and ready:
                    self.serial = ready[0][0]
                    boot_status, boot_id = self.text("shell", "cat /proc/sys/kernel/random/boot_id")
                    identity = (self.serial, boot_id if boot_status == 0 else "unknown")
                    if identity != active:
                        self.stop_streams()
                        self.snapshot()
                        active = identity
                        if self.arguments.once:
                            return
                    elif self.streams and all(process.poll() is not None for process in self.streams):
                        self.stop_streams()
                        self.event("Live log streams ended; retrying after 10 seconds")
                        time.sleep(10)
                        self.start_stream("logcat-all.txt", "logcat -b all -v threadtime")
                        self.start_stream("kernel-live.txt", "dmesg -w", privileged=True)
                elif active is not None:
                    self.stop_streams()
                    active = None
                    self.event("ADB disconnected; waiting without rebooting the phone")
                time.sleep(self.arguments.interval)
        except KeyboardInterrupt:
            self.event("Stopped by user")
        finally:
            self.stop_streams()
        if self.session == 0:
            raise RuntimeError("No accessible ADB session captured; bootloader/EDL/pre-ADB failures cannot be read over ADB")


def main():
    parser = argparse.ArgumentParser(description="Read-only OnePlus 13 Android/recovery boot log collector (Python 3.8+)")
    parser.add_argument("--adb", default="adb", help="Path to Android platform-tools adb")
    parser.add_argument("--serial", help="ADB serial; required when more than one device is attached")
    parser.add_argument("--output", type=Path, default=Path("op13-bootlogs"))
    parser.add_argument("--duration", type=int, default=900, help="Seconds to wait/capture; 0 waits until Ctrl+C")
    parser.add_argument("--interval", type=float, default=2, help="ADB reconnect polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Capture one snapshot, then exit")
    parser.add_argument("--backup-boot", action="store_true", help="With --once, back up current Android A boot/init_boot/vendor_boot to the computer")
    arguments = parser.parse_args()
    if arguments.duration < 0 or arguments.interval <= 0:
        parser.error("duration must be nonnegative and interval must be positive")
    if arguments.backup_boot and not arguments.once:
        parser.error("--backup-boot requires --once")
    if not shutil.which(arguments.adb):
        parser.error("adb not found; install Android SDK Platform-Tools or specify --adb")
    try:
        Collector(arguments).run()
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"Collection failed: {error}\n")


if __name__ == "__main__":
    main()
