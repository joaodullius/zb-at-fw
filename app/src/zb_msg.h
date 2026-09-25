#ifndef ZB_MSG_H_
#define ZB_MSG_H_

#include "at.h"

/* Build endpoint 2 from S48-S4C and register endpoints and data callbacks.
 * Call before the Zigbee stack starts.
 */
void zbm_init(void);

extern const struct at_cmd zbm_cmds[];

#endif /* ZB_MSG_H_ */
