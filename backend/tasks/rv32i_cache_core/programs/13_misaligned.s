# A misaligned lw stops the core without retiring and without the later store.
# The source still ends in ebreak. That instruction is not reached.
# expect 0x1000 = 1
# expect 0x1004 = 0
    lui t0, 1
    addi a0, x0, 1
    sw a0, 0(t0)
    lw a1, 1(t0)
    addi a0, x0, 2
    sw a0, 4(t0)
    ebreak
