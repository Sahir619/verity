# A not-taken branch to a target that is not a multiple of 4 retires as usual.
# A taken branch to such a target stops the core without retiring and without the later store.
# expect 0x1000 = 1
# expect 0x1004 = 0
    lui t0, 1
    bne x0, x0, 6
    addi a0, x0, 1
    sw a0, 0(t0)
    beq x0, x0, 6
    addi a0, x0, 2
    sw a0, 4(t0)
    ebreak
