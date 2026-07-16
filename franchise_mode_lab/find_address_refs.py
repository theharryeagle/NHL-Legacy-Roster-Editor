from __future__ import annotations

import argparse
from pathlib import Path

from scan_xex_runtime import PEImage, find_direct_be32, find_lis_low_pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("addresses", nargs="+", type=lambda s: int(s, 0))
    args = parser.parse_args()

    image = PEImage(args.image)
    text_sec, text = image.section_bytes(".text")
    rdata_sec, rdata = image.section_bytes(".rdata")

    for address in args.addresses:
        direct_text = [image.rva_to_va(text_sec.va + off) for off in find_direct_be32(text, address)]
        direct_rdata = [image.rva_to_va(rdata_sec.va + off) for off in find_direct_be32(rdata, address)]
        lis_pairs = find_lis_low_pairs(text, address)
        print(f"0x{address:08X}")
        print(f"  direct_text={len(direct_text)} direct_rdata={len(direct_rdata)} lis={len(lis_pairs)}")
        for ref in direct_text[:20]:
            print(f"    direct_text 0x{ref:08X}")
        for ref in direct_rdata[:20]:
            print(f"    direct_rdata 0x{ref:08X}")
        for pair in lis_pairs[:20]:
            lis_va = image.rva_to_va(text_sec.va + int(pair["lis_text_offset"]))
            second_va = image.rva_to_va(text_sec.va + int(pair["second_text_offset"]))
            print(
                f"    {pair['kind']} r{pair['register']} "
                f"0x{lis_va:08X}->0x{second_va:08X} gap={pair['lookahead_words']}"
            )


if __name__ == "__main__":
    main()
