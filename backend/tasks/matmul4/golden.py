"""Golden model for matmul4: C = A x B for 4x4 matrices of unsigned 8-bit values.

Pure function of its inputs: no I/O, no cocotb, no randomness.
"""

N = 4
ELEM_BITS = 8
RESULT_BITS = 20
RESULT_MASK = (1 << RESULT_BITS) - 1
CYCLES = 24  # edges from the start edge until done is visible


def golden_matmul4(a_rows, b_rows):
    """Flat list of the 16 results, index i*4 + j = C[i][j].

    a_rows[r] and b_rows[r] are the four lane values of a_row and b_row loaded at edge r + 1,
    so a_rows[i][k] = A[i][k] and b_rows[k][j] = B[k][j].
    """
    result = []
    for i in range(N):
        for j in range(N):
            total = sum((a_rows[i][k] & 0xFF) * (b_rows[k][j] & 0xFF) for k in range(N))
            result.append(total & RESULT_MASK)
    return result
