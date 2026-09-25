#ifndef ZB_CORE_H_
#define ZB_CORE_H_

#include <stdbool.h>

#include "at.h"

enum zbc_role {
	ZBC_ROLE_NONE,
	ZBC_ROLE_ZC,
	ZBC_ROLE_ZR,
	ZBC_ROLE_ZED,
};

/* Load the stored state; rejoin the recorded network or run a pending command. */
void zbc_init(void);
/* The stack runs and the node is in a network. */
bool zbc_joined(void);
/* EUI64 of this node, 16 hex digits, most significant first. */
void zbc_eui64_str(char out[17]);
/* Security settings (zb_secpol.c, ZBOSS thread or before the stack starts). */
/* Trust Centre policy: legacy = ignore TC link key requests (R309 behaviour). */
void zbc_legacy_tc_apply(bool legacy);
/* Preconfigured global Trust Centre link key (16 bytes, as written in S09). */
void zbc_tc_link_key_apply(const uint8_t key[16]);
/* Trust Centre: allow unsecured (TC) rejoins. */
void zbc_tc_rejoin_apply(bool allow);
/* Trust Centre: authorize joins through routers even when joining through the TC is closed. */
void zbc_tc_authenticate_always(bool always);
/* Trust Centre: send the network key to joining nodes without link key encryption. */
void zbc_tc_unencrypted_key_transport(bool unencrypted);
/* Joiner: accept a network key sent without link key encryption. */
void zbc_accept_unencrypted_key_transport(bool accept);

extern const struct at_cmd zbc_cmds[];

#endif /* ZB_CORE_H_ */
