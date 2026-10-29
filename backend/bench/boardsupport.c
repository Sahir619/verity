#include <stdint.h>

void initialise_board(void) {}
void start_trigger(void) { *(volatile uint32_t *)0xfffffff0u = 1; }
void stop_trigger(void) { *(volatile uint32_t *)0xfffffff4u = 1; }
