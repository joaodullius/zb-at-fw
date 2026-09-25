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
/* Trust Centre policy: legacy = ignore TC link key requests (R309 behaviour). */
void zbc_legacy_tc_apply(bool legacy);

extern const struct at_cmd zbc_cmds[];

#endif /* ZB_CORE_H_ */
