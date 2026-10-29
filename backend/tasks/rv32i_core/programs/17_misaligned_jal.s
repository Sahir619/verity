# JAL to a target that is not a multiple of 4 stops the core without retiring.
# It does not write ra, and the later store does not happen.
# expect 0x1000 = 1
# expect 0x1004 = 0
    lui t0, 1
    addi a0, x0, 1
    sw a0, 0(t0)
    jal ra, 6
    addi a0, x0, 2
    sw a0, 4(t0)
    ebreak
