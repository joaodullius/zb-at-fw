/*
 * Trust Centre link key requests.
 *
 * R309 firmware is older than Zigbee R21: as Trust Centre it never answers the
 * APS Request Key a Zigbee 3.0 node sends after joining. ZBOSS has no public
 * API for that, but its TC policy has the R22 attribute
 * allowTrustCenterLinkKeyRequests (0 = never, 1 = any time). The internal
 * headers are the ones the add-on uses to build lib/zboss/src.
 */
#include "zb_common.h"
#include "zb_aps.h"
#include "zb_aps_globals.h"

#include "zb_core.h"

#define TCLK_REQUESTS_NEVER    0
#define TCLK_REQUESTS_ANY_TIME 1

void zbc_legacy_tc_apply(bool legacy)
{
	ZB_TCPOL().allow_tc_link_key_requests = legacy ? TCLK_REQUESTS_NEVER : TCLK_REQUESTS_ANY_TIME;
}
