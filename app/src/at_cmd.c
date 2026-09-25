/*
 * AT command parser and dispatcher, plus the module control commands
 * AT, ATI and ATZ.
 */
#include <ctype.h>
#include <string.h>
#include <strings.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/util.h>

#include "at.h"
#include "sreg.h"
#include "zb_core.h"
#include "zb_msg.h"

LOG_MODULE_REGISTER(at_cmd, LOG_LEVEL_INF);

#define AT_CMD_STACK_SIZE 4096
#define AT_CMD_PRIORITY   7

static char current_line[AT_LINE_MAX];

void at_reboot(void)
{
	k_msleep(20); /* let the last line leave the UART */
	sys_reboot(SYS_REBOOT_COLD);
}

void at_print_result(int result)
{
	/* S0E bit 1 hides OK, bit 0 hides ERROR:XX. */
	if (result == AT_OK && !sreg_bit(0x0E, 1)) {
		at_print("OK");
	} else if (result > 0 && !sreg_bit(0x0E, 0)) {
		at_print("ERROR:%02X", result);
	}
}

const char *at_cmd_current_line(void)
{
	return current_line;
}

static int cmd_at(char *args)
{
	return *args ? AT_ERR_UNKNOWN_CMD : AT_OK;
}

static int cmd_ati(char *args)
{
	char eui[17];

	if (*args) {
		return AT_ERR_INVALID_PARAM;
	}
	zbc_eui64_str(eui);
	at_print("Telegesis %s", AT_DEVICE_NAME);
	at_print("%s", AT_FW_REVISION);
	at_print("%s", eui);
	return AT_OK;
}

static int cmd_atz(char *args)
{
	if (*args) {
		return AT_ERR_INVALID_PARAM;
	}
	at_print_result(AT_OK);
	at_reboot();
	return AT_NO_RESPONSE;
}

static const struct at_cmd base_cmds[] = {
	{ "", cmd_at },
	{ "I", cmd_ati },
	{ "Z", cmd_atz },
	{ "+TOKDUMP", sreg_cmd_tokdump },
	{ NULL, NULL },
};

static const struct at_cmd *const cmd_tables[] = {
	base_cmds,
	zbc_cmds,
	zbm_cmds,
	NULL,
};

static int execute(char *line)
{
	char *p;
	size_t n;

	if (line[0] == '\x01') {
		return AT_ERR_MSG_TOO_LONG;
	}
	if (toupper((unsigned char)line[0]) != 'A' || toupper((unsigned char)line[1]) != 'T') {
		return AT_ERR_UNKNOWN_CMD;
	}
	p = line + 2;
	if (toupper((unsigned char)p[0]) == 'S' && isxdigit((unsigned char)p[1])) {
		return sreg_at_command(p + 1);
	}
	n = strcspn(p, ":=?");
	for (size_t t = 0; cmd_tables[t] != NULL; t++) {
		for (const struct at_cmd *c = cmd_tables[t]; c->name != NULL; c++) {
			if (strlen(c->name) == n && strncasecmp(c->name, p, n) == 0) {
				return c->handler(p + n);
			}
		}
	}
	return AT_ERR_UNKNOWN_CMD;
}

static void at_cmd_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	for (;;) {
		(void)at_uart_get_line(current_line, K_FOREVER);
		LOG_DBG("cmd: %s", current_line);
		at_print_result(execute(current_line));
	}
}

K_THREAD_DEFINE(at_cmd_tid, AT_CMD_STACK_SIZE, at_cmd_thread, NULL, NULL, NULL, AT_CMD_PRIORITY,
		0, SYS_FOREVER_MS);

void at_cmd_start(void)
{
	k_thread_start(at_cmd_tid);
}
