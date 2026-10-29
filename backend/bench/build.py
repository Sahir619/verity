"""Build the pinned Embench subset into little endian RV32I word images."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

BENCHMARKS = ("crc32", "matmult-int", "edn")


def build(source: Path, output: Path) -> None:
    board = Path(__file__).resolve().parent
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in BENCHMARKS:
        elf = output / f"{name}.elf"
        binary = output / f"{name}.bin"
        cmd = [
            "riscv-none-elf-gcc", "-march=rv32i", "-mabi=ilp32", "-O2",
            "-ffreestanding", "-fno-builtin", "-fno-common", "-mstrict-align",
            "-ffunction-sections", "-fdata-sections", "-nostartfiles",
            "-DGLOBAL_SCALE_FACTOR=1", "-DWARMUP_HEAT=0",
            f"-I{source / 'support'}", f"-T{board / 'link.ld'}",
            str(board / "crt0.S"), str(board / "boardsupport.c"),
            str(source / "support/main.c"), str(source / "support/beebsc.c"),
            *map(str, sorted((source / "src" / name).glob("*.c"))),
            "-Wl,--gc-sections", "-Wl,--no-warn-rwx-segments", "--specs=nosys.specs",
            "-o", str(elf),
        ]
        subprocess.run(cmd, check=True)
        subprocess.run(["riscv-none-elf-objcopy", "-O", "binary", str(elf), str(binary)], check=True)
        data = binary.read_bytes()
        if len(data) > 1024 * 1024:
            raise ValueError(f"{name}: image exceeds memory")
        data += bytes(-len(data) % 4)
        image = output / f"{name}.hex"
        image.write_text("".join(f"{int.from_bytes(data[i:i+4], 'little'):08x}\n" for i in range(0, len(data), 4)), encoding="ascii")
        rows.append({"name": name, "image": image.name, "sha256": hashlib.sha256(image.read_bytes()).hexdigest()})
    (output / "suite.json").write_text(json.dumps({"global_scale_factor": 1, "warmup_heat": 0, "benchmarks": rows}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.source, args.output)
