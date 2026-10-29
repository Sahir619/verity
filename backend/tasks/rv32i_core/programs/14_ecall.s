# ecall stops the core without retiring and without the later store.
# expect 0x1000 = 9
# expect 0x1004 = 0
    lui t0, 1
    addi a0, x0, 9
    sw a0, 0(t0)
    ecall
    addi a0, x0, 3
    sw a0, 4(t0)
    ebreak
