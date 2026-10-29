# Writes to x0 are dropped: addi, add, lui and lw into x0 leave x0 at 0.
# sw x0 stores 0. addi a0, x0, 7 reads 0 and writes 7.
# expect 0x1000 = 0
# expect 0x1004 = 7
# expect 0x1008 = 0
    lui t0, 1
    addi x0, x0, 123
    add x0, t0, t0
    lui x0, 0x12345
    sw x0, 0(t0)
    addi a0, x0, 7
    sw a0, 4(t0)
    lw x0, 0(t0)
    sw x0, 8(t0)
    ebreak
