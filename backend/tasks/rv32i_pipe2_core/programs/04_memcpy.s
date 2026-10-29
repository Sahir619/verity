# Copy four bytes from 0x1000 to 0x1010. Source word 0x44332211 is bytes 11 22 33 44.
# The destination starts as 0xA5A5A5A5 so a missed byte stays visible.
# expect 0x1000 = 0x44332211
# expect 0x1010 = 0x44332211
    .org 0x1000
    .word 0x44332211
    .org 0x1010
    .word 0xA5A5A5A5
    .org 0
    li s0, 0x1000
    li s1, 0x1010
    lbu t0, 0(s0)
    sb t0, 0(s1)
    lbu t0, 1(s0)
    sb t0, 1(s1)
    lbu t0, 2(s0)
    sb t0, 2(s1)
    lbu t0, 3(s0)
    sb t0, 3(s1)
    ebreak
