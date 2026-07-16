from __future__ import annotations

import argparse
import struct
from pathlib import Path

from scan_xex_runtime import PEImage


GPR_PREFIX_OPS = {
    7: "mulli",
    8: "subfic",
    10: "cmplwi",
    11: "cmpwi",
    12: "addic",
    13: "addic.",
    14: "addi",
    15: "addis",
    20: "rlwimi",
    21: "rlwinm",
    23: "rlwnm",
    24: "ori",
    25: "oris",
    26: "xori",
    27: "xoris",
    28: "andi.",
    29: "andis.",
    32: "lwz",
    33: "lwzu",
    34: "lbz",
    35: "lbzu",
    36: "stw",
    37: "stwu",
    38: "stb",
    39: "stbu",
    40: "lhz",
    41: "lhzu",
    42: "lha",
    43: "lhau",
    44: "sth",
    45: "sthu",
    46: "lmw",
    47: "stmw",
    48: "lfs",
    50: "lfd",
    52: "stfs",
    54: "stfd",
}


def signed(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value ^ sign) - sign


def decode(word: int, va: int) -> str:
    op = (word >> 26) & 0x3F
    rt = (word >> 21) & 0x1F
    ra = (word >> 16) & 0x1F
    rb = (word >> 11) & 0x1F
    imm = word & 0xFFFF
    simm = signed(imm, 16)

    if word == 0x60000000:
        return "nop"
    if op == 14 and ra == 0:
        return f"li r{rt},{simm}"
    if op == 15:
        return f"lis r{rt},0x{imm:04X}"
    if op in {18, 16}:
        if op == 18:
            li = signed(word & 0x03FFFFFC, 26)
            aa = (word >> 1) & 1
            lk = word & 1
            target = li if aa else va + li
            return f"b{'l' if lk else ''} 0x{target:08X}"
        bd = signed(word & 0xFFFC, 16)
        bo = (word >> 21) & 0x1F
        bi = (word >> 16) & 0x1F
        aa = (word >> 1) & 1
        lk = word & 1
        target = bd if aa else va + bd
        return f"bc bo={bo},bi={bi},0x{target:08X}{',lk' if lk else ''}"
    if op in GPR_PREFIX_OPS:
        name = GPR_PREFIX_OPS[op]
        if name in {"lwz", "lwzu", "lbz", "lbzu", "stw", "stwu", "stb", "stbu", "lhz", "lhzu", "lha", "lhau", "sth", "sthu", "lfs", "lfd", "stfs", "stfd"}:
            return f"{name} r{rt},{simm}(r{ra})"
        if name in {"cmplwi", "cmpwi"}:
            return f"{name} r{ra},{imm if name == 'cmplwi' else simm}"
        if name in {"ori", "oris", "xori", "xoris", "andi.", "andis."}:
            return f"{name} r{rt},r{ra},0x{imm:04X}"
        return f"{name} r{rt},r{ra},{simm}"
    if op == 31:
        xo = (word >> 1) & 0x3FF
        if xo == 266:
            return f"add r{rt},r{ra},r{rb}"
        if xo == 444:
            return f"or r{ra},r{rt},r{rb}"
        if xo == 467:
            return f"mtctr r{rt}"
        if xo == 339:
            return f"mfspr r{rt},spr={((word >> 11) & 0x3FF)}"
        if xo == 491:
            return f"divw r{rt},r{ra},r{rb}"
        if xo == 235:
            return f"mullw r{rt},r{ra},r{rb}"
        if xo == 40:
            return f"subf r{rt},r{ra},r{rb}"
    if op == 19:
        xo = (word >> 1) & 0x3FF
        if xo == 16:
            return "bclr"
        if xo == 528:
            return "bcctr"
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("address", type=lambda s: int(s, 0))
    parser.add_argument("--bytes-before", type=lambda s: int(s, 0), default=0x80)
    parser.add_argument("--bytes-after", type=lambda s: int(s, 0), default=0x100)
    args = parser.parse_args()

    image = PEImage(args.image)
    start_va = args.address - args.bytes_before
    end_va = args.address + args.bytes_after
    start_off = image.rva_to_file(image.va_to_rva(start_va))
    end_off = image.rva_to_file(image.va_to_rva(end_va))
    start_off -= start_off % 4

    for file_off in range(start_off, end_off, 4):
        va = image.file_to_va(file_off)
        word = struct.unpack_from(">I", image.data, file_off)[0]
        marker = "=>" if va == args.address else "  "
        decoded = decode(word, va)
        print(f"{marker} 0x{va:08X}  0x{word:08X}  {decoded}")


if __name__ == "__main__":
    main()
