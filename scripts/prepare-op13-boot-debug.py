import argparse
from pathlib import Path


MARKER = b"OP13-DIAG:"


def prepare(kernel):
    path = kernel / "init/main.c"
    text = path.read_text()
    if MARKER.decode() in text:
        raise ValueError("Boot diagnostics are already applied")
    for function in ("sched_init", "cgroup_init",
                     "do_pre_smp_initcalls", "do_basic_setup"):
        original = f"\t{function}();"
        if text.count(original) != 1:
            raise ValueError(f"Unexpected boot checkpoint: {function}")
        text = text.replace(original, f'\tpr_notice("OP13-DIAG: {function} begin\\n");\n'
                            + original
                            + f'\n\tpr_notice("OP13-DIAG: {function} done\\n");')
    replacements = {
        '\tpr_notice("%s", linux_banner);':
            '\tpr_notice("%s", linux_banner);\n'
            '\tpr_notice("OP13-DIAG: start_kernel, cgroup_init_early done\\n");',
        "\tret = fn();": '\tpr_notice("OP13-DIAG: initcall %ps begin\\n", fn);\n'
                         '\tret = fn();\n'
                         '\tpr_notice("OP13-DIAG: initcall %ps ret=%d\\n", fn, ret);',
        "\targv_init[0] = init_filename;":
            '\tpr_notice("OP13-DIAG: exec init %s\\n", init_filename);\n'
            '\targv_init[0] = init_filename;',
    }
    for original, replacement in replacements.items():
        if text.count(original) != 1:
            raise ValueError(f"Unexpected init/main.c context: {original}")
        text = text.replace(original, replacement)
    path.write_text(text)
    print("Enabled temporary boot checkpoints and initcall result logging")


def check(image, flavor):
    present = MARKER in image.read_bytes()
    if present != (flavor == "Debug"):
        raise ValueError(f"Boot diagnostic marker does not match {flavor} Image")
    print(f"{flavor} Image temporary boot diagnostic marker verified: {present}")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("kernel", type=Path)
    verify = commands.add_parser("check")
    verify.add_argument("image", type=Path)
    verify.add_argument("flavor", choices=("Debug", "Release"))
    arguments = parser.parse_args()
    if arguments.command == "apply":
        prepare(arguments.kernel)
    else:
        check(arguments.image, arguments.flavor)


if __name__ == "__main__":
    main()
