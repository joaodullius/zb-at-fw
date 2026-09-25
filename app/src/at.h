/*
 * Shared definitions of the AT firmware (Telegesis R309 command set).
 */
#ifndef AT_H_
#define AT_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <zephyr/kernel.h>

#define AT_DEVICE_NAME "nRF54L15"
#define AT_FW_REVISION "R309N"

#define AT_LINE_MAX 200

/* Handler results: AT_OK prints OK, a positive value prints ERROR:XX,
 * AT_NO_RESPONSE prints nothing (the handler printed or is rebooting).
 */
#define AT_OK          0
#define AT_NO_RESPONSE (-1)

/* Error codes, R309 AT command manual section 3. */
#define AT_ERR_UNKNOWN_CMD    0x02
#define AT_ERR_INVALID_SREG   0x04
#define AT_ERR_INVALID_PARAM  0x05
#define AT_ERR_UNREACHABLE    0x06
#define AT_ERR_NO_BUFFERS     0x18
#define AT_ERR_READ_ONLY      0x19
#define AT_ERR_BAD_PASSWORD   0x20
#define AT_ERR_CANNOT_FORM    0x25
#define AT_ERR_NO_NETWORK     0x27
#define AT_ERR_IN_PAN         0x28
#define AT_ERR_LEAVE          0x2C
#define AT_ERR_XCASTB_TIMEOUT 0x35
#define AT_ERR_TOO_MANY_UCAST 0x72
#define AT_ERR_MSG_TOO_LONG   0x74
#define AT_ERR_NOT_JOINED     0x93
#define AT_ERR_CANNOT_JOIN    0x94

struct at_cmd {
	const char *name; /* text after "AT", e.g. "I", "+JN", "&F" */
	int (*handler)(char *args); /* args: text after the name, e.g. ":0000=hi" or "?" */
};

/* at_uart.c */
int at_uart_init(void);
int at_uart_get_line(char *out, k_timeout_t timeout);
void at_uart_inject_line(const char *line);
void at_uart_set_echo(bool on);
int at_uart_read_binary(uint8_t *buf, size_t len, k_timeout_t timeout);
void at_print(const char *fmt, ...);
void at_print_parts(const char *head, const uint8_t *body, size_t body_len, const char *tail);

/* at_cmd.c */
void at_cmd_start(void);
const char *at_cmd_current_line(void);
void at_print_result(int result);
void at_reboot(void);

/* at_fmt.c */
void at_hex_rev(char *out, const uint8_t *lsb_first, size_t n);
int at_parse_hex_rev(const char *s, uint8_t *lsb_first, size_t n);
int at_parse_hex_n(const char *s, size_t len, uint32_t *out);
void at_reverse(uint8_t *buf, size_t n);

#endif /* AT_H_ */
