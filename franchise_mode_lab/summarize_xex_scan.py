from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan", type=Path)
    args = parser.parse_args()

    report = json.loads(args.scan.read_text(encoding="utf-8"))
    print("Anchors")
    for anchor in report["anchors"]:
        print(
            f"- {anchor['text']} "
            f"string_va=0x{anchor['string_va']:08X} "
            f"file=0x{anchor['string_file_offset']:X} "
            f"direct_text={len(anchor['direct_text_refs'])} "
            f"direct_rdata={len(anchor['direct_rdata_refs'])} "
            f"lis={len(anchor['lis_low_refs'])}"
        )
        for ref in anchor["direct_text_refs"][:5]:
            print(f"  direct_text 0x{ref:08X}")
        for ref in anchor["direct_rdata_refs"][:5]:
            print(f"  direct_rdata 0x{ref:08X}")
        for ref in anchor.get("descriptor_refs", [])[:8]:
            words = " ".join(f"0x{word:08X}" for word in ref["descriptor_words_be"])
            print(
                f"  descriptor 0x{ref['descriptor_va']:08X} "
                f"direct_text={len(ref['direct_text_refs'])} "
                f"lis={len(ref['lis_low_refs'])} "
                f"words={words}"
            )
            for text_ref in ref["direct_text_refs"][:5]:
                print(f"    descriptor_direct_text 0x{text_ref:08X}")
            for lis_ref in ref["lis_low_refs"][:5]:
                print(
                    f"    descriptor_{lis_ref['kind']} r{lis_ref['register']} "
                    f"0x{lis_ref['lis_va']:08X}->0x{lis_ref['second_va']:08X} "
                    f"gap={lis_ref['lookahead_words']}"
                )
        for ref in anchor["lis_low_refs"][:8]:
            print(
                f"  {ref['kind']} r{ref['register']} "
                f"0x{ref['lis_va']:08X}->0x{ref['second_va']:08X} "
                f"gap={ref['lookahead_words']}"
            )

    print()
    print("Ranked constants")
    for hit in report["ranked_constant_hits_near_anchors"]:
        print(
            f"- va=0x{hit['va']:08X} "
            f"file=0x{hit['file_offset']:X} "
            f"word=0x{hit['word']:08X} "
            f"dist=0x{hit['nearest_anchor_distance']:X} "
            f"{', '.join(hit['notes'])}"
        )


if __name__ == "__main__":
    main()
