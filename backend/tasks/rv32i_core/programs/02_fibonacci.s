# fib(0) = 0, fib(1) = 1. Ten steps leave fib(10) = 55 in a0.
# Trace: (0,1) (1,1) (1,2) (2,3) (3,5) (5,8) (8,13) (13,21) (21,34) (34,55) (55,89).
# expect 0x1000 = 55
    li a0, 0
    li a1, 1
    li t0, 10
loop:
    add t1, a0, a1
    mv a0, a1
    mv a1, t1
    addi t0, t0, -1
    bnez t0, loop
    li t2, 0x1000
    sw a0, 0(t2)
    ebreak
