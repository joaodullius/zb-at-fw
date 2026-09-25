/*
 * Application data: endpoint 1 carries Telegesis text (profile 0xC091,
 * cluster 0x0002), endpoint 2 is defined by S48-S4C and left to the host
 * (raw ZCL). Transmit commands, receive prompts, ACK/NACK.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>

#include <zboss_api.h>
#include <zb_address.h>

#include "at.h"
#include "sreg.h"
#include "zb_core.h"
#include "zb_msg.h"

LOG_MODULE_REGISTER(zb_msg, LOG_LEVEL_INF);

#define TEXT_PROFILE      0xC091
#define TEXT_CLUSTER      0x0002
#define EP_TEXT           1
#define EP_APP            2
#define PAYLOAD_MAX       82
#define RX_SHOW_MAX       128
#define UCAST_IN_FLIGHT   10
#define TX_SLOTS          4
#define DISCOVERY_ATTEMPTS 3
#define DISCOVERY_WAIT     K_SECONDS(2) /* per attempt: about 6 s in total */
#define BINARY_TIMEOUT    K_SECONDS(5)

#define S0A_RX_RSSI     0x6
#define S0E_ACK_OFF     0x6
#define S0E_NACK_OFF    0x7
#define S0E_SEQ_OFF     0xC
#define S0E_TEXT_OFF    0xE
#define S0F_SHOW_ALL    0x1 /* set: hide unhandled frames on all endpoints */
#define S0F_RX_HEX      0xC
#define S0F_TEXT_RSSI   0xE
#define S0F_SHOW_EP     0x8 /* set: show unhandled frames on endpoints 1 and above */
#define S10_QUIET_SEND  0xB

/* ---------------------------------------------------------------------------
 * Endpoints
 */
ZB_DECLARE_SIMPLE_DESC(8, 8);

static ZB_AF_SIMPLE_DESC_TYPE(1, 1) ep1_desc = {
	EP_TEXT, TEXT_PROFILE, 0xFFFF, 1, 0, 0, 0, { 0 },
};
static ZB_AF_SIMPLE_DESC_TYPE(8, 8) ep2_desc;

ZB_AF_DECLARE_ENDPOINT_DESC(ep1, EP_TEXT, TEXT_PROFILE, 0, NULL, 0, NULL,
			    (zb_af_simple_desc_1_1_t *)&ep1_desc, 0, NULL, 0, NULL);
ZB_AF_DECLARE_ENDPOINT_DESC(ep2, EP_APP, TEXT_PROFILE, 0, NULL, 0, NULL,
			    (zb_af_simple_desc_1_1_t *)&ep2_desc, 0, NULL, 0, NULL);
ZBOSS_DECLARE_DEVICE_CTX_2_EP(at_ctx, ep1, ep2);

static void ep2_build(void)
{
	uint16_t in[SREG_MAX_CLUSTERS];
	uint16_t out[SREG_MAX_CLUSTERS];
	int n_in = sreg_clusters(0x4B, in, ARRAY_SIZE(in));
	int n_out = sreg_clusters(0x4C, out, ARRAY_SIZE(out));

	ep2_desc.endpoint = EP_APP;
	ep2_desc.app_profile_id = (zb_uint16_t)sreg_u32(0x48);
	ep2_desc.app_device_id = (zb_uint16_t)sreg_u32(0x49);
	ep2_desc.app_device_version = sreg_u32(0x4A) & 0x0F;
	ep2_desc.app_input_cluster_count = (zb_uint8_t)n_in;
	ep2_desc.app_output_cluster_count = (zb_uint8_t)n_out;
	memcpy(ep2_desc.app_cluster_list, in, n_in * sizeof(uint16_t));
	memcpy(&ep2_desc.app_cluster_list[n_in], out, n_out * sizeof(uint16_t));
	ep2.profile_id = ep2_desc.app_profile_id;
}

/* ---------------------------------------------------------------------------
 * Transmit
 */
struct tx_req {
	uint16_t dst;
	uint16_t profile;
	uint16_t cluster;
	uint8_t src_ep;
	uint8_t dst_ep;
	uint8_t seq;
	bool ack;
	bool quiet;
	uint8_t len;
	uint8_t data[PAYLOAD_MAX];
};

struct in_flight {
	bool used;
	bool quiet;
	zb_bufid_t buf;
	uint8_t seq;
};

static struct tx_req slots[TX_SLOTS];
static atomic_t slots_used;
static struct in_flight in_flight[UCAST_IN_FLIGHT]; /* ZBOSS thread only */
static atomic_t in_flight_count;
static uint8_t next_seq; /* AT thread only */

static void report(uint8_t seq, bool quiet, bool acked)
{
	if (quiet) {
		return;
	}
	if (acked && !sreg_bit(0x0E, S0E_ACK_OFF)) {
		at_print("ACK:%02X", seq);
	} else if (!acked && !sreg_bit(0x0E, S0E_NACK_OFF)) {
		at_print("NACK:%02X", seq);
	}
}

static void in_flight_add(zb_bufid_t buf, const struct tx_req *r)
{
	for (int i = 0; i < UCAST_IN_FLIGHT; i++) {
		if (!in_flight[i].used) {
			in_flight[i] = (struct in_flight){ true, r->quiet, buf, r->seq };
			return;
		}
	}
}

static void tx_done(zb_uint8_t param)
{
	bool acked = zb_buf_get_status(param) == ZB_APS_USER_PAYLOAD_CB_STATUS_SUCCESS;

	for (int i = 0; i < UCAST_IN_FLIGHT; i++) {
		if (in_flight[i].used && in_flight[i].buf == param) {
			in_flight[i].used = false;
			atomic_dec(&in_flight_count);
			report(in_flight[i].seq, in_flight[i].quiet, acked);
			break;
		}
	}
	zb_buf_free(param);
}

static void tx_send(zb_bufid_t bufid, zb_uint16_t slot)
{
	struct tx_req *r = &slots[slot];
	zb_addr_u dst = { .addr_short = r->dst };
	zb_ret_t err;

	if (r->ack) {
		in_flight_add(bufid, r);
	}
	err = zb_aps_send_user_payload(bufid, dst, r->profile, r->cluster, r->dst_ep, r->src_ep,
				       ZB_APS_ADDR_MODE_16_ENDP_PRESENT,
				       r->ack ? ZB_TRUE : ZB_FALSE, r->data, r->len);
	if (err != RET_OK) {
		LOG_ERR("TX to 0x%04X not queued (%d)", r->dst, err);
		if (r->ack) {
			for (int i = 0; i < UCAST_IN_FLIGHT; i++) {
				if (in_flight[i].used && in_flight[i].buf == bufid) {
					in_flight[i].used = false;
				}
			}
			atomic_dec(&in_flight_count);
			report(r->seq, r->quiet, false);
		}
		zb_buf_free(bufid);
	}
	atomic_clear_bit(&slots_used, slot);
}

static int submit(const struct tx_req *r)
{
	int slot = -1;

	for (int i = 0; i < TX_SLOTS; i++) {
		if (!atomic_test_and_set_bit(&slots_used, i)) {
			slot = i;
			break;
		}
	}
	if (slot < 0) {
		return AT_ERR_NO_BUFFERS;
	}
	if (r->ack && atomic_inc(&in_flight_count) >= UCAST_IN_FLIGHT) {
		atomic_dec(&in_flight_count);
		atomic_clear_bit(&slots_used, slot);
		return AT_ERR_TOO_MANY_UCAST;
	}
	slots[slot] = *r;
	if (zb_buf_get_out_delayed_ext(tx_send, (zb_uint16_t)slot, 0) != RET_OK) {
		if (r->ack) {
			atomic_dec(&in_flight_count);
		}
		atomic_clear_bit(&slots_used, slot);
		return AT_ERR_NO_BUFFERS;
	}
	return AT_OK;
}

/* ---------------------------------------------------------------------------
 * Address resolution: EUI64 -> network address, with NWK_addr_req if unknown.
 */
static zb_ieee_addr_t disc_ieee;
static volatile zb_uint16_t disc_result;
static K_SEM_DEFINE(disc_done, 0, 1);

static void disc_resp(zb_bufid_t bufid)
{
	zb_zdo_nwk_addr_resp_head_t *resp = (zb_zdo_nwk_addr_resp_head_t *)zb_buf_begin(bufid);

	/* Only an answer for the EUI64 being resolved completes the wait. ZBOSS keeps
	 * broadcast ZDO callbacks alive longer than we wait, so a late answer or timeout
	 * of an earlier request can arrive here; its TSN cannot be used to tell (measured:
	 * valid answers after a rejoin carry a different TSN), the EUI64 can. Failures
	 * are left to our own timeout.
	 */
	if (resp->status == ZB_ZDP_STATUS_SUCCESS && ZB_IEEE_ADDR_CMP(resp->ieee_addr, disc_ieee)) {
		zb_uint16_t nwk;

		ZB_LETOH16(&nwk, &resp->nwk_addr);
		disc_result = nwk;
		k_sem_give(&disc_done);
	}
	zb_buf_free(bufid);
}

static void disc_send(zb_bufid_t bufid)
{
	zb_zdo_nwk_addr_req_param_t *req = ZB_BUF_GET_PARAM(bufid, zb_zdo_nwk_addr_req_param_t);

	req->dst_addr = ZB_NWK_BROADCAST_RX_ON_WHEN_IDLE;
	ZB_IEEE_ADDR_COPY(req->ieee_addr, disc_ieee);
	req->request_type = ZB_ZDO_SINGLE_DEVICE_RESP;
	req->start_index = 0;
	if (zb_zdo_nwk_addr_req(bufid, disc_resp) == 0xFF) {
		zb_buf_free(bufid);
		k_sem_give(&disc_done);
	}
}

static int discover(const zb_ieee_addr_t ieee, uint16_t *out)
{
	ZB_IEEE_ADDR_COPY(disc_ieee, ieee);
	disc_result = ZB_UNKNOWN_SHORT_ADDR;
	k_sem_reset(&disc_done);
	/* A broadcast can be lost (measured right after an end device rejoins): ask again. */
	for (int attempt = 0; attempt < DISCOVERY_ATTEMPTS; attempt++) {
		if (zb_buf_get_out_delayed(disc_send) != RET_OK) {
			return AT_ERR_NO_BUFFERS;
		}
		if (k_sem_take(&disc_done, DISCOVERY_WAIT) == 0 &&
		    disc_result != ZB_UNKNOWN_SHORT_ADDR) {
			break;
		}
	}
	if (disc_result == ZB_UNKNOWN_SHORT_ADDR) {
		return AT_ERR_UNREACHABLE;
	}
	*out = disc_result;
	return AT_OK;
}

/* <addr> is 4 hex digits (network address) or 16 hex digits (EUI64). */
static int resolve(const char *addr, size_t len, uint16_t *out)
{
	zb_ieee_addr_t ieee;
	uint32_t v;
	zb_uint16_t s;

	if (len == 4) {
		if (at_parse_hex_n(addr, 4, &v)) {
			return AT_ERR_INVALID_PARAM;
		}
		*out = (uint16_t)v;
		return AT_OK;
	}
	if (len != 16 || at_parse_hex_rev(addr, ieee, 8)) {
		return AT_ERR_INVALID_PARAM;
	}
	s = zb_address_short_by_ieee(ieee);
	if (s != ZB_UNKNOWN_SHORT_ADDR) {
		*out = s;
		return AT_OK;
	}
	return discover(ieee, out);
}

/* ---------------------------------------------------------------------------
 * AT commands
 */
static void xcast_defaults(struct tx_req *r)
{
	uint32_t eps = sreg_u32(0x40);

	r->src_ep = (uint8_t)(eps >> 8);
	r->dst_ep = (uint8_t)eps;
	r->cluster = (uint16_t)sreg_u32(0x42);
	r->profile = (uint16_t)sreg_u32(0x44);
}

static int send_unicast(struct tx_req *r, bool quiet)
{
	int err;

	r->ack = true;
	r->quiet = quiet;
	r->seq = next_seq++;
	err = submit(r);
	if (err) {
		return err;
	}
	if (!quiet && !sreg_bit(0x0E, S0E_SEQ_OFF)) {
		at_print("SEQ:%02X", r->seq);
	}
	return AT_OK;
}

/* AT+UCAST:<addr>=<data> */
static int cmd_ucast(char *args)
{
	struct tx_req r = { 0 };
	char *eq;
	size_t len;
	int err;

	if (!zbc_joined()) {
		return AT_ERR_NOT_JOINED;
	}
	if (args[0] != ':' || !(eq = strchr(args, '='))) {
		return AT_ERR_INVALID_PARAM;
	}
	len = strlen(eq + 1);
	if (len > PAYLOAD_MAX) {
		return AT_ERR_MSG_TOO_LONG;
	}
	err = resolve(args + 1, eq - (args + 1), &r.dst);
	if (err) {
		return err;
	}
	xcast_defaults(&r);
	memcpy(r.data, eq + 1, len);
	r.len = (uint8_t)len;
	return send_unicast(&r, false);
}

/* AT+BCAST:<nn>,<data>, nn = 00..30 hops (validated, not applied) */
static int cmd_bcast(char *args)
{
	struct tx_req r = { 0 };
	char *end;
	long hops;
	size_t len;

	if (!zbc_joined()) {
		return AT_ERR_NOT_JOINED;
	}
	if (args[0] != ':') {
		return AT_ERR_INVALID_PARAM;
	}
	hops = strtol(args + 1, &end, 10);
	if (end != args + 3 || *end != ',' || hops < 0 || hops > 30) {
		return AT_ERR_INVALID_PARAM;
	}
	len = strlen(end + 1);
	if (len > PAYLOAD_MAX) {
		return AT_ERR_MSG_TOO_LONG;
	}
	xcast_defaults(&r);
	r.dst = ZB_NWK_BROADCAST_ROUTER_COORDINATOR;
	memcpy(r.data, end + 1, len);
	r.len = (uint8_t)len;
	return submit(&r);
}

/* Split off the next comma-separated field of *p. */
static char *next_field(char **p)
{
	char *start = *p;
	char *comma = start ? strchr(start, ',') : NULL;

	if (!comma) {
		*p = NULL;
		return start;
	}
	*comma = '\0';
	*p = comma + 1;
	return start;
}

/* <addr>,<srcEP>,<dstEP>,<profile>,<cluster> */
static int parse_target(char **p, struct tx_req *r)
{
	char *addr = next_field(p);
	char *src = next_field(p);
	char *dst = next_field(p);
	char *profile = next_field(p);
	char *cluster = *p ? next_field(p) : NULL;
	uint32_t v[4];

	if (!addr || !src || !dst || !profile || !cluster || strlen(src) != 2 ||
	    strlen(dst) != 2 || strlen(profile) != 4 || strlen(cluster) != 4 ||
	    at_parse_hex_n(src, 2, &v[0]) || at_parse_hex_n(dst, 2, &v[1]) ||
	    at_parse_hex_n(profile, 4, &v[2]) || at_parse_hex_n(cluster, 4, &v[3])) {
		return AT_ERR_INVALID_PARAM;
	}
	r->src_ep = (uint8_t)v[0];
	r->dst_ep = (uint8_t)v[1];
	r->profile = (uint16_t)v[2];
	r->cluster = (uint16_t)v[3];
	return resolve(addr, strlen(addr), &r->dst);
}

/* AT+SENDUCAST:<addr>,<srcEP>,<dstEP>,<profile>,<cluster>,<data> */
static int cmd_senducast(char *args)
{
	struct tx_req r = { 0 };
	char *p = args + 1;
	char *data;
	char *comma;
	int err;

	if (!zbc_joined()) {
		return AT_ERR_NOT_JOINED;
	}
	if (args[0] != ':') {
		return AT_ERR_INVALID_PARAM;
	}
	/* The data may contain commas: cut it off after the fifth comma first. */
	comma = p;
	for (int i = 0; i < 5 && comma; i++) {
		comma = strchr(comma, ',');
		comma = comma ? comma + 1 : NULL;
	}
	if (!comma) {
		return AT_ERR_INVALID_PARAM;
	}
	comma[-1] = '\0';
	data = comma;
	if (strlen(data) > PAYLOAD_MAX) {
		return AT_ERR_MSG_TOO_LONG;
	}
	err = parse_target(&p, &r);
	if (err) {
		return err;
	}
	r.len = (uint8_t)strlen(data);
	memcpy(r.data, data, r.len);
	return send_unicast(&r, sreg_bit(0x10, S10_QUIET_SEND));
}

/* AT+SENDUCASTB:<len>,<addr>,<srcEP>,<dstEP>,<profile>,<cluster>, then '>' and <len> bytes */
static int cmd_senducastb(char *args)
{
	struct tx_req r = { 0 };
	char *p = args + 1;
	char *len_str;
	uint32_t len;
	int err;

	if (!zbc_joined()) {
		return AT_ERR_NOT_JOINED;
	}
	if (args[0] != ':') {
		return AT_ERR_INVALID_PARAM;
	}
	len_str = next_field(&p);
	if (!len_str || strlen(len_str) != 2 || at_parse_hex_n(len_str, 2, &len) || len == 0) {
		return AT_ERR_INVALID_PARAM;
	}
	if (len > PAYLOAD_MAX) {
		return AT_ERR_MSG_TOO_LONG;
	}
	err = parse_target(&p, &r);
	if (err) {
		return err;
	}
	if (at_uart_read_binary(r.data, len, BINARY_TIMEOUT) != 0) {
		return AT_ERR_XCASTB_TIMEOUT;
	}
	r.len = (uint8_t)len;
	return send_unicast(&r, sreg_bit(0x10, S10_QUIET_SEND));
}

/* ---------------------------------------------------------------------------
 * AT+MATCHREQ: ZDO Match_Desc_req broadcast; every answer becomes a MatchDesc: prompt.
 */
struct match_req {
	uint16_t profile;
	uint8_t n_in;
	uint8_t n_out;
	uint16_t clusters[2 * SREG_MAX_CLUSTERS];
};

static struct match_req match_pending; /* written by the AT thread, read in match_send */

static void match_resp(zb_bufid_t bufid)
{
	zb_zdo_match_desc_resp_t *resp = (zb_zdo_match_desc_resp_t *)zb_buf_begin(bufid);
	zb_apsde_data_indication_t *ind = ZB_BUF_GET_PARAM(bufid, zb_apsde_data_indication_t);
	const zb_uint8_t *ep = (const zb_uint8_t *)(resp + 1);
	char line[16 + 3 * 32];
	size_t n;

	/* One call per answering node, then one with TIMEOUT: nothing to print for that. */
	if (resp->status != ZB_ZDP_STATUS_TIMEOUT && resp->status != ZB_ZDP_STATUS_TIMEOUT_BY_STACK) {
		n = snprintf(line, sizeof(line), "MatchDesc:%04X,%02X", ind->src_addr, resp->status);
		for (int i = 0; resp->status == ZB_ZDP_STATUS_SUCCESS && i < resp->match_len &&
				n + 3 < sizeof(line); i++) {
			n += snprintf(&line[n], sizeof(line) - n, ",%02X", ep[i]);
		}
		at_print("%s", line);
	}
	zb_buf_free(bufid);
}

static void match_send(zb_bufid_t bufid)
{
	size_t n = match_pending.n_in + match_pending.n_out;
	zb_zdo_match_desc_param_t *req =
		zb_buf_initial_alloc(bufid, sizeof(*req) + n * sizeof(zb_uint16_t));

	/* To every node with the receiver on, end devices such as switches included. */
	req->nwk_addr = ZB_NWK_BROADCAST_RX_ON_WHEN_IDLE;
	req->addr_of_interest = ZB_NWK_BROADCAST_RX_ON_WHEN_IDLE;
	req->profile_id = match_pending.profile;
	req->num_in_clusters = match_pending.n_in;
	req->num_out_clusters = match_pending.n_out;
	memcpy(req->cluster_list, match_pending.clusters, n * sizeof(zb_uint16_t));
	if (zb_zdo_match_desc_req(bufid, match_resp) == ZB_ZDO_INVALID_TSN) {
		LOG_ERR("Match_Desc_req not sent");
		zb_buf_free(bufid);
	}
}

/* <NN>[,<cluster>...]: a 2-digit count followed by that many 4-digit cluster IDs. */
static int parse_cluster_list(char **p, uint8_t *count, uint16_t *out)
{
	char *field = next_field(p);
	uint32_t v;

	if (!field || strlen(field) != 2 || at_parse_hex_n(field, 2, &v) ||
	    v > SREG_MAX_CLUSTERS) {
		return AT_ERR_INVALID_PARAM;
	}
	*count = (uint8_t)v;
	for (uint8_t i = 0; i < *count; i++) {
		field = next_field(p);
		if (!field || strlen(field) != 4 || at_parse_hex_n(field, 4, &v)) {
			return AT_ERR_INVALID_PARAM;
		}
		out[i] = (uint16_t)v;
	}
	return AT_OK;
}

/* AT+MATCHREQ:<profile>,<NumIn>[,<InCluster>...],<NumOut>[,<OutCluster>...] */
static int cmd_matchreq(char *args)
{
	struct match_req req = { 0 };
	char *p = args + 1;
	char *profile;
	uint32_t v;
	int err;

	if (args[0] != ':') {
		return AT_ERR_INVALID_PARAM;
	}
	profile = next_field(&p);
	if (!profile || strlen(profile) != 4 || at_parse_hex_n(profile, 4, &v)) {
		return AT_ERR_INVALID_PARAM;
	}
	req.profile = (uint16_t)v;
	err = parse_cluster_list(&p, &req.n_in, req.clusters);
	if (!err) {
		err = parse_cluster_list(&p, &req.n_out, &req.clusters[req.n_in]);
	}
	if (err || p != NULL) {
		return AT_ERR_INVALID_PARAM; /* malformed, or fields left over */
	}
	if (!zbc_joined()) {
		return AT_ERR_NOT_JOINED;
	}
	match_pending = req;
	if (zb_buf_get_out_delayed(match_send) != RET_OK) {
		return AT_ERR_NO_BUFFERS;
	}
	return AT_OK;
}

const struct at_cmd zbm_cmds[] = {
	{ "+UCAST", cmd_ucast },
	{ "+BCAST", cmd_bcast },
	{ "+SENDUCAST", cmd_senducast },
	{ "+SENDUCASTB", cmd_senducastb },
	{ "+MATCHREQ", cmd_matchreq },
	{ NULL, NULL },
};

/* ---------------------------------------------------------------------------
 * Receive (ZBOSS thread)
 */
static void print_text(const char *kind, const char *eui_part, const zb_apsde_data_indication_t *ind,
		       const uint8_t *payload, size_t len)
{
	char head[40];
	char tail[16] = "";

	snprintf(head, sizeof(head), "%s:%s%02X=", kind, eui_part, (unsigned int)len);
	if (sreg_bit(0x0F, S0F_TEXT_RSSI)) {
		snprintf(tail, sizeof(tail), ",%d,%u", ind->rssi, ind->lqi);
	}
	at_print_parts(head, payload, len, tail);
}

static void print_rx(const char *eui_part, const zb_apsde_data_indication_t *ind,
		     const uint8_t *payload, size_t len)
{
	static char hex[2 * RX_SHOW_MAX + 1];
	char head[64];
	char tail[16] = "";

	len = MIN(len, RX_SHOW_MAX);
	snprintf(head, sizeof(head), "RX:%s%04X,%04X,%02X,%02X,%04X,%02X:", eui_part, ind->src_addr,
		 ind->profileid, ind->dst_endpoint, ind->src_endpoint, ind->clusterid,
		 (unsigned int)len);
	if (sreg_bit(0x0A, S0A_RX_RSSI)) {
		snprintf(tail, sizeof(tail), ",%d,%u", ind->rssi, ind->lqi);
	}
	if (sreg_bit(0x0F, S0F_RX_HEX)) {
		for (size_t i = 0; i < len; i++) {
			sprintf(&hex[2 * i], "%02X", payload[i]);
		}
		at_print_parts(head, (const uint8_t *)hex, 2 * len, tail);
	} else {
		at_print_parts(head, payload, len, tail);
	}
}

static zb_uint8_t data_indication(zb_bufid_t bufid)
{
	zb_apsde_data_indication_t *ind = ZB_BUF_GET_PARAM(bufid, zb_apsde_data_indication_t);
	const uint8_t *payload = zb_buf_begin(bufid);
	size_t len = zb_buf_len(bufid);
	char eui_part[18] = "";
	zb_ieee_addr_t ieee;

	if (ind->profileid == ZB_AF_ZDO_PROFILE_ID ||
	    (ind->dst_endpoint != EP_TEXT && ind->dst_endpoint != EP_APP)) {
		return ZB_FALSE;
	}
	if (zb_address_ieee_by_short(ind->src_addr, ieee) == RET_OK) {
		at_hex_rev(eui_part, ieee, 8);
		strcat(eui_part, ",");
	}

	if (ind->dst_endpoint == EP_TEXT && ind->profileid == TEXT_PROFILE &&
	    ind->clusterid == TEXT_CLUSTER) {
		if (!sreg_bit(0x0E, S0E_TEXT_OFF)) {
			print_text(ZB_NWK_IS_ADDRESS_BROADCAST(ind->dst_addr) ? "BCAST" : "UCAST",
				   eui_part, ind, payload, len);
		}
	} else if (sreg_bit(0x0F, S0F_SHOW_EP) && !sreg_bit(0x0F, S0F_SHOW_ALL)) {
		print_rx(eui_part, ind, payload, len);
	}
	/* Endpoints 1 and 2 belong to the host: ZBOSS never answers them itself. */
	zb_buf_free(bufid);
	return ZB_TRUE;
}

void zbm_init(void)
{
	ep2_build();
	ZB_AF_REGISTER_DEVICE_CTX(&at_ctx);
	zb_af_set_data_indication(data_indication);
	zb_aps_set_user_data_tx_cb(tx_done);
}
