# fence retires as a no-op. nop retires as addi x0, x0, 0. a0 goes 1, then 2.
# expect 0x1000 = 2
    addi a0, x0, 1
    fence
    nop
    addi a0, a0, 1
    lui t0, 1
    sw a0, 0(t0)
    ebreak
