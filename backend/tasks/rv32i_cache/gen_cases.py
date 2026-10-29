"""Deterministic legal transactions, with all expected values from golden_step."""

import json
import random
from pathlib import Path

from golden import golden_step

SEED = 0xCA16
RANDOM_REQUESTS = 240


class Case:
    def __init__(self, name):
        self.name, self.state, self.steps = name, {}, []
        self.step(rst=True)

    def step(self, rst=False, **kw):
        inputs = dict(clk=0, rst_n=int(not rst), cpu_valid=0, cpu_addr=0,
                      cpu_wdata=0, cpu_wstrb=0, mem_ready=0, mem_rdata=0)
        inputs.update(kw)
        self.state, expected = golden_step(self.state, inputs, rst)
        self.steps.append(dict(inputs=inputs, rst=rst, expected=expected))
        return expected

    def request(self, addr, word, wait=0, strobe=0, wdata=0):
        kw = dict(cpu_valid=1, cpu_addr=addr, cpu_wstrb=strobe, cpu_wdata=wdata)
        # Observe the request with ready low before offering any memory response.
        out = self.step(mem_rdata=word ^ 0xFFFFFFFF, **kw)
        if not out["cpu_ready"]:
            for _ in range(wait):
                self.step(mem_rdata=word ^ 0xFFFFFFFF, **kw)
            self.step(mem_ready=1, mem_rdata=word, **kw)
        self.step()

    def json(self):
        return dict(name=self.name, steps=self.steps)


def build():
    cases = []
    c = Case("cold_miss_then_hit")
    c.request(0x1004, 0x12345678)
    c.request(0x1004, 0xDEADBEEF)
    cases.append(c)
    c = Case("conflict_same_index_new_tag")
    for addr, data in [(0x1000, 11), (0x1040, 22), (0x1040, 22), (0x1000, 11)]:
        c.request(addr, data, wait=3)
    cases.append(c)
    c = Case("write_hit_invalidates_after_ack")
    c.request(0x1020, 0x11223344)
    c.request(0x1020, 0x11223344, wait=3, strobe=2, wdata=0xAA00)
    c.request(0x1020, 0x1122AA44, wait=2)
    c.request(0x1020, 0x1122AA44)
    cases.append(c)
    c = Case("write_miss_no_allocate_preserves_other_tag")
    c.request(0x1010, 0x88776655)
    c.request(0x1050, 0, wait=2, strobe=15, wdata=0xABCDEF01)
    c.request(0x1010, 0x88776655)
    c.request(0x1050, 0xABCDEF01)
    c.request(0x100C, 0, strobe=1, wdata=0x99)
    c.request(0x100C, 0x99)
    cases.append(c)
    c = Case("io_never_caches")
    c.request(0x1008, 0x12345678)
    for word in (1, 2, 3):
        c.request(0x80001008, word, wait=3)
    c.request(0x80001008, 3, strobe=15, wdata=9)
    c.request(0x1008, 0x12345678)
    cases.append(c)
    c = Case("all_lines_and_reset")
    for index in range(16):
        c.request(0x1000 + 4 * index, 0xA000 + index)
    for index in range(16):
        c.request(0x1000 + 4 * index, 0xA000 + index)
    c.step(rst=True, cpu_valid=1, cpu_addr=0x1000, mem_ready=1, mem_rdata=0xFFFFFFFF)
    for index in range(16):
        c.request(0x1000 + 4 * index, 0xB000 + index, wait=3)
    cases.append(c)
    c = Case("idle_and_passthrough")
    c.request(0x1000, 0xFACECAFE)
    c.step(cpu_addr=0x1000, mem_rdata=17, cpu_wdata=23, cpu_wstrb=9)
    c.step(cpu_addr=0xFFFFFFFF, mem_rdata=0xDEADBEEF, cpu_wdata=0xABCDEF01, cpu_wstrb=15)
    cases.append(c)
    c = Case("random_legal")
    rng = random.Random(SEED)
    memory = {}
    for _ in range(RANDOM_REQUESTS):
        addr = rng.choice([0, 0x1000, 0x1040, 0x80001000]) + rng.randrange(16) * 4
        word = memory.setdefault(addr, rng.getrandbits(32))
        strobe = rng.choice([0, 0, 0, 1, 2, 3, 4, 8, 12, 15])
        data = rng.getrandbits(32)
        c.request(addr, word, wait=rng.choice([0, 1, 3, 5]), strobe=strobe, wdata=data)
        for lane in range(4):
            if strobe & (1 << lane):
                mask = 255 << (lane * 8)
                word = (word & ~mask) | (data & mask)
        memory[addr] = word
    cases.append(c)
    return [c.json() for c in cases]


if __name__ == "__main__":
    cases = build()
    Path(__file__).with_name("cases.json").write_text(
        json.dumps(dict(cases=cases), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"rv32i_cache cases={len(cases)} random_requests={RANDOM_REQUESTS} seed={SEED:#x}")
