"""The fixed technique list per live family (plan claim 2.2). Only these ids, with OUR one-line labels, ever reach a
Nemotron role. No web text, no summary text and no user text crosses into a run.

A research summary may name a technique only when its id is in this list for the family AND one of its keywords
appears in a source it cites (checked in code, verity.research.summary). Pipelining is not in the list: pack latency is
fixed and checked by feasibility and EQY. Nemotron still writes every line of Verilog; no code is fetched from the web.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Technique:
    id: str
    label: str  # short name shown in the chat and the plan prompt
    idea: str  # our own one-line description, used in the plan prompt and as the EXPLORE idea
    keywords: tuple[str, ...]  # lowercase; one must appear in a cited title or snippet
    goals: tuple[str, ...] = ()  # the run goals (area | speed | power) this approach usually serves; ranks it first for them


TECHNIQUES: dict[str, tuple[Technique, ...]] = {
    "add8": (
        Technique(
            "ripple_carry",
            "Ripple-carry adder",
            "one full adder per bit; the carry passes from bit to bit",
            ("ripple",),
            ("area", "power"),
        ),
        Technique(
            "carry_lookahead",
            "Carry-lookahead adder",
            "generate and propagate signals compute the carries of a group in parallel",
            ("lookahead", "look-ahead", "carry look ahead"),
            ("speed",),
        ),
        Technique(
            "carry_select",
            "Carry-select adder",
            "each block computes the sum for both carry-in values and a mux picks one when the carry arrives",
            ("carry select", "carry-select"),
            ("speed",),
        ),
        Technique(
            "kogge_stone",
            "Kogge-Stone adder",
            "a parallel prefix tree for the carries with the fewest logic levels",
            ("kogge",),
            ("speed",),
        ),
        Technique(
            "brent_kung",
            "Brent-Kung adder",
            "a parallel prefix tree for the carries with fewer cells and a few more levels",
            ("brent",),
            ("speed",),
        ),
    ),
    "cnt8": (
        Technique(
            "ripple_incrementer",
            "Ripple incrementer",
            "each bit toggles when every lower bit is one, with the all-ones condition chained bit to bit",
            ("ripple",),
            ("area", "power"),
        ),
        Technique(
            "lookahead_incrementer",
            "Lookahead incrementer",
            "the all-ones condition of the lower bits is computed in parallel groups, which splits the carry chain",
            ("lookahead", "look-ahead", "carry chain", "prefix"),
            ("speed",),
        ),
    ),
    "mac4": (
        Technique(
            "booth_radix4",
            "Radix-4 Booth multiplier",
            "recode the multiplier in radix 4 so there are half as many partial products",
            ("booth",),
            ("area", "speed"),
        ),
        Technique(
            "wallace_tree",
            "Wallace tree",
            "add the partial products with layers of carry-save adders and one final adder",
            ("wallace",),
            ("speed",),
        ),
        Technique(
            "dadda_tree",
            "Dadda tree",
            "a carry-save reduction of the partial products that uses as few adders per layer as possible",
            ("dadda",),
            ("speed", "area"),
        ),
        Technique(
            "carry_save_accumulate",
            "Carry-save accumulator",
            "keep the running sum in carry-save form and resolve it with one adder at the end",
            ("carry-save", "carry save"),
            ("speed",),
        ),
    ),
    "fsm_seq": (
        Technique(
            "binary_encoding",
            "Binary state encoding",
            "number the states in binary so the state register has the fewest bits",
            ("binary encod", "state encoding", "binary state"),
            ("area", "power"),
        ),
        Technique(
            "one_hot_encoding",
            "One-hot state encoding",
            "one flip-flop per state, which keeps the next-state logic simple",
            ("one-hot", "one hot"),
            ("speed",),
        ),
        Technique(
            "shift_register_match",
            "Shift-register match",
            "keep the last input bits in a shift register and compare them with the pattern",
            ("shift register", "shift-register"),
            ("speed",),
        ),
    ),
    # CPU ideas are adapted to the fixed CPU block interfaces: no new ports or cycles, no latches or gated clocks.
    "rv32i_imm": (
        # Source: https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html
        Technique(
            "imm_rotated_wiring", "Direct immediate wiring",
            "wire B and J offset bits directly with a constant low zero instead of a variable shift",
            ("rotating bits", "immediate multiplexer", "immediate encoding"), ("area", "speed"),
        ),
        # Source: https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html
        Technique(
            "imm_parallel_sign", "Parallel sign extension",
            "form the sign fill from instr[31] alongside format decoding, masking unsupported opcodes to zero",
            ("sign extension", "sign-extension"), ("speed",),
        ),
        # Source: https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html
        Technique(
            "imm_shared_slices", "Shared immediate slices",
            "share overlapping instruction slices across formats and select only the bits that differ",
            ("maximize overlap", "immediate multiplexer", "immediate types"), ("area", "power"),
        ),
    ),
    "rv32i_dec": (
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_decoder.sv
        Technique(
            "dec_opcode_predecode", "Shared opcode decode",
            "decode each full opcode once and reuse the group predicates across control outputs",
            ("opcode",), ("area", "power"),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_decoder.sv
        Technique(
            "dec_parallel_alu", "Parallel ALU control decode",
            "derive ALU operation and operand selects alongside the main control decode without adding registers",
            ("alu control", "alu operator", "alu decoder"), ("speed",),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_decoder.sv
        Technique(
            "dec_shared_legality", "Shared legality checks",
            "factor common funct3 and funct7 legality predicates and force every non-trap output to zero on illegal inputs",
            ("illegal instruction", "illegal_insn", "instruction legality"), ("area", "power"),
        ),
    ),
    "rv32i_alu": (
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_alu.sv
        Technique(
            "alu_shared_addsub", "Shared arithmetic path",
            "use conditional operand inversion and carry-in for ADD, SUB and signed or unsigned less-than results",
            ("adder", "subtractor"), ("area", "power"),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_alu.sv
        Technique(
            "alu_reversed_shifter", "Shared bidirectional shifter",
            "reverse bits around one combinational right shifter for SLL and select zero or sign fill for SRL and SRA",
            ("bit-reversed", "bit reverse", "reversed", "funnel shifter"), ("area", "power"),
        ),
        # Source: https://pages.hmc.edu/harris/class/e85/DDCArv_Ch5.pdf
        Technique(
            "alu_carry_lookahead", "Grouped carry lookahead",
            "compute carries from grouped generate and propagate signals within the combinational arithmetic path",
            ("carry-lookahead", "carry lookahead", "carry-look-ahead"), ("speed",),
        ),
    ),
    "rv32i_br": (
        # Source: https://raw.githubusercontent.com/YosysHQ/picorv32/main/picorv32.v
        Technique(
            "br_shared_equality", "Shared equality predicate",
            "compute equality once for BEQ and BNE and select its polarity, keeping reserved funct3 values false",
            ("alu_eq", "equality", "equal"), ("area", "power"),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_alu.sv
        Technique(
            "br_shared_signed_compare", "Shared magnitude comparison",
            "reuse a magnitude comparison for signed and unsigned branches with explicit sign-bit correction and polarity",
            ("signed and unsigned", "cmp_signed", "is_greater_equal"), ("area", "power"),
        ),
        # Source: https://raw.githubusercontent.com/YosysHQ/picorv32/main/picorv32.v
        Technique(
            "br_parallel_predicates", "Parallel branch predicates",
            "evaluate equality and less-than predicates in parallel before a shallow funct3 selection with reserved values false",
            ("alu_lts", "alu_ltu", "branch comparison"), ("speed",),
        ),
    ),
    "rv32i_rf": (
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_register_file_ff.sv
        Technique(
            "rf_hardwired_zero", "Omit zero-register storage",
            "store only x1 through x31 in synchronously reset flops and hardwire both x0 reads to zero",
            ("no flops for r0", "hard-wired", "hardwired", "register x0"), ("area", "power"),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_register_file_ff.sv
        Technique(
            "rf_shared_write_decode", "Shared word write enables",
            "decode wa and we once per word and share that enable across its flops, retaining synchronous reset priority",
            ("write decode", "we_a_decoder", "write enable"), ("area", "power"),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_register_file_ff.sv
        Technique(
            "rf_banked_read_mux", "Hierarchical read selection",
            "split each asynchronous read mux into smaller address-selected groups and a final bank select with no added cycle",
            ("bank-select", "bank select", "read mux"), ("speed",),
        ),
    ),
    "rv32i_lsu": (
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_load_store_unit.sv
        Technique(
            "lsu_lane_select", "Narrow load lane selection",
            "select the addressed byte or aligned halfword with narrow muxes instead of a general word shifter",
            ("align bytes and halfwords", "rdata_offset", "byte select"), ("area", "speed"),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_load_store_unit.sv
        Technique(
            "lsu_shared_extension", "Shared load extension",
            "extend the selected byte or halfword with a shared sign-fill path and keep invalid or misaligned loads zero",
            ("sign extension", "sign-extension"), ("area", "power"),
        ),
        # Source: https://raw.githubusercontent.com/lowRISC/ibex/master/rtl/ibex_load_store_unit.sv
        Technique(
            "lsu_size_predecode", "Shared access-size decode",
            "reuse size and low-address predicates for byte enables and alignment checks while preserving replicated sdata",
            ("byte enable", "be generation", "data type"), ("area", "power"),
        ),
    ),
    # Internal recoding must still expose the contract's state[2:0], cycle timing and HALT behavior for 6 and 7.
    "rv32i_ctl": (
        # Source: https://docs.amd.com/r/en-US/ug901-vivado-synthesis/Sequential-State-Encoding
        Technique(
            "ctl_binary_state", "Compact binary states",
            "retain the contract's three-bit state codes and factor next-state equations without changing Mealy outputs",
            ("sequential state", "binary encod", "radix two"), ("area", "power"),
        ),
        # Source: https://docs.amd.com/r/en-US/ug901-vivado-synthesis/One-Hot-State-Encoding
        Technique(
            "ctl_one_hot_state", "Internal one-hot states",
            "use one internal flop per state with combinational mapping to the fixed external codes and unchanged reset and HALT behavior",
            ("one-hot", "one hot"), ("speed",),
        ),
        # Source: https://docs.amd.com/r/en-US/ug901-vivado-synthesis/Gray-State-Encoding
        Technique(
            "ctl_gray_state", "Internal Gray states",
            "choose internal Gray codes to reduce toggles on common transitions and map back to the fixed external state codes and HALT behavior",
            ("gray",), ("power",),
        ),
    ),
}

_BY_ID: dict[str, Technique] = {t.id: t for family in TECHNIQUES.values() for t in family}


def techniques_for(family: str | None) -> tuple[Technique, ...]:
    return TECHNIQUES.get(family or "", ())


def technique(technique_id: str) -> Technique | None:
    return _BY_ID.get(technique_id)


def valid_ids(family: str | None, ids: list[str]) -> list[str]:
    """Keep only ids from this family's fixed list, in the order given, without duplicates."""
    allowed = {t.id for t in techniques_for(family)}
    out: list[str] = []
    for tid in ids:
        if tid in allowed and tid not in out:
            out.append(tid)
    return out


def mentioned_in(family: str | None, text: str) -> set[str]:
    """Ids of this family whose keywords appear in `text` (used to guess which technique a plan already uses)."""
    low = text.lower()
    return {t.id for t in techniques_for(family) if any(k in low for k in t.keywords)}
