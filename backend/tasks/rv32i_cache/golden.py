"""Pure cache model derived from CPU_CONTRACT 12.2, independently of the RTL.

Advance the state once, then compute outputs with the same held inputs.
Tags and words survive reset; only validity is reset.
"""

MASK32 = (1 << 32) - 1


def golden_step(state: dict, inputs: dict, rst: bool) -> tuple[dict, dict]:
    lines = [tuple(line) for line in state.get("lines", [(False, 0, 0)] * 16)]
    addr = int(inputs.get("cpu_addr", 0)) & MASK32
    index, tag = (addr // 4) % 16, addr // 64
    cacheable = addr < (1 << 31)
    request = bool(inputs.get("cpu_valid", 0))
    write = bool(int(inputs.get("cpu_wstrb", 0)) & 15)
    ready = int(bool(inputs.get("mem_ready", 0)))
    word = int(inputs.get("mem_rdata", 0)) & MASK32

    def hits() -> bool:
        valid, saved_tag, _ = lines[index]
        return request and cacheable and not write and valid and saved_tag == tag

    if rst or not inputs.get("rst_n", 1):
        lines = [(False, saved_tag, data) for _, saved_tag, data in lines]
    elif request and not hits() and ready and cacheable:
        valid, saved_tag, data = lines[index]
        if not write:
            lines[index] = (True, tag, word)
        elif valid and saved_tag == tag:
            lines[index] = (False, saved_tag, data)

    hit = hits()
    return {"lines": lines}, {
        "cpu_ready": int(hit or ready),
        "cpu_rdata": lines[index][2] if hit else word,
        "mem_valid": int(request and not hit),
        "mem_addr": addr,
        "mem_wdata": int(inputs.get("cpu_wdata", 0)) & MASK32,
        "mem_wstrb": int(inputs.get("cpu_wstrb", 0)) & 15,
    }
