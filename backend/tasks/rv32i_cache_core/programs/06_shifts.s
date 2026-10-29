# Shifts by 0 and by 31, plus arithmetic shifts of negative numbers.
# 1<<31 = 0x80000000. 0x7fffffff<<0 stays. 0x80000000 logical >>31 = 1.
# -1 logical >>0 stays. 0x80000000 arithmetic >>31 = -1. -1 arithmetic >>4 stays -1.
# 0x80000000 arithmetic >>1 = 0xc0000000. Register sll 1 by 31 matches.
# 0x80000000 logical >>0 stays. -2 arithmetic >>1 = -1. 0x7fffffff arithmetic >>31 = 0.
# -1 << 4 = 0xfffffff0.
# expect 0x1000 = 0x80000000
# expect 0x1004 = 0x7fffffff
# expect 0x1008 = 1
# expect 0x100c = 0xffffffff
# expect 0x1010 = 0xffffffff
# expect 0x1014 = 0xffffffff
# expect 0x1018 = 0xc0000000
# expect 0x101c = 0x80000000
# expect 0x1020 = 0x80000000
# expect 0x1024 = 0xffffffff
# expect 0x1028 = 0
# expect 0x102c = 0xfffffff0
    li s0, 0x1000
    li t0, 1
    slli t1, t0, 31
    sw t1, 0(s0)
    li t0, 0x7fffffff
    slli t1, t0, 0
    sw t1, 4(s0)
    li t0, 0x80000000
    srli t1, t0, 31
    sw t1, 8(s0)
    li t0, -1
    srli t1, t0, 0
    sw t1, 12(s0)
    li t0, 0x80000000
    srai t1, t0, 31
    sw t1, 16(s0)
    li t0, -1
    srai t1, t0, 4
    sw t1, 20(s0)
    li t0, 0x80000000
    srai t1, t0, 1
    sw t1, 24(s0)
    li t0, 1
    li t2, 31
    sll t1, t0, t2
    sw t1, 28(s0)
    li t0, 0x80000000
    li t2, 0
    srl t1, t0, t2
    sw t1, 32(s0)
    li t0, -2
    li t2, 1
    sra t1, t0, t2
    sw t1, 36(s0)
    li t0, 0x7fffffff
    li t2, 31
    sra t1, t0, t2
    sw t1, 40(s0)
    li t0, -1
    li t2, 4
    sll t1, t0, t2
    sw t1, 44(s0)
    ebreak
