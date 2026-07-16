from __future__ import annotations

import argparse
import dataclasses
import json
import struct
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class Section:
    name: str
    va: int
    vsize: int
    raw: int
    raw_size: int
    chars: int

    @property
    def end_va(self) -> int:
        return self.va + max(self.vsize, self.raw_size)

    @property
    def end_raw(self) -> int:
        return self.raw + self.raw_size


class PEImage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        self.pe_off = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[:2] != b"MZ" or self.data[self.pe_off : self.pe_off + 4] != b"PE\0\0":
            raise ValueError(f"{path} is not an unpacked PE image")

        coff = self.pe_off + 4
        self.machine, self.section_count, _time, _symptr, _symcount, opt_size, _chars = struct.unpack_from(
            "<HHIIIHH", self.data, coff
        )
        opt = coff + 20
        self.opt_magic = struct.unpack_from("<H", self.data, opt)[0]
        if self.opt_magic != 0x10B:
            raise ValueError(f"expected PE32 optional header, got 0x{self.opt_magic:04x}")
        self.entry_rva = struct.unpack_from("<I", self.data, opt + 16)[0]
        self.image_base = struct.unpack_from("<I", self.data, opt + 28)[0]
        self.size_image = struct.unpack_from("<I", self.data, opt + 56)[0]

        sec_off = opt + opt_size
        self.sections: list[Section] = []
        for index in range(self.section_count):
            off = sec_off + index * 40
            raw_name = self.data[off : off + 8].split(b"\0", 1)[0]
            name = raw_name.decode("ascii", "replace")
            vsize, va, raw_size, raw, _reloc, _lineno, _nreloc, _nline, chars = struct.unpack_from(
                "<IIIIIIHHI", self.data, off + 8
            )
            self.sections.append(Section(name, va, vsize, raw, raw_size, chars))

    def section_by_name(self, name: str) -> Section:
        for section in self.sections:
            if section.name == name:
                return section
        raise KeyError(name)

    def va_to_rva(self, va: int) -> int:
        if va < self.image_base:
            raise ValueError(f"VA 0x{va:08x} is below image base 0x{self.image_base:08x}")
        return va - self.image_base

    def rva_to_va(self, rva: int) -> int:
        return self.image_base + rva

    def rva_to_file(self, rva: int) -> int:
        for section in self.sections:
            if section.va <= rva < section.end_va:
                delta = rva - section.va
                if delta >= section.raw_size:
                    raise ValueError(f"RVA 0x{rva:08x} is in virtual-only part of {section.name}")
                return section.raw + delta
        if rva < self.sections[0].raw:
            return rva
        raise ValueError(f"RVA 0x{rva:08x} not mapped to a section")

    def file_to_rva(self, file_off: int) -> int:
        for section in self.sections:
            if section.raw <= file_off < section.end_raw:
                return section.va + (file_off - section.raw)
        if file_off < self.sections[0].raw:
            return file_off
        raise ValueError(f"file offset 0x{file_off:08x} not mapped to a section")

    def file_to_va(self, file_off: int) -> int:
        return self.rva_to_va(self.file_to_rva(file_off))

    def section_bytes(self, name: str) -> tuple[Section, bytes]:
        section = self.section_by_name(name)
        return section, self.data[section.raw : section.raw + section.raw_size]


def ppc_immediate_notes(word: int) -> list[str]:
    op = (word >> 26) & 0x3F
    rt = (word >> 21) & 0x1F
    ra = (word >> 16) & 0x1F
    imm = word & 0xFFFF
    simm = imm - 0x10000 if imm & 0x8000 else imm
    notes: list[str] = []
    if op == 14 and ra == 0:
        notes.append(f"li r{rt},{simm}")
    elif op == 14:
        notes.append(f"addi r{rt},r{ra},{simm}")
    elif op == 15:
        notes.append(f"lis r{rt},0x{imm:04x}")
    elif op == 11:
        notes.append(f"cmpwi r{ra},{simm}")
    elif op == 10:
        notes.append(f"cmplwi r{ra},{imm}")
    elif op == 24:
        notes.append(f"ori r{rt},r{ra},0x{imm:04x}")
    elif op == 25:
        notes.append(f"oris r{rt},r{ra},0x{imm:04x}")
    return notes


def find_ascii(data: bytes, text: str) -> list[int]:
    needle = text.encode("ascii")
    hits: list[int] = []
    start = 0
    while True:
        hit = data.find(needle, start)
        if hit < 0:
            return hits
        hits.append(hit)
        start = hit + 1


def find_direct_be32(haystack: bytes, value: int) -> list[int]:
    needle = struct.pack(">I", value)
    hits: list[int] = []
    start = 0
    while True:
        hit = haystack.find(needle, start)
        if hit < 0:
            return hits
        hits.append(hit)
        start = hit + 1


def find_lis_low_pairs(text_bytes: bytes, target_va: int) -> list[dict[str, int | str]]:
    hi = (target_va >> 16) & 0xFFFF
    lo = target_va & 0xFFFF
    adjusted_hi = ((target_va + 0x8000) >> 16) & 0xFFFF
    results: list[dict[str, int | str]] = []

    words = [struct.unpack_from(">I", text_bytes, off)[0] for off in range(0, len(text_bytes) - 3, 4)]
    for idx, word in enumerate(words):
        op = (word >> 26) & 0x3F
        rt = (word >> 21) & 0x1F
        imm = word & 0xFFFF
        if op != 15 or imm not in {hi, adjusted_hi}:
            continue

        for lookahead in range(1, 9):
            if idx + lookahead >= len(words):
                break
            second = words[idx + lookahead]
            second_op = (second >> 26) & 0x3F
            second_rt = (second >> 21) & 0x1F
            second_ra = (second >> 16) & 0x1F
            second_imm = second & 0xFFFF
            if second_ra != rt or second_imm != lo:
                continue
            if second_op == 14:
                kind = "lis/addi"
            elif second_op == 24 and second_rt == rt:
                kind = "lis/ori"
            else:
                continue
            results.append(
                {
                    "lis_text_offset": idx * 4,
                    "second_text_offset": (idx + lookahead) * 4,
                    "lookahead_words": lookahead,
                    "register": rt,
                    "kind": kind,
                }
            )
    return results


def read_be32_at_va(image: PEImage, va: int, count: int) -> list[int]:
    file_off = image.rva_to_file(image.va_to_rva(va))
    return [struct.unpack_from(">I", image.data, file_off + index * 4)[0] for index in range(count)]


def scan_immediate_constants(image: PEImage, constants: list[int]) -> list[dict[str, int | str | list[str]]]:
    text_sec, text = image.section_bytes(".text")
    out: list[dict[str, int | str | list[str]]] = []
    constant_set = {value & 0xFFFF for value in constants}
    for off in range(0, len(text) - 3, 4):
        word = struct.unpack_from(">I", text, off)[0]
        imm = word & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if imm not in constant_set and simm not in constants:
            continue
        notes = ppc_immediate_notes(word)
        if not notes:
            continue
        va = image.rva_to_va(text_sec.va + off)
        out.append({"va": va, "file_offset": text_sec.raw + off, "word": word, "notes": notes})
    return out


def rank_constant_hits(
    image: PEImage,
    constant_hits: list[dict[str, int | str | list[str]]],
    anchor_vas: list[int],
    window: int,
) -> list[dict[str, int | str | list[str]]]:
    text_sec, text = image.section_bytes(".text")
    anchor_text_offsets: list[int] = []
    for va in anchor_vas:
        for pair in find_lis_low_pairs(text, va):
            anchor_text_offsets.append(int(pair["lis_text_offset"]))
            anchor_text_offsets.append(int(pair["second_text_offset"]))

    ranked: list[dict[str, int | str | list[str]]] = []
    for hit in constant_hits:
        text_off = int(hit["file_offset"]) - text_sec.raw
        distances = [abs(text_off - anchor) for anchor in anchor_text_offsets]
        if not distances:
            continue
        best = min(distances)
        if best <= window:
            item = dict(hit)
            item["nearest_anchor_distance"] = best
            ranked.append(item)
    ranked.sort(key=lambda item: int(item["nearest_anchor_distance"]))
    return ranked


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, default=Path("franchise_mode_lab/working/xex_runtime_scan.json"))
    parser.add_argument("--constants", nargs="*", type=lambda s: int(s, 0), default=[30, 31, 32, 82, 84])
    parser.add_argument(
        "--anchors",
        nargs="*",
        default=[
            "FeLeagueManager::CreateLeague::numTiers",
            "CreateLeague",
            "DynastyStartPoint",
            "NumberOfTeams",
            "NHLSalaryCap",
            "ION_NHLDynasty",
            "GetActiveLeagues",
        ],
    )
    args = parser.parse_args()

    image = PEImage(args.image)
    text_sec, text = image.section_bytes(".text")
    rdata_sec, rdata = image.section_bytes(".rdata")

    anchor_info: list[dict[str, object]] = []
    anchor_vas: list[int] = []
    for anchor in args.anchors:
        for file_off in find_ascii(image.data, anchor):
            va = image.file_to_va(file_off)
            anchor_vas.append(va)
            direct_text_hits = find_direct_be32(text, va)
            direct_rdata_hits = find_direct_be32(rdata, va)
            lis_pairs = find_lis_low_pairs(text, va)
            descriptor_refs = []
            for rdata_hit in direct_rdata_hits[:50]:
                descriptor_va = image.rva_to_va(rdata_sec.va + rdata_hit)
                descriptor_refs.append(
                    {
                        "descriptor_va": descriptor_va,
                        "descriptor_words_be": read_be32_at_va(image, descriptor_va, 6),
                        "direct_text_refs": [
                            image.rva_to_va(text_sec.va + off)
                            for off in find_direct_be32(text, descriptor_va)[:50]
                        ],
                        "lis_low_refs": [
                            {
                                **pair,
                                "lis_va": image.rva_to_va(text_sec.va + int(pair["lis_text_offset"])),
                                "second_va": image.rva_to_va(text_sec.va + int(pair["second_text_offset"])),
                            }
                            for pair in find_lis_low_pairs(text, descriptor_va)[:100]
                        ],
                    }
                )
            anchor_info.append(
                {
                    "text": anchor,
                    "string_file_offset": file_off,
                    "string_va": va,
                    "direct_text_refs": [image.rva_to_va(text_sec.va + off) for off in direct_text_hits[:50]],
                    "direct_rdata_refs": [image.rva_to_va(rdata_sec.va + off) for off in direct_rdata_hits[:50]],
                    "descriptor_refs": descriptor_refs,
                    "lis_low_refs": [
                        {
                            **pair,
                            "lis_va": image.rva_to_va(text_sec.va + int(pair["lis_text_offset"])),
                            "second_va": image.rva_to_va(text_sec.va + int(pair["second_text_offset"])),
                        }
                        for pair in lis_pairs[:100]
                    ],
                }
            )

    constants = scan_immediate_constants(image, args.constants)
    ranked_constants = rank_constant_hits(image, constants, anchor_vas, 0x400)
    report = {
        "image": str(args.image),
        "image_base": image.image_base,
        "entry_va": image.rva_to_va(image.entry_rva),
        "sections": [dataclasses.asdict(section) for section in image.sections],
        "anchors": anchor_info,
        "constant_hit_count": len(constants),
        "ranked_constant_hits_near_anchors": ranked_constants[:300],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"image base: 0x{image.image_base:08X}")
    print(f"entry:      0x{image.rva_to_va(image.entry_rva):08X}")
    print(f"anchors:    {len(anchor_info)}")
    print(f"constants:  {len(constants)}")
    print(f"ranked:     {len(ranked_constants)}")
    print(f"wrote:      {args.out}")


if __name__ == "__main__":
    main()
