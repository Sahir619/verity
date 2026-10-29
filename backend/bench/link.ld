OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { ram (rwx) : ORIGIN = 0, LENGTH = 1M }
SECTIONS {
    .text : { KEEP(*(.text.start)) *(.text .text.*) } > ram
    .rodata : { *(.rodata .rodata.* .srodata .srodata.*) } > ram
    .data : { *(.data .data.* .sdata .sdata.*) } > ram
    . = ALIGN(4);
    __global_pointer$ = . + 0x800;
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.bss .bss.* .sbss .sbss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > ram
    __stack_top = ORIGIN(ram) + LENGTH(ram);
    ASSERT(__bss_end < __stack_top - 16384, "benchmark leaves no stack space")
    /DISCARD/ : { *(.comment .note.* .eh_frame*) }
}
