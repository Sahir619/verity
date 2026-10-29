"""Golden model for mm4_int4: C = A x B for 4x4 matrices of signed 4-bit values, signed 10-bit results.

Pure function of its inputs: no I/O, no cocotb, no randomness.
"""

N = 4
ELEM_BITS = 4
RESULT_BITS = 10
CYCLES = 24  # edges from the start edge until done is visible


def to_signed(value, bits):
    """Interpret the low `bits` bits of value as two's complement."""
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def golden_mm4_int4(a_rows, b_rows):
    """Flat list of the 16 signed results, index i*4 + j = C[i][j].

    a_rows[r] and b_rows[r] are the four lane values of a_row and b_row loaded at edge r + 1, so
    a_rows[i][k] = A[i][k] and b_rows[k][j] = B[k][j]. Lane values may be given as -8..7 or as raw 4-bit patterns.
    """
    result = []
    for i in range(N):
        for j in range(N):
            total = sum(to_signed(a_rows[i][k], ELEM_BITS) * to_signed(b_rows[k][j], ELEM_BITS) for k in range(N))
            result.append(to_signed(total, RESULT_BITS))
    return result
