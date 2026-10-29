"""Golden model for alu8: 8-bit ALU with a zero flag and a carry flag (registered outputs, latency 1).

Pure function of its inputs: no I/O, no cocotb, no randomness.
"""

OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_SHL, OP_SHR, OP_CMP = range(8)
OP_NAMES = ("add", "sub", "and", "or", "xor", "shl", "shr", "cmp")


def golden_alu8(a, b, op):
    """Return (y, zero, carry) for one operation. All values unsigned 8-bit; the shift amount is b[2:0]."""
    a &= 0xFF
    b &= 0xFF
    op &= 0x7
    amount = b & 0x7
    carry = 0
    if op == OP_ADD:
        total = a + b
        y, carry = total & 0xFF, total >> 8
    elif op == OP_SUB:
        y, carry = (a - b) & 0xFF, 1 if a < b else 0
    elif op == OP_AND:
        y = a & b
    elif op == OP_OR:
        y = a | b
    elif op == OP_XOR:
        y = a ^ b
    elif op == OP_SHL:
        y = (a << amount) & 0xFF
        carry = (a >> (8 - amount)) & 1 if amount else 0
    elif op == OP_SHR:
        y = a >> amount
        carry = (a >> (amount - 1)) & 1 if amount else 0
    else:  # compare: y is 0, the flags carry the answer
        return 0, 1 if a == b else 0, 1 if a < b else 0
    return y, 1 if y == 0 else 0, carry
