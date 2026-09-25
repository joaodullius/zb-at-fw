/*
 * Hex helpers. ZBOSS keeps EUI64 / extended PAN IDs least significant byte
 * first; the AT interface shows them most significant digit first.
 */
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "at.h"

static int hex_nibble(char c)
{
	if (c >= '0' && c <= '9') {
		return c - '0';
	}
	c = (char)toupper((unsigned char)c);
	if (c >= 'A' && c <= 'F') {
		return c - 'A' + 10;
	}
	return -1;
}

void at_hex_rev(char *out, const uint8_t *lsb_first, size_t n)
{
	for (size_t i = 0; i < n; i++) {
		sprintf(out + 2 * i, "%02X", lsb_first[n - 1 - i]);
	}
	out[2 * n] = '\0';
}

int at_parse_hex_rev(const char *s, uint8_t *lsb_first, size_t n)
{
	for (size_t i = 0; i < n; i++) {
		int hi = hex_nibble(s[2 * i]);
		int lo = hex_nibble(s[2 * i + 1]);

		if (hi < 0 || lo < 0) {
			return -EINVAL;
		}
		lsb_first[n - 1 - i] = (uint8_t)(hi << 4 | lo);
	}
	return 0;
}

int at_parse_hex_n(const char *s, size_t len, uint32_t *out)
{
	uint32_t v = 0;

	if (len == 0 || len > 8) {
		return -EINVAL;
	}
	for (size_t i = 0; i < len; i++) {
		int d = hex_nibble(s[i]);

		if (d < 0) {
			return -EINVAL;
		}
		v = v << 4 | (uint32_t)d;
	}
	*out = v;
	return 0;
}

void at_reverse(uint8_t *buf, size_t n)
{
	for (size_t i = 0; i < n / 2; i++) {
		uint8_t t = buf[i];

		buf[i] = buf[n - 1 - i];
		buf[n - 1 - i] = t;
	}
}
