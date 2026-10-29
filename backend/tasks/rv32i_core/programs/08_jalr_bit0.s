# JALR clears bit 0 of the target. auipc at pc 4, plus 17, is 21. The target is 20.
# Landing on 16 would ebreak with memory still 0. A pc of 21 would disagree with the model.
# expect 0x1000 = 7
    lui t0, 1
    auipc t1, 0
    addi t1, t1, 17
    jalr x0, t1, 0
    ebreak
    addi a0, x0, 7
    sw a0, 0(t0)
    ebreak
