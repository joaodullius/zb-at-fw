/*
 * Security settings that ZBOSS only exposes through its internal headers (the
 * ones the add-on uses to build lib/zboss/src):
 *
 * - Trust Centre link key requests. R309 firmware is older than Zigbee R21: as
 *   Trust Centre it never answers the APS Request Key a Zigbee 3.0 node sends
 *   after joining. ZBOSS's TC policy has the R22 attribute
 *   allowTrustCenterLinkKeyRequests (0 = never, 1 = any time).
 * - The preconfigured global Trust Centre link key (S09, used when S0A bit 8 is
 *   set; otherwise the Zigbee default "ZigBeeAlliance09").
 * - Trust Centre rejoin (unsecured rejoin) allowed or not (S0A bit 3).
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

void zbc_tc_link_key_apply(const uint8_t key[16])
{
	ZB_MEMCPY(ZB_AIB().tc_standard_key, key, ZB_CCM_KEY_SIZE);
}

void zbc_tc_rejoin_apply(bool allow)
{
	ZB_TCPOL().allow_tc_rejoins = allow ? ZB_TRUE : ZB_FALSE;
}

void zbc_tc_authenticate_always(bool always)
{
	/* 1: the TC authorizes new nodes joining through routers even while joining
	 * through the TC itself is closed (S0A bit 0 on the coordinator).
	 */
	ZB_TCPOL().authenticate_always = always ? 1 : 0;
}

void zbc_tc_unencrypted_key_transport(bool unencrypted)
{
	/* 1: the network key is sent to joining nodes without APS (link key) encryption. */
	ZB_TCPOL().aps_unencrypted_transport_key_join = unencrypted ? 1 : 0;
}

void zbc_accept_unencrypted_key_transport(bool accept)
{
	/* Joiner side: accept a network key sent without APS encryption, as R309 (Ember)
	 * nodes do; needed to join a TC that has S0A bit 4 clear.
	 */
	ZB_CERT_HACKS().allow_aps_unencrypted_transport_key = accept ? 1 : 0;
}
