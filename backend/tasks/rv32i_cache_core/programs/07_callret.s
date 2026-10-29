# LUI, AUIPC, JAL and JALR (call and ret).
# 0: lui a0, 0x12345            -> a0 = 0x12345000
# 4: auipc a1, 0                 -> a1 = 4
# 8: lui t0, 1                   -> t0 = 0x1000
# 12: sw a0, 8(t0)                -> word 0x1008
# 16: sw a1, 0(t0)                -> word 0x1000
# 20: jal ra, func                -> ra = 24, func at 32
# 24: sw a0, 4(t0)                -> word 0x1004 = 42
# 28: ebreak
# 32: addi a0, x0, 42
# 36: ret                         -> jalr x0, ra, 0
# expect 0x1000 = 4
# expect 0x1004 = 42
# expect 0x1008 = 0x12345000
    lui a0, 0x12345
    auipc a1, 0
    lui t0, 1
    sw a0, 8(t0)
    sw a1, 0(t0)
    jal ra, func
    sw a0, 4(t0)
    ebreak
func:
    addi a0, x0, 42
    ret
