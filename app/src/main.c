/*
 * Telegesis R309-compatible AT firmware for the nRF54L15 DK.
 *
 * One image for all Zigbee roles: the host decides over the AT interface
 * whether the node forms a network (AT+EN) or joins one as router or end
 * device (S0A bits F-E, AT+JN).
 */
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

#include "at.h"
#include "sreg.h"
#include "zb_core.h"
#include "zb_msg.h"

LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

int main(void)
{
	int err = at_uart_init();

	if (err) {
		LOG_ERR("AT UART not ready (%d)", err);
		return 0;
	}
	err = settings_subsys_init();
	if (err) {
		LOG_ERR("Settings init failed (%d)", err);
	}
	sreg_init();
	zbm_init();
	at_cmd_start();
	zbc_init();
	LOG_INF("AT firmware %s ready", AT_FW_REVISION);
	return 0;
}
