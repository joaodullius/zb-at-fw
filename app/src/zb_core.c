/*
 * Zigbee stack control for the AT firmware: role selection, start, leave,
 * network prompts, and the AT commands that drive them.
 *
 * ZBOSS can be started once per boot and its role is fixed before start.
 * So: the stack starts on AT+EN / AT+JN / AT+JPAN (after erasing ZBOSS NVRAM),
 * or at boot when a network was recorded ("at/zb/role"). A second attempt in
 * the same boot is stored in "at/zb/pend" and run right after a silent reboot.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include <zboss_api.h>
#include <zb_address.h>
#include <zb_nrf_platform.h>
#include <zigbee/zigbee_app_utils.h>
#include <zigbee/zigbee_error_handler.h>

#include "at.h"
#include "sreg.h"
#include "zb_core.h"

LOG_MODULE_REGISTER(zb_core, LOG_LEVEL_INF);

#define FORM_TIMEOUT          K_SECONDS(20)
#define JOIN_TIMEOUT          K_SECONDS(30)
#define LEAVE_TIMEOUT         K_SECONDS(5)
#define PERMIT_JOIN_REFRESH_S 150 /* steering opens the network for 180 s */

#define S0A_BLOCK_JOIN_LOCAL 0x0
#define S0A_NO_UNSECURE_REJOIN 0x3
#define S0A_ENCRYPT_NWK_KEY  0x4
#define S0A_BLOCK_JOIN_TC    0x5
#define S0A_USE_S09_KEY      0x8
#define S0A_NO_TCLK_REQUEST  0xA
#define S0A_TYPE_LOW         0xE
#define S0A_TYPE_HIGH        0xF
#define S0E_JPAN_OFF         0x3
#define S0E_LEFTPAN_OFF      0x4
#define S0E_NEWNODE_OFF      0x8
#define S0F_ANNOUNCE_OFF     0x0
#define S0F_NODELEFT_ON      0xB

#define CAP_DEVICE_TYPE_FFD BIT(1)
#define CAP_RX_ON_WHEN_IDLE BIT(3)

enum op {
	OP_NONE,
	OP_FORM,
	OP_JOIN,
	OP_LEAVE,
	OP_FACTORY,
};

static volatile enum op op;
static volatile int op_result;
static K_SEM_DEFINE(op_done, 0, 1);
static bool stack_started;
static uint8_t stored_role;
static char pending_cmd[AT_LINE_MAX];
static struct k_work_delayable reboot_work;
static zb_ieee_addr_t last_newnode;

/* ---------------------------------------------------------------------------
 * State
 */
static int load_cb(const char *key, size_t len, settings_read_cb read_cb, void *cb_arg,
		   void *param)
{
	ARG_UNUSED(param);

	if (strcmp(key, "role") == 0 && len == 1) {
		(void)read_cb(cb_arg, &stored_role, 1);
	} else if (strcmp(key, "pend") == 0 && len <= sizeof(pending_cmd)) {
		ssize_t n = read_cb(cb_arg, pending_cmd, len);

		pending_cmd[n > 0 ? n - 1 : 0] = '\0';
	}
	return 0;
}

static void role_save(uint8_t role)
{
	stored_role = role;
	(void)settings_save_one("at/zb/role", &role, sizeof(role));
}

static void reboot_fn(struct k_work *work)
{
	ARG_UNUSED(work);
	at_reboot();
}

static void complete(int result)
{
	if (op != OP_NONE) {
		op_result = result;
		op = OP_NONE;
		k_sem_give(&op_done);
	}
}

bool zbc_joined(void)
{
	return stack_started && ZB_JOINED();
}

void zbc_eui64_str(char out[17])
{
	zb_ieee_addr_t ieee;

	zb_get_long_address(ieee);
	at_hex_rev(out, ieee, sizeof(ieee));
}

int sreg_dynamic_read(uint16_t id, char *out, size_t len)
{
	switch (id) {
	case 0x04:
		if (len < 17) {
			return -ENOMEM;
		}
		zbc_eui64_str(out);
		return 0;
	case 0x05:
		snprintf(out, len, "%04X", zbc_joined() ? zb_get_short_address() : 0xFFFE);
		return 0;
	case 0x0D:
		snprintf(out, len, "%s,%s", AT_DEVICE_NAME, AT_FW_REVISION);
		return 0;
	default:
		return -ENOENT;
	}
}

/* ---------------------------------------------------------------------------
 * Prompts (ZBOSS thread)
 */
static void print_jpan(void)
{
	zb_ext_pan_id_t epid;
	char epid_str[17];

	if (sreg_bit(0x0E, S0E_JPAN_OFF)) {
		return;
	}
	zb_get_extended_pan_id(epid);
	at_hex_rev(epid_str, epid, sizeof(epid));
	at_print("JPAN:%u,%04X,%s", zb_get_current_channel(), zb_get_pan_id(), epid_str);
}

static void print_announce(const zb_zdo_signal_device_annce_params_t *p)
{
	char eui[17];
	const char *kind;

	if (sreg_bit(0x0F, S0F_ANNOUNCE_OFF)) {
		return;
	}
	if (p->capability & CAP_DEVICE_TYPE_FFD) {
		kind = "FFD";
	} else if (p->capability & CAP_RX_ON_WHEN_IDLE) {
		kind = "ZED";
	} else {
		kind = "SED";
	}
	at_hex_rev(eui, p->ieee_addr, sizeof(p->ieee_addr));
	at_print("%s:%s,%04X", kind, eui, p->device_short_addr);
}

static void print_newnode(zb_uint16_t short_addr, const zb_ieee_addr_t ieee)
{
	char eui[17];

	/* Direct children are reported twice (association and device update). */
	if (ZB_IEEE_ADDR_CMP(last_newnode, ieee)) {
		return;
	}
	ZB_IEEE_ADDR_COPY(last_newnode, ieee);
	if (sreg_bit(0x0E, S0E_NEWNODE_OFF)) {
		return;
	}
	at_hex_rev(eui, ieee, 8);
	at_print("NEWNODE:%04X,%s,FFFF", short_addr, eui);
}

static void print_nodeleft(zb_uint16_t short_addr, const zb_ieee_addr_t ieee)
{
	char eui[17];

	if (ZB_IEEE_ADDR_CMP(last_newnode, ieee)) {
		ZB_IEEE_ADDR_ZERO(last_newnode); /* report a later rejoin again */
	}
	if (!sreg_bit(0x0F, S0F_NODELEFT_ON)) {
		return;
	}
	at_hex_rev(eui, ieee, 8);
	at_print("NODELEFT:%04X,%s", short_addr, eui);
}

/* ---------------------------------------------------------------------------
 * Stack control
 */
static zb_uint32_t channel_mask(void)
{
	return (zb_uint32_t)sreg_u32(0x00) << 11; /* S00 bit 0 = channel 11 */
}

static bool end_device_type(void)
{
	return sreg_bit(0x0A, S0A_TYPE_HIGH) || sreg_bit(0x0A, S0A_TYPE_LOW);
}

static uint8_t current_role(void)
{
	switch (zb_get_network_role()) {
	case ZB_NWK_DEVICE_TYPE_COORDINATOR:
		return ZBC_ROLE_ZC;
	case ZB_NWK_DEVICE_TYPE_ROUTER:
		return ZBC_ROLE_ZR;
	default:
		return ZBC_ROLE_ZED;
	}
}

/* Keys and key transport, applied before the stack starts (they are read at join time). */
static void security_prestart(uint8_t role)
{
	static const uint8_t standard_key[16] = ZB_STANDARD_TC_KEY
	uint8_t key[16];

	/* S0A bit 8: preconfigured TC link key from S09, else the Zigbee default key. */
	if (sreg_bit(0x0A, S0A_USE_S09_KEY) && sreg_bytes(0x09, key, sizeof(key)) == 0) {
		zbc_tc_link_key_apply(key);
	} else {
		zbc_tc_link_key_apply(standard_key);
	}
	if (role == ZBC_ROLE_ZC) {
		/* S0A bit 4 clear (R309 default): the TC sends the network key without link
		 * key encryption (applied again in join_policy_apply, formation resets it).
		 */
		zbc_tc_unencrypted_key_transport(!sreg_bit(0x0A, S0A_ENCRYPT_NWK_KEY));
	} else {
		/* Like R309 (Ember) nodes, accept a network key sent in clear. */
		zbc_accept_unencrypted_key_transport(true);
	}
}

static void set_role(uint8_t role, zb_uint32_t mask)
{
	security_prestart(role);
	if (role == ZBC_ROLE_ZC) {
		zb_set_network_coordinator_role(mask);
		/* Pre-Zigbee 3.0 devices may join and are not evicted. */
		zb_bdb_set_legacy_device_support(1);
		return;
	}
	if (role == ZBC_ROLE_ZR) {
		zb_set_network_router_role(mask);
	} else {
		zb_set_network_ed_role(mask);
		zb_set_rx_on_when_idle(ZB_TRUE); /* v1: SED/MED behave as a non-sleepy ZED */
	}
	zb_bdb_set_legacy_device_support(sreg_bit(0x0A, S0A_NO_TCLK_REQUEST) ? 1 : 0);
}

static void tx_power_done(zb_bufid_t bufid)
{
	zb_tx_power_params_t *p = zb_buf_begin(bufid);

	if (p->status != RET_OK) {
		LOG_WRN("TX power not set (%d)", p->status);
	}
	zb_buf_free(bufid);
}

static void tx_power_set(zb_bufid_t bufid)
{
	zb_tx_power_params_t *p = zb_buf_initial_alloc(bufid, sizeof(*p));

	p->page = ZB_CHANNEL_PAGE0_2_4_GHZ;
	p->channel = zb_get_current_channel();
	p->tx_power = sreg_s8(0x01);
	p->cb = tx_power_done;
	zb_set_tx_power_async(bufid);
}

/* ---------------------------------------------------------------------------
 * Join control (ZBOSS thread). The coordinator keeps the whole network open by
 * re-running BDB network steering (S0A bit 5 on the TC closes it network-wide);
 * S0A bit 0 closes joining through the local node, on any node, every time the
 * network is opened again.
 */
static void permit_req_done(zb_bufid_t bufid)
{
	zb_buf_free(bufid);
}

/* tc_significance = 1 also changes the Trust Centre policy: the TC then refuses to
 * authorize any join, through any router (measured). 0 only closes the addressed node.
 */
static void permit_join_send(zb_bufid_t bufid, zb_uint16_t dest, zb_uint8_t tc_significance)
{
	zb_zdo_mgmt_permit_joining_req_param_t *req =
		ZB_BUF_GET_PARAM(bufid, zb_zdo_mgmt_permit_joining_req_param_t);

	req->dest_addr = dest;
	req->permit_duration = 0;
	req->tc_significance = tc_significance;
	if (zb_zdo_mgmt_permit_joining_req(bufid, permit_req_done) == ZB_ZDO_INVALID_TSN) {
		zb_buf_free(bufid);
	}
}

/* S0A bit 0: no joining through this node. */
static void close_local(zb_bufid_t bufid)
{
	permit_join_send(bufid, zb_get_short_address(), 0);
}

/* S0A bit 5 on the TC: the Trust Centre accepts no new node, whoever the parent is. */
static void close_network(zb_bufid_t bufid)
{
	permit_join_send(bufid, zb_get_short_address(), 1);
}

static void permit_join_refresh(zb_uint8_t param)
{
	ARG_UNUSED(param);

	if (sreg_bit(0x0A, S0A_BLOCK_JOIN_TC)) {
		return;
	}
	if (!bdb_start_top_level_commissioning(ZB_BDB_NETWORK_STEERING)) {
		ZB_SCHEDULE_APP_ALARM(permit_join_refresh, 0, ZB_TIME_ONE_SECOND * 5);
	}
}

/* Apply S0A bits 0, 3 and 5 now (after joining, or when S0A is written). */
static void join_policy_apply(zb_uint8_t param)
{
	ARG_UNUSED(param);

	if (!ZB_JOINED()) {
		return;
	}
	if (current_role() == ZBC_ROLE_ZC) {
		zbc_tc_rejoin_apply(!sreg_bit(0x0A, S0A_NO_UNSECURE_REJOIN));
		zbc_tc_unencrypted_key_transport(!sreg_bit(0x0A, S0A_ENCRYPT_NWK_KEY));
		zbc_tc_authenticate_always(sreg_bit(0x0A, S0A_BLOCK_JOIN_LOCAL));
		ZB_SCHEDULE_APP_ALARM_CANCEL(permit_join_refresh, ZB_ALARM_ANY_PARAM);
		if (sreg_bit(0x0A, S0A_BLOCK_JOIN_TC)) {
			(void)zb_buf_get_out_delayed(close_network);
			return;
		}
		/* Opens the network; the PERMIT_JOIN_STATUS signal then applies bit 0. */
		ZB_SCHEDULE_APP_CALLBACK(permit_join_refresh, 0);
	} else if (sreg_bit(0x0A, S0A_BLOCK_JOIN_LOCAL)) {
		(void)zb_buf_get_out_delayed(close_local);
	}
}

void sreg_written(uint16_t id)
{
	if (id == 0x0A && zbc_joined()) {
		ZB_SCHEDULE_APP_CALLBACK(join_policy_apply, 0);
	}
}

static void network_up(void)
{
	uint8_t role = current_role();

	if (stored_role != role) {
		role_save(role);
	}
	(void)zb_buf_get_out_delayed(tx_power_set);
	if (role == ZBC_ROLE_ZC) {
		zbc_legacy_tc_apply(true); /* R309 is a pre-Zigbee 3.0 Trust Centre */
	}
	ZB_SCHEDULE_APP_CALLBACK(join_policy_apply, 0);
	print_jpan();
}

void zboss_signal_handler(zb_bufid_t bufid)
{
	zb_zdo_app_signal_hdr_t *hdr = NULL;
	zb_zdo_app_signal_type_t sig = zb_get_app_signal(bufid, &hdr);
	zb_ret_t status = ZB_GET_APP_SIGNAL_STATUS(bufid);
	bool is_zc = zb_get_network_role() == ZB_NWK_DEVICE_TYPE_COORDINATOR;

	switch (sig) {
	case ZB_BDB_SIGNAL_DEVICE_FIRST_START:
		if (op == OP_FORM) {
			if (status != RET_OK ||
			    !bdb_start_top_level_commissioning(ZB_BDB_NETWORK_FORMATION)) {
				complete(AT_ERR_CANNOT_FORM);
			}
		} else if (op == OP_JOIN) {
			if (status != RET_OK ||
			    !bdb_start_top_level_commissioning(ZB_BDB_NETWORK_STEERING)) {
				complete(AT_ERR_CANNOT_JOIN);
			}
		} else {
			/* A network was recorded but ZBOSS NVRAM is empty. */
			role_save(ZBC_ROLE_NONE);
		}
		break;

	case ZB_BDB_SIGNAL_DEVICE_REBOOT:
		if (status == RET_OK) {
			network_up();
		} else if (!is_zc) {
			/* The default handler keeps trying to rejoin. */
			ZB_ERROR_CHECK(zigbee_default_signal_handler(bufid));
		}
		break;

	case ZB_BDB_SIGNAL_FORMATION:
		if (status == RET_OK) {
			network_up();
			complete(AT_OK);
		} else {
			complete(AT_ERR_CANNOT_FORM);
		}
		break;

	case ZB_BDB_SIGNAL_STEERING:
		if (is_zc) {
			ZB_SCHEDULE_APP_ALARM_CANCEL(permit_join_refresh, ZB_ALARM_ANY_PARAM);
			ZB_SCHEDULE_APP_ALARM(permit_join_refresh, 0,
					      ZB_TIME_ONE_SECOND *
						      (status == RET_OK ? PERMIT_JOIN_REFRESH_S : 5));
		} else if (op == OP_JOIN) {
			if (status == RET_OK && ZB_JOINED()) {
				network_up();
				complete(AT_OK);
			} else {
				complete(AT_ERR_CANNOT_JOIN);
			}
		} else if (stored_role != ZBC_ROLE_NONE) {
			/* Rejoin of the recorded network, driven by the default handler
			 * (after a reboot, a lost parent or a leave with rejoin).
			 */
			bool up = status == RET_OK && ZB_JOINED();

			ZB_ERROR_CHECK(zigbee_default_signal_handler(bufid));
			if (up) {
				network_up();
			}
		}
		/* Otherwise an AT+JN/AT+JPAN failed or timed out: no automatic retries. */
		break;

	case ZB_ZDO_SIGNAL_LEAVE: {
		zb_zdo_signal_leave_params_t *p =
			ZB_ZDO_SIGNAL_GET_PARAMS(hdr, zb_zdo_signal_leave_params_t);

		if (op == OP_JOIN) {
			/* Joined and left again, e.g. the TC link key exchange failed. */
			role_save(ZBC_ROLE_NONE);
			complete(AT_ERR_CANNOT_JOIN);
		} else if (op == OP_LEAVE || op == OP_FACTORY) {
			complete(AT_OK);
		} else if (status != RET_OK) {
			LOG_WRN("Leave failed (status %d)", status);
		} else if (p->leave_type == ZB_NWK_LEAVE_TYPE_REJOIN) {
			/* Asked to leave and rejoin: keep the network, the default handler rejoins. */
			LOG_INF("Leave with rejoin");
			ZB_ERROR_CHECK(zigbee_default_signal_handler(bufid));
		} else if (stored_role != ZBC_ROLE_NONE) {
			/* Removed from the network by another node. */
			role_save(ZBC_ROLE_NONE);
			if (!sreg_bit(0x0E, S0E_LEFTPAN_OFF)) {
				at_print("LeftPAN");
			}
			k_work_reschedule(&reboot_work, K_MSEC(200));
		}
	} break;

	case ZB_NWK_SIGNAL_PERMIT_JOIN_STATUS:
		/* The network was opened (e.g. by the coordinator's steering): S0A bit 0
		 * keeps joining through this node closed.
		 */
		if (*ZB_ZDO_SIGNAL_GET_PARAMS(hdr, zb_uint8_t) > 0 &&
		    sreg_bit(0x0A, S0A_BLOCK_JOIN_LOCAL)) {
			(void)zb_buf_get_out_delayed(close_local);
		}
		break;

	case ZB_ZDO_SIGNAL_DEVICE_ANNCE:
		print_announce(ZB_ZDO_SIGNAL_GET_PARAMS(hdr, zb_zdo_signal_device_annce_params_t));
		break;

	case ZB_NWK_SIGNAL_DEVICE_ASSOCIATED:
		if (is_zc) {
			zb_nwk_signal_device_associated_params_t *p = ZB_ZDO_SIGNAL_GET_PARAMS(
				hdr, zb_nwk_signal_device_associated_params_t);

			print_newnode(zb_address_short_by_ieee(p->device_addr), p->device_addr);
		}
		break;

	case ZB_ZDO_SIGNAL_DEVICE_UPDATE: {
		zb_zdo_signal_device_update_params_t *p =
			ZB_ZDO_SIGNAL_GET_PARAMS(hdr, zb_zdo_signal_device_update_params_t);

		if (is_zc && p->status == 0x01) { /* standard device unsecured join */
			print_newnode(p->short_addr, p->long_addr);
		} else if (is_zc && p->status == 0x02) { /* device left */
			print_nodeleft(p->short_addr, p->long_addr);
		}
		ZB_ERROR_CHECK(zigbee_default_signal_handler(bufid));
	} break;

	case ZB_ZDO_SIGNAL_LEAVE_INDICATION: {
		zb_zdo_signal_leave_indication_params_t *p =
			ZB_ZDO_SIGNAL_GET_PARAMS(hdr, zb_zdo_signal_leave_indication_params_t);

		if (is_zc && !p->rejoin) {
			print_nodeleft(p->short_addr, p->device_addr);
		}
		ZB_ERROR_CHECK(zigbee_default_signal_handler(bufid));
	} break;

	default:
		ZB_ERROR_CHECK(zigbee_default_signal_handler(bufid));
		break;
	}

	if (bufid) {
		zb_buf_free(bufid);
	}
}

/* ---------------------------------------------------------------------------
 * AT commands (AT thread)
 */
static int run_op(enum op new_op, k_timeout_t timeout, int timeout_err)
{
	k_sem_reset(&op_done);
	op = new_op;
	if (!stack_started) {
		stack_started = true;
		zigbee_enable();
	}
	if (k_sem_take(&op_done, timeout) != 0) {
		op = OP_NONE;
		return timeout_err;
	}
	return op_result;
}

static int defer_to_next_boot(void)
{
	const char *line = at_cmd_current_line();

	(void)settings_save_one("at/zb/pend", line, strlen(line) + 1);
	at_reboot();
	return AT_NO_RESPONSE;
}

static void prepare_new_network(uint8_t role, zb_uint32_t mask, const uint8_t *epid_lsb)
{
	zigbee_erase_persistent_storage(ZB_TRUE);
	set_role(role, mask);
	if (role == ZBC_ROLE_ZC) {
		uint8_t key[16];

		if (sreg_u32(0x02) != 0) {
			zb_set_pan_id((zb_uint16_t)sreg_u32(0x02));
		}
		if (!sreg_is_zero(0x08) && sreg_bytes(0x08, key, sizeof(key)) == 0) {
			zb_secur_setup_nwk_key(key, 0);
		}
	}
	if (epid_lsb) {
		zb_set_extended_pan_id(epid_lsb);
	}
}

static int start_network(uint8_t role, zb_uint32_t mask, const uint8_t *epid_lsb)
{
	if (stored_role != ZBC_ROLE_NONE) {
		return AT_ERR_IN_PAN;
	}
	if (stack_started) {
		return defer_to_next_boot();
	}
	prepare_new_network(role, mask, epid_lsb);
	if (role == ZBC_ROLE_ZC) {
		return run_op(OP_FORM, FORM_TIMEOUT, AT_ERR_CANNOT_FORM);
	}
	return run_op(OP_JOIN, JOIN_TIMEOUT, AT_ERR_NO_NETWORK);
}

static const uint8_t *preferred_epid(uint8_t *buf)
{
	if (sreg_is_zero(0x03) || sreg_bytes(0x03, buf, 8) != 0) {
		return NULL;
	}
	at_reverse(buf, 8);
	return buf;
}

static int cmd_en(char *args)
{
	uint8_t epid[8];

	if (*args) {
		return AT_ERR_INVALID_PARAM;
	}
	return start_network(ZBC_ROLE_ZC, channel_mask(), preferred_epid(epid));
}

static int cmd_jn(char *args)
{
	uint8_t epid[8];

	if (*args) {
		return AT_ERR_INVALID_PARAM;
	}
	return start_network(end_device_type() ? ZBC_ROLE_ZED : ZBC_ROLE_ZR, channel_mask(),
			     preferred_epid(epid));
}

/* AT+JPAN:<channel>,<PID or EPID> */
static int cmd_jpan(char *args)
{
	uint8_t epid[8];
	char *comma;
	char *end;
	long channel;
	size_t id_len;
	uint32_t pan;

	if (args[0] != ':' || !(comma = strchr(args, ','))) {
		return AT_ERR_INVALID_PARAM;
	}
	channel = strtol(args + 1, &end, 10);
	id_len = strlen(comma + 1);
	if (end != comma || channel < 11 || channel > 26) {
		return AT_ERR_INVALID_PARAM;
	}
	if (id_len == 16 && at_parse_hex_rev(comma + 1, epid, 8) == 0) {
		return start_network(end_device_type() ? ZBC_ROLE_ZED : ZBC_ROLE_ZR,
				     1UL << channel, epid);
	}
	if (id_len == 4 && at_parse_hex_n(comma + 1, 4, &pan) == 0) {
		/* The PAN ID cannot be used as a join filter: only the channel restricts. */
		return start_network(end_device_type() ? ZBC_ROLE_ZED : ZBC_ROLE_ZR,
				     1UL << channel, NULL);
	}
	return AT_ERR_INVALID_PARAM;
}

static void leave_network(enum op leave_op)
{
	if (zbc_joined()) {
		k_sem_reset(&op_done);
		op = leave_op;
		ZB_SCHEDULE_APP_CALLBACK(zb_bdb_reset_via_local_action, 0);
		if (k_sem_take(&op_done, LEAVE_TIMEOUT) != 0) {
			op = OP_NONE;
			LOG_WRN("No leave confirmation, resetting anyway");
		}
	}
	role_save(ZBC_ROLE_NONE);
}

static int cmd_dassl(char *args)
{
	if (*args) {
		return AT_ERR_INVALID_PARAM;
	}
	if (stored_role == ZBC_ROLE_NONE) {
		return AT_ERR_NOT_JOINED;
	}
	leave_network(OP_LEAVE);
	at_print_result(AT_OK);
	if (!sreg_bit(0x0E, S0E_LEFTPAN_OFF)) {
		at_print("LeftPAN");
	}
	at_reboot();
	return AT_NO_RESPONSE;
}

static int cmd_factory(char *args)
{
	if (*args) {
		return AT_ERR_INVALID_PARAM;
	}
	if (stored_role != ZBC_ROLE_NONE) {
		leave_network(OP_FACTORY);
	}
	(void)settings_delete("at/zb/pend");
	sreg_factory_reset();
	at_print_result(AT_OK);
	at_reboot();
	return AT_NO_RESPONSE;
}

static const char *role_name(void)
{
	switch (current_role()) {
	case ZBC_ROLE_ZC:
		return "COO";
	case ZBC_ROLE_ZR:
		return "FFD";
	default:
		break;
	}
	/* S0A bits F-E: 10 = ZED, 01 = SED, 11 = MED */
	if (sreg_bit(0x0A, S0A_TYPE_HIGH) && sreg_bit(0x0A, S0A_TYPE_LOW)) {
		return "MED";
	}
	return sreg_bit(0x0A, S0A_TYPE_LOW) ? "SED" : "ZED";
}

static int cmd_n(char *args)
{
	zb_ext_pan_id_t epid;
	char epid_str[17];
	int power = sreg_s8(0x01);

	if (*args && strcmp(args, "?") != 0) {
		return AT_ERR_INVALID_PARAM;
	}
	if (!zbc_joined()) {
		at_print("+N=NoPAN");
		return AT_OK;
	}
	zb_get_extended_pan_id(epid);
	at_hex_rev(epid_str, epid, sizeof(epid));
	at_print(power < 0 ? "+N=%s,%u,-%02d,%04X,%s" : "+N=%s,%u,%02d,%04X,%s", role_name(),
		 zb_get_current_channel(), power < 0 ? -power : power, zb_get_pan_id(), epid_str);
	return AT_OK;
}

const struct at_cmd zbc_cmds[] = {
	{ "+EN", cmd_en },
	{ "+JN", cmd_jn },
	{ "+JPAN", cmd_jpan },
	{ "+DASSL", cmd_dassl },
	{ "+N", cmd_n },
	{ "&F", cmd_factory },
	{ NULL, NULL },
};

void zbc_init(void)
{
	k_work_init_delayable(&reboot_work, reboot_fn);
	(void)settings_load_subtree_direct("at/zb", load_cb, NULL);

	if (pending_cmd[0] != '\0') {
		(void)settings_delete("at/zb/pend");
		at_uart_inject_line(pending_cmd);
	} else if (stored_role != ZBC_ROLE_NONE) {
		/* Rejoin the recorded network from ZBOSS NVRAM, as R309 does after a reset. */
		set_role(stored_role, channel_mask());
		stack_started = true;
		zigbee_enable();
	}
}
