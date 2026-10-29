# Sum 1 + 2 + ... + 10. The loop adds t0 while t0 is still <= 10, then stores a0.
# expect 0x1000 = 55
    li a0, 0
    li t0, 1
    li t1, 10
loop:
    add a0, a0, t0
    addi t0, t0, 1
    bge t1, t0, loop
    li t2, 0x1000
    sw a0, 0(t2)
    ebreak
