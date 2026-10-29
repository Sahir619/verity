# Four words at 0x1000. 10 + 20 + 30 + 40 = 100, stored at 0x1010.
# expect 0x1010 = 100
    .org 0x1000
    .word 10
    .word 20
    .word 30
    .word 40
    .word 0
    .org 0
    li s0, 0x1000
    lw t0, 0(s0)
    lw t1, 4(s0)
    add t0, t0, t1
    lw t1, 8(s0)
    add t0, t0, t1
    lw t1, 12(s0)
    add t0, t0, t1
    sw t0, 16(s0)
    ebreak
