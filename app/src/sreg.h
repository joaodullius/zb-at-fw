#ifndef SREG_H_
#define SREG_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SREG_MAX_CLUSTERS 8

int sreg_init(void);
uint32_t sreg_u32(uint16_t id);
int8_t sreg_s8(uint16_t id);
bool sreg_bit(uint16_t id, uint8_t bit);
/* Hex registers (S03, S08, S09) as bytes in the order written (MSB first). */
int sreg_bytes(uint16_t id, uint8_t *out, size_t len);
bool sreg_is_zero(uint16_t id);
int sreg_clusters(uint16_t id, uint16_t *out, size_t max);
void sreg_factory_reset(void);

/* AT handlers: sreg_at_command() gets the text after "ATS". */
int sreg_at_command(char *arg);
int sreg_cmd_tokdump(char *args);

/* Called after a register was written (weak default in sreg.c does nothing). */
void sreg_written(uint16_t id);

/* Read-only registers owned by the Zigbee glue (S04, S05, S0D). Weak default in sreg.c. */
int sreg_dynamic_read(uint16_t id, char *out, size_t len);

#endif /* SREG_H_ */
