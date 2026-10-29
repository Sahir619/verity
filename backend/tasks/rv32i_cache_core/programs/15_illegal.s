# An all-zero encoding is illegal. The core stops without retiring it and without the later store.
# expect 0x1000 = 4
# expect 0x1004 = 0
    lui t0, 1
    addi a0, x0, 4
    sw a0, 0(t0)
    .word 0
    addi a0, x0, 8
    sw a0, 4(t0)
    ebreak
