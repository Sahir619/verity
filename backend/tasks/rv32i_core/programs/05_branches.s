# All six branches, signed and unsigned corners. Each check adds its own bit.
# Taken checks jump to the add. Not-taken checks fall into the add.
# Bits 0..17 all set: 1+2+...+131072 = 262143.
#   beq equal taken, beq unequal not taken, bne unequal taken, bne equal not taken,
#   blt -1<1, blt 1<-1 not, blt 0x80000000<0, bge 5>=5, bge -2>=-1 not, bge 1>=-1,
#   bltu 0<1, bltu 0xffffffff<1 not, bltu 1<0x80000000, bgeu 0>=0, bgeu 1>=2 not,
#   bgeu 0xffffffff>=0x80000000, beqz x0, bnez x0 not.
# expect 0x1000 = 262143
    li a0, 0
    li t0, 0
    li t1, 1
    li t2, -1
    li t3, -2
    li t4, 5
    li t5, 6
    li t6, 0x80000000
    li a1, 2

    beq t4, t4, beq_eq_ok
    j beq_eq_skip
beq_eq_ok:
    addi a0, a0, 1
beq_eq_skip:

    beq t4, t5, beq_ne_bad
    addi a0, a0, 2
    j beq_ne_skip
beq_ne_bad:
    nop
beq_ne_skip:

    bne t4, t5, bne_ne_ok
    j bne_ne_skip
bne_ne_ok:
    addi a0, a0, 4
bne_ne_skip:

    bne t4, t4, bne_eq_bad
    addi a0, a0, 8
    j bne_eq_skip
bne_eq_bad:
    nop
bne_eq_skip:

    blt t2, t1, blt_m1_ok
    j blt_m1_skip
blt_m1_ok:
    addi a0, a0, 16
blt_m1_skip:

    blt t1, t2, blt_rev_bad
    addi a0, a0, 32
    j blt_rev_skip
blt_rev_bad:
    nop
blt_rev_skip:

    blt t6, t0, blt_min_ok
    j blt_min_skip
blt_min_ok:
    addi a0, a0, 64
blt_min_skip:

    bge t4, t4, bge_eq_ok
    j bge_eq_skip
bge_eq_ok:
    addi a0, a0, 128
bge_eq_skip:

    bge t3, t2, bge_m_bad
    addi a0, a0, 256
    j bge_m_skip
bge_m_bad:
    nop
bge_m_skip:

    bge t1, t2, bge_mix_ok
    j bge_mix_skip
bge_mix_ok:
    addi a0, a0, 512
bge_mix_skip:

    bltu t0, t1, bltu_01_ok
    j bltu_01_skip
bltu_01_ok:
    addi a0, a0, 1024
bltu_01_skip:

    bltu t2, t1, bltu_hi_bad
    li s1, 2048
    add a0, a0, s1
    j bltu_hi_skip
bltu_hi_bad:
    nop
bltu_hi_skip:

    bltu t1, t6, bltu_min_ok
    j bltu_min_skip
bltu_min_ok:
    li s1, 4096
    add a0, a0, s1
bltu_min_skip:

    bgeu t0, t0, bgeu_eq_ok
    j bgeu_eq_skip
bgeu_eq_ok:
    li s1, 8192
    add a0, a0, s1
bgeu_eq_skip:

    bgeu t1, a1, bgeu_12_bad
    li s1, 16384
    add a0, a0, s1
    j bgeu_12_skip
bgeu_12_bad:
    nop
bgeu_12_skip:

    bgeu t2, t6, bgeu_hi_ok
    j bgeu_hi_skip
bgeu_hi_ok:
    li s1, 32768
    add a0, a0, s1
bgeu_hi_skip:

    beqz t0, beqz_ok
    j beqz_skip
beqz_ok:
    li s1, 65536
    add a0, a0, s1
beqz_skip:

    bnez t0, bnez_bad
    li s1, 131072
    add a0, a0, s1
    j bnez_skip
bnez_bad:
    nop
bnez_skip:

    li s0, 0x1000
    sw a0, 0(s0)
    ebreak
