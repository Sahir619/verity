# Every ALU op except the shifts (those are in 06_shifts), with wrap and sign corners.
# add 0x7fffffff+1 = 0x80000000. add -1+1 = 0. sub 0-1 = -1. sub 0x80000000-1 = 0x7fffffff.
# slt -1<1 = 1, 1<-1 = 0, 0x80000000<0 = 1.
# sltu 1<0xffffffff = 1, 0xffffffff<1 = 0.
# xor/or of 0xf0f0f0f0 and 0x0f0f0f0f = 0xffffffff. and with 0xff00ff00 = 0xf000f000.
# addi same wrap. slti -1<1 = 1. sltiu 1<-1 = 1. xori 15,-1 = 0xfffffff0.
# ori 0,-1 = -1. andi -1, 0xff = 0xff. slti 5<5 = 0. sltiu 0<1 = 1.
# expect 0x1000 = 0x80000000
# expect 0x1004 = 0
# expect 0x1008 = 0xffffffff
# expect 0x100c = 0x7fffffff
# expect 0x1010 = 1
# expect 0x1014 = 0
# expect 0x1018 = 1
# expect 0x101c = 1
# expect 0x1020 = 0
# expect 0x1024 = 0xffffffff
# expect 0x1028 = 0xffffffff
# expect 0x102c = 0xf000f000
# expect 0x1030 = 0x80000000
# expect 0x1034 = 1
# expect 0x1038 = 1
# expect 0x103c = 0xfffffff0
# expect 0x1040 = 0xffffffff
# expect 0x1044 = 0xff
# expect 0x1048 = 0
# expect 0x104c = 1
    li s0, 0x1000
    li t0, 0x7fffffff
    addi t1, t0, 1
    sw t1, 0(s0)
    li t0, -1
    addi t1, t0, 1
    sw t1, 4(s0)
    li t0, 0
    addi t2, x0, 1
    sub t1, t0, t2
    sw t1, 8(s0)
    li t0, 0x80000000
    sub t1, t0, t2
    sw t1, 12(s0)
    li t0, -1
    slt t1, t0, t2
    sw t1, 16(s0)
    li t3, 1
    li t0, -1
    slt t1, t3, t0
    sw t1, 20(s0)
    li t0, 0x80000000
    li t3, 0
    slt t1, t0, t3
    sw t1, 24(s0)
    li t0, 1
    li t3, -1
    sltu t1, t0, t3
    sw t1, 28(s0)
    sltu t1, t3, t0
    sw t1, 32(s0)
    li t0, 0xf0f0f0f0
    li t3, 0x0f0f0f0f
    xor t1, t0, t3
    sw t1, 36(s0)
    or t1, t0, t3
    sw t1, 40(s0)
    li t3, 0xff00ff00
    and t1, t0, t3
    sw t1, 44(s0)
    li t0, 0x7fffffff
    addi t1, t0, 1
    sw t1, 48(s0)
    li t0, -1
    slti t1, t0, 1
    sw t1, 52(s0)
    li t0, 1
    sltiu t1, t0, -1
    sw t1, 56(s0)
    li t0, 15
    xori t1, t0, -1
    sw t1, 60(s0)
    li t0, 0
    ori t1, t0, -1
    sw t1, 64(s0)
    li t0, -1
    andi t1, t0, 0xff
    sw t1, 68(s0)
    li t0, 5
    slti t1, t0, 5
    sw t1, 72(s0)
    li t0, 0
    sltiu t1, t0, 1
    sw t1, 76(s0)
    ebreak
