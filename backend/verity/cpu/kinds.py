"""The CPU choice card's fixed catalog. CPU kinds are never model output. Each summary names a real chip of the
same class (sources: PicoRV32, Espressif ESP32-C3 docs, lowRISC Ibex, SiFive U74 and Rocket READMEs)."""

CPU_KINDS = (
    {
        "id": "rv32i_micro",
        "name": "RV32I microcontroller core, multicycle, no pipeline",
        "summary": "RV32I base instructions, seven blocks and an integrated core. No caches or MMU. Same class as PicoRV32, a small RISC-V core used on FPGAs.",
        "buildable": True,
    },
    {"id": "rv32im", "name": "RV32IM", "summary": "Adds multiply and divide in hardware.", "buildable": False},
    {"id": "rv32imc", "name": "RV32IMC", "summary": "Adds compressed instructions. The ESP32-C3 Wi-Fi chip uses an RV32IMC core.", "buildable": False},
    {
        "id": "rv32i_pipeline",
        "name": "2-stage pipelined RV32I",
        "summary": "Fetches the next instruction while one executes. Ibex, the core in OpenTitan, uses this design.",
        "buildable": False,
    },
    {
        "id": "rv32i_cached",
        "name": "RV32I with a cache",
        "summary": "A small cache in front of slow memory: the idea behind the Ryzen 5800X3D's stacked cache, at small scale.",
        "buildable": False,
    },
    {
        "id": "application_class",
        "name": "Application-class cores",
        "summary": "Cores with caches and an MMU that run Linux, like Rocket or SiFive U74. Desktop chips such as AMD Ryzen are far beyond this.",
        "buildable": False,
    },
)
