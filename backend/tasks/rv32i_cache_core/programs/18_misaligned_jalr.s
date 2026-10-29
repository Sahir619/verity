# JALR clears only bit 0 of the target. auipc at pc 4, plus 15, is 19; with bit 0 cleared the target is 18.
# Bit 1 is still set, so the core stops without retiring, without writing ra and without the later store.
# expect 0x1000 = 1
# expect 0x1004 = 0
    lui t0, 1
    auipc t1, 0
    addi t1, t1, 15
    addi a0, x0, 1
    sw a0, 0(t0)
    jalr ra, t1, 0
    addi a0, x0, 2
    sw a0, 4(t0)
    ebreak
