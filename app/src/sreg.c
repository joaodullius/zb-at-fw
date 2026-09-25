/*
 * S-registers of the R309 command set: table, validation, ATS commands and
 * persistence (settings keys "at/s/<XX>").
 */
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/util.h>

#include "at.h"
#include "sreg.h"

LOG_MODULE_REGISTER(sreg, LOG_LEVEL_INF);

enum sreg_type {
	T_U8,       /* 2 hex digits */
	T_U16,      /* 4 hex digits */
	T_S8,       /* signed decimal, S01 */
	T_HEX64,    /* 16 hex digits */
	T_KEY128,   /* 32 hex digits */
	T_STR,      /* text up to max_len */
	T_CLUSTERS, /* up to 8 comma-separated 16-bit IDs */
	T_DYN,      /* value comes from sreg_dynamic_read() */
};

#define F_NV BIT(0) /* persisted */
#define F_RO BIT(1) /* read only */
#define F_PW BIT(2) /* write needs the password */
#define F_WO BIT(3) /* write only: reads show an empty value */

#define VAL_MAX 48

struct sreg {
	uint16_t id;
	uint8_t type;
	uint8_t flags;
	uint8_t max_len;
	const char *def;
	char val[VAL_MAX];
};

static struct sreg regs[] = {
	{ 0x00, T_U16, F_NV, 0, "FFFF" },   /* channel mask, bit 0 = channel 11 */
	{ 0x01, T_S8, F_NV, 0, "08" },      /* TX power [dBm] */
	{ 0x02, T_U16, F_NV, 0, "0000" },   /* preferred PAN ID */
	{ 0x03, T_HEX64, F_NV, 0, "0000000000000000" }, /* preferred EPID */
	{ 0x04, T_DYN, F_RO, 0, "" },       /* local EUI64 */
	{ 0x05, T_DYN, F_RO, 0, "" },       /* local NWK address */
	{ 0x08, T_KEY128, F_NV | F_PW | F_WO, 0, "00000000000000000000000000000000" },
	{ 0x09, T_KEY128, F_NV | F_PW | F_WO, 0, "5A6967426565416C6C69616E63653039" },
	{ 0x0A, T_U16, F_NV | F_PW, 0, "0000" }, /* main function */
	{ 0x0B, T_STR, F_NV, 16, "" },      /* user readable name */
	{ 0x0C, T_STR, F_NV | F_PW | F_WO, 8, "password" },
	{ 0x0D, T_DYN, F_RO, 0, "" },       /* device information */
	{ 0x0E, T_U16, F_NV, 0, "0000" },   /* prompt enable 1 */
	{ 0x0F, T_U16, F_NV, 0, "0006" },   /* prompt enable 2 */
	{ 0x10, T_U16, F_NV, 0, "0000" },   /* extended function */
	{ 0x12, T_U16, F_NV, 0, "0C00" },   /* UART: 115200, echo on */
	{ 0x40, T_U16, 0, 0, "0101" },      /* xCAST src/dst endpoints (volatile) */
	{ 0x41, T_U16, F_NV, 0, "0101" },
	{ 0x42, T_U16, 0, 0, "0002" },      /* xCAST cluster (volatile) */
	{ 0x43, T_U16, F_NV, 0, "0002" },
	{ 0x44, T_U16, 0, 0, "C091" },      /* xCAST profile (volatile) */
	{ 0x45, T_U16, F_NV, 0, "C091" },
	{ 0x48, T_U16, F_NV, 0, "C091" },   /* endpoint 2 profile */
	{ 0x49, T_U16, F_NV, 0, "0000" },   /* endpoint 2 device ID */
	{ 0x4A, T_U8, F_NV, 0, "00" },      /* endpoint 2 device version */
	{ 0x4B, T_CLUSTERS, F_NV, 0, "" },  /* endpoint 2 input clusters */
	{ 0x4C, T_CLUSTERS, F_NV, 0, "" },  /* endpoint 2 output clusters */
	{ 0x4E, T_U16, F_NV, 0, "0000" },   /* end device poll timeout (stored only) */
	{ 0xE0, T_U8, F_NV, 0, "00" },      /* vendor: 0 = legacy TC, 1 = Zigbee 3.0 TC */
};

__weak int sreg_dynamic_read(uint16_t id, char *out, size_t len)
{
	ARG_UNUSED(id);
	if (len > 0) {
		out[0] = '\0';
	}
	return 0;
}

static struct sreg *find(uint16_t id)
{
	for (size_t i = 0; i < ARRAY_SIZE(regs); i++) {
		if (regs[i].id == id) {
			return &regs[i];
		}
	}
	return NULL;
}

static void key_of(const struct sreg *r, char *key, size_t len)
{
	snprintf(key, len, "at/s/%02X", r->id);
}

static void persist(const struct sreg *r)
{
	char key[12];

	if (r->flags & F_NV) {
		key_of(r, key, sizeof(key));
		(void)settings_save_one(key, r->val, strlen(r->val) + 1);
	}
}

static bool all_hex(const char *s, size_t len)
{
	for (size_t i = 0; i < len; i++) {
		if (!isxdigit((unsigned char)s[i])) {
			return false;
		}
	}
	return true;
}

static bool numeric(const struct sreg *r)
{
	return r->type == T_U8 || r->type == T_U16;
}

/* Validate `in` for register `r` and write its canonical form to `out`. */
static int normalise(const struct sreg *r, const char *in, char *out)
{
	size_t len = strlen(in);
	uint32_t v;

	switch (r->type) {
	case T_U8:
	case T_U16: {
		size_t digits = r->type == T_U8 ? 2 : 4;

		if (len == 0 || len > digits || at_parse_hex_n(in, len, &v)) {
			return AT_ERR_INVALID_PARAM;
		}
		if (r->id == 0x12 && (v >> 8) != 0x0C) {
			return AT_ERR_INVALID_PARAM; /* only 115200 baud is supported */
		}
		sprintf(out, digits == 2 ? "%02X" : "%04X", (unsigned int)v);
		return AT_OK;
	}
	case T_S8: {
		char *end;
		long d = strtol(in, &end, 10);

		if (len == 0 || *end != '\0' || d < -40 || d > 8) {
			return AT_ERR_INVALID_PARAM;
		}
		sprintf(out, d < 0 ? "-%02ld" : "%02ld", d < 0 ? -d : d);
		return AT_OK;
	}
	case T_HEX64:
	case T_KEY128: {
		size_t digits = r->type == T_HEX64 ? 16 : 32;

		if (len != digits || !all_hex(in, len)) {
			return AT_ERR_INVALID_PARAM;
		}
		for (size_t i = 0; i <= len; i++) {
			out[i] = (char)toupper((unsigned char)in[i]);
		}
		return AT_OK;
	}
	case T_STR:
		if (len > r->max_len) {
			return AT_ERR_INVALID_PARAM;
		}
		strcpy(out, in);
		return AT_OK;
	case T_CLUSTERS: {
		size_t count = 0;
		const char *p = in;

		out[0] = '\0';
		while (*p) {
			if (count == SREG_MAX_CLUSTERS || strlen(p) < 4 || !all_hex(p, 4) ||
			    (p[4] != ',' && p[4] != '\0') || (p[4] == ',' && p[5] == '\0')) {
				return AT_ERR_INVALID_PARAM;
			}
			at_parse_hex_n(p, 4, &v);
			sprintf(out + strlen(out), count ? ",%04X" : "%04X", (unsigned int)v);
			count++;
			p += p[4] == ',' ? 5 : 4;
		}
		return AT_OK;
	}
	default:
		return AT_ERR_READ_ONLY;
	}
}

static void apply_side_effects(const struct sreg *r)
{
	if (r->id == 0x12) {
		at_uart_set_echo(!(sreg_u32(0x12) & BIT(4)));
	}
}

static int load_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg,
		   void *param)
{
	uint32_t id;
	struct sreg *r;
	char buf[VAL_MAX];
	ssize_t n;

	ARG_UNUSED(param);
	if (strlen(key) != 2 || at_parse_hex_n(key, 2, &id) || !(r = find((uint16_t)id)) ||
	    len > sizeof(buf)) {
		return 0;
	}
	n = read_cb(cb_arg, buf, len);
	if (n > 0) {
		buf[n - 1] = '\0';
		strcpy(r->val, buf);
	}
	return 0;
}

static void load_defaults(void)
{
	for (size_t i = 0; i < ARRAY_SIZE(regs); i++) {
		strcpy(regs[i].val, regs[i].def);
	}
}

static void init_volatile(void)
{
	/* S40, S42, S44 start from S41, S43, S45. */
	strcpy(find(0x40)->val, find(0x41)->val);
	strcpy(find(0x42)->val, find(0x43)->val);
	strcpy(find(0x44)->val, find(0x45)->val);
}

int sreg_init(void)
{
	load_defaults();
	(void)settings_load_subtree_direct("at/s", load_cb, NULL);
	init_volatile();
	apply_side_effects(find(0x12));
	return 0;
}

void sreg_factory_reset(void)
{
	char key[12];

	for (size_t i = 0; i < ARRAY_SIZE(regs); i++) {
		if (regs[i].flags & F_NV) {
			key_of(&regs[i], key, sizeof(key));
			(void)settings_delete(key);
		}
	}
	load_defaults();
	init_volatile();
}

uint32_t sreg_u32(uint16_t id)
{
	const struct sreg *r = find(id);

	return r ? (uint32_t)strtoul(r->val, NULL, 16) : 0;
}

int8_t sreg_s8(uint16_t id)
{
	const struct sreg *r = find(id);

	return r ? (int8_t)atoi(r->val) : 0;
}

bool sreg_bit(uint16_t id, uint8_t bit)
{
	return (sreg_u32(id) >> bit) & 1U;
}

int sreg_bytes(uint16_t id, uint8_t *out, size_t len)
{
	const struct sreg *r = find(id);
	uint32_t byte;

	if (!r || strlen(r->val) != 2 * len) {
		return -EINVAL;
	}
	for (size_t i = 0; i < len; i++) {
		at_parse_hex_n(&r->val[2 * i], 2, &byte);
		out[i] = (uint8_t)byte;
	}
	return 0;
}

bool sreg_is_zero(uint16_t id)
{
	const struct sreg *r = find(id);

	for (const char *p = r ? r->val : ""; *p; p++) {
		if (*p != '0') {
			return false;
		}
	}
	return true;
}

int sreg_clusters(uint16_t id, uint16_t *out, size_t max)
{
	const struct sreg *r = find(id);
	const char *p = r ? r->val : "";
	size_t n = 0;
	uint32_t v;

	while (*p && n < max && at_parse_hex_n(p, 4, &v) == 0) {
		out[n++] = (uint16_t)v;
		p += p[4] == ',' ? 5 : 4;
	}
	return (int)n;
}

static int read_reg(const struct sreg *r, int bit)
{
	char val[VAL_MAX];

	if (r->type == T_DYN) {
		(void)sreg_dynamic_read(r->id, val, sizeof(val));
	} else if (r->flags & F_WO) {
		val[0] = '\0';
	} else {
		strcpy(val, r->val);
	}

	if (bit >= 0) {
		if (!numeric(r)) {
			return AT_ERR_INVALID_PARAM;
		}
		at_print("%u", (unsigned int)((strtoul(val, NULL, 16) >> bit) & 1U));
	} else {
		at_print("%s", val);
	}
	return AT_OK;
}

static int write_reg(struct sreg *r, int bit, char *arg)
{
	char *sep = strchr(arg, ':'); /* the minimal libc has no strpbrk() */
	char value[VAL_MAX];
	int err;

	if (r->flags & F_RO) {
		return AT_ERR_READ_ONLY;
	}
	if (!sep) {
		sep = strchr(arg, ';');
	}
	if (sep) {
		*sep = '\0';
	}
	if ((r->flags & F_PW) && (!sep || strcmp(sep + 1, find(0x0C)->val) != 0)) {
		return AT_ERR_BAD_PASSWORD;
	}

	if (bit >= 0) {
		uint32_t v = (uint32_t)strtoul(r->val, NULL, 16);

		if (!numeric(r) || (strcmp(arg, "0") != 0 && strcmp(arg, "1") != 0)) {
			return AT_ERR_INVALID_PARAM;
		}
		v = arg[0] == '1' ? (v | BIT(bit)) : (v & ~BIT(bit));
		snprintf(value, sizeof(value), r->type == T_U8 ? "%02X" : "%04X", (unsigned int)v);
		arg = value;
	}

	err = normalise(r, arg, value);
	if (err) {
		return err;
	}
	strcpy(r->val, value);
	persist(r);
	apply_side_effects(r);
	return AT_OK;
}

int sreg_at_command(char *arg)
{
	size_t n = 0;
	uint32_t id;
	uint32_t bit;
	struct sreg *r;

	while (isxdigit((unsigned char)arg[n])) {
		n++;
	}
	if ((n != 2 && n != 3) || at_parse_hex_n(arg, 2, &id) || !(r = find((uint16_t)id))) {
		return AT_ERR_INVALID_SREG;
	}
	if (n == 3) {
		at_parse_hex_n(&arg[2], 1, &bit);
	}
	if (arg[n] == '?' && arg[n + 1] == '\0') {
		return read_reg(r, n == 3 ? (int)bit : -1);
	}
	if (arg[n] == '=') {
		return write_reg(r, n == 3 ? (int)bit : -1, &arg[n + 1]);
	}
	return AT_ERR_INVALID_PARAM;
}

int sreg_cmd_tokdump(char *args)
{
	char val[VAL_MAX];

	if (*args) {
		return AT_ERR_INVALID_PARAM;
	}
	for (size_t i = 0; i < ARRAY_SIZE(regs); i++) {
		const struct sreg *r = &regs[i];

		if (r->type == T_DYN) {
			(void)sreg_dynamic_read(r->id, val, sizeof(val));
		} else {
			strcpy(val, (r->flags & F_WO) ? "" : r->val);
		}
		at_print("S%02X:%s", r->id, val);
	}
	return AT_OK;
}
