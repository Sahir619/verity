# Every load and store width, including sign extension.
# Word 0x80FF01FE at 0x1000 is bytes FE 01 FF 80.
# lb +0 = 0xfffffffe, lbu +0 = 0xfe, lh +0 = 0x1fe, lhu +0 = 0x1fe, lw = 0x80ff01fe.
# lb +3 = 0xffffff80, lh +2 = 0xffff80ff, lhu +2 = 0x80ff.
# Then sb 0xAA, sb 0xFF, sh 0xBEEF, sw 0x12345678 at 0x1100.
# Word 0x1100 becomes bytes AA FF EF BE = 0xbeefffaa. Word 0x1104 is 0x12345678.
# lb of the 0xFF byte is -1. lh of 0xBEEF is 0xffffbeef.
# expect 0x1000 = 0x80ff01fe
# expect 0x1020 = 0xfffffffe
# expect 0x1024 = 0xfe
# expect 0x1028 = 0x1fe
# expect 0x102c = 0x1fe
# expect 0x1030 = 0x80ff01fe
# expect 0x1034 = 0xffffff80
# expect 0x1038 = 0xffff80ff
# expect 0x103c = 0x80ff
# expect 0x1040 = 0xbeefffaa
# expect 0x1044 = 0x12345678
# expect 0x1048 = 0xffffffff
# expect 0x104c = 0xffffbeef
# expect 0x1100 = 0xbeefffaa
# expect 0x1104 = 0x12345678
    .org 0x1000
    .word 0x80ff01fe
    .org 0x1100
    .word 0xa5a5a5a5
    .word 0xa5a5a5a5
    .org 0
    li s0, 0x1000
    lb t0, 0(s0)
    sw t0, 32(s0)
    lbu t0, 0(s0)
    sw t0, 36(s0)
    lh t0, 0(s0)
    sw t0, 40(s0)
    lhu t0, 0(s0)
    sw t0, 44(s0)
    lw t0, 0(s0)
    sw t0, 48(s0)
    lb t0, 3(s0)
    sw t0, 52(s0)
    lh t0, 2(s0)
    sw t0, 56(s0)
    lhu t0, 2(s0)
    sw t0, 60(s0)
    li s1, 0x1100
    li t1, 0xaa
    sb t1, 0(s1)
    li t1, 0xff
    sb t1, 1(s1)
    li t1, 0xbeef
    sh t1, 2(s1)
    li t1, 0x12345678
    sw t1, 4(s1)
    lw t0, 0(s1)
    sw t0, 64(s0)
    lw t0, 4(s1)
    sw t0, 68(s0)
    lb t0, 1(s1)
    sw t0, 72(s0)
    lh t0, 2(s1)
    sw t0, 76(s0)
    ebreak
