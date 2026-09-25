/*
 * AT UART on the asynchronous (EasyDMA) UART API, modelled on the UART
 * handler of the nRF Serial Modem:
 *
 * - RX: the driver fills buffers from a slab; each UART_RX_RDY is queued by
 *   reference (the buffer is reference counted) and processed in a thread:
 *   echo, line assembly, binary entry for AT+SENDUCASTB. If RX stops (no
 *   free buffer), the thread enables it again.
 * - TX: output is written to a ring buffer that uart_tx() drains, chained on
 *   UART_TX_DONE. Writers hold a mutex for a whole line, so prompts from the
 *   Zigbee thread never interleave with command responses.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/ring_buffer.h>

#include "at.h"

LOG_MODULE_REGISTER(at_uart, LOG_LEVEL_INF);

#define RX_BUF_SIZE     256
#define RX_BUF_COUNT    3
#define RX_TIMEOUT_US   2000
#define RX_EVENT_COUNT  8
#define RX_RETRY_DELAY  K_MSEC(10)
#define TX_BUF_SIZE     1024
#define AT_OUT_MAX      256
#define LINE_Q_DEPTH    8
#define RX_STACK_SIZE   2048
#define RX_PRIORITY     6

static const struct device *const uart = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

/* ---------------------------------------------------------------------------
 * RX buffers: fixed-size slab blocks with a reference count, because the
 * driver reports received data as offsets into the block it is filling.
 */
struct rx_buf {
	atomic_t refs;
	uint8_t data[RX_BUF_SIZE];
};

BUILD_ASSERT((sizeof(struct rx_buf) % 4) == 0);
K_MEM_SLAB_DEFINE_STATIC(rx_slab, sizeof(struct rx_buf), RX_BUF_COUNT, 4);

struct rx_event {
	uint8_t *data;
	size_t len;
};

K_MSGQ_DEFINE(rx_events, sizeof(struct rx_event), RX_EVENT_COUNT, 4);

/* Set by UART_RX_DISABLED (e.g. no free buffer); the RX thread enables RX again. */
static atomic_t rx_disabled;

static struct rx_buf *rx_block(const uint8_t *p)
{
	size_t n = (p - (const uint8_t *)rx_slab.buffer) / sizeof(struct rx_buf);

	return (struct rx_buf *)&rx_slab.buffer[n * sizeof(struct rx_buf)];
}

static uint8_t *rx_buf_alloc(void)
{
	struct rx_buf *b;

	if (k_mem_slab_alloc(&rx_slab, (void **)&b, K_NO_WAIT) != 0) {
		return NULL;
	}
	atomic_set(&b->refs, 1);
	return b->data;
}

static void rx_buf_ref(const uint8_t *p)
{
	atomic_inc(&rx_block(p)->refs);
}

static void rx_buf_unref(const uint8_t *p)
{
	struct rx_buf *b = rx_block(p);

	if (atomic_dec(&b->refs) == 1) {
		k_mem_slab_free(&rx_slab, b);
	}
}

static int rx_enable(void)
{
	uint8_t *buf = rx_buf_alloc();
	int err;

	if (!buf) {
		return -ENOMEM;
	}
	err = uart_rx_enable(uart, buf, RX_BUF_SIZE, RX_TIMEOUT_US);
	if (err) {
		rx_buf_unref(buf);
	}
	return err;
}

/* ---------------------------------------------------------------------------
 * TX ring buffer. tx_idle is 1 while no DMA transfer is running.
 */
RING_BUF_DECLARE(tx_ring, TX_BUF_SIZE);
K_MUTEX_DEFINE(tx_lock);
K_SEM_DEFINE(tx_idle, 1, 1);

/* Start a transfer of what is in the ring. Called with tx_idle taken. */
static void tx_start(void)
{
	uint8_t *p;
	uint32_t n = ring_buf_get_claim(&tx_ring, &p, TX_BUF_SIZE);

	if (n == 0 || uart_tx(uart, p, n, SYS_FOREVER_US) != 0) {
		ring_buf_get_finish(&tx_ring, 0);
		k_sem_give(&tx_idle);
	}
}

/* Copy into the ring, starting transfers while it is full. tx_lock held. */
static void tx_write(const void *data, size_t len)
{
	const uint8_t *p = data;

	while (len > 0) {
		uint32_t n = ring_buf_put(&tx_ring, p, len);

		p += n;
		len -= n;
		if (len > 0) {
			(void)k_sem_take(&tx_idle, K_FOREVER);
			tx_start();
		}
	}
}

static void tx_flush(void)
{
	if (k_sem_take(&tx_idle, K_NO_WAIT) == 0) {
		tx_start();
	}
}

/* ---------------------------------------------------------------------------
 * Driver callback (interrupt context)
 */
static void uart_cb(const struct device *dev, struct uart_event *evt, void *user_data)
{
	struct rx_event ev;
	uint8_t *buf;

	ARG_UNUSED(dev);
	ARG_UNUSED(user_data);

	switch (evt->type) {
	case UART_TX_DONE:
	case UART_TX_ABORTED:
		ring_buf_get_finish(&tx_ring, evt->data.tx.len);
		if (ring_buf_is_empty(&tx_ring)) {
			k_sem_give(&tx_idle);
		} else {
			tx_start();
		}
		break;
	case UART_RX_RDY:
		rx_buf_ref(evt->data.rx.buf);
		ev.data = &evt->data.rx.buf[evt->data.rx.offset];
		ev.len = evt->data.rx.len;
		if (k_msgq_put(&rx_events, &ev, K_NO_WAIT) != 0) {
			LOG_ERR("RX event queue full, %u bytes dropped", evt->data.rx.len);
			rx_buf_unref(evt->data.rx.buf);
		}
		break;
	case UART_RX_BUF_REQUEST:
		/* Keep room for this buffer's events; otherwise let RX stop and recover. */
		if (k_msgq_num_free_get(&rx_events) < 2) {
			break;
		}
		buf = rx_buf_alloc();
		if (buf && uart_rx_buf_rsp(uart, buf, RX_BUF_SIZE) != 0) {
			rx_buf_unref(buf);
		}
		break;
	case UART_RX_BUF_RELEASED:
		if (evt->data.rx_buf.buf) {
			rx_buf_unref(evt->data.rx_buf.buf);
		}
		break;
	case UART_RX_DISABLED:
		/* A flag, not a queued event: it must not be lost when the queue is full. */
		atomic_set(&rx_disabled, 1);
		break;
	case UART_RX_STOPPED:
		LOG_WRN("UART RX stopped (reason %d)", evt->data.rx_stop.reason);
		break;
	default:
		break;
	}
}

/* ---------------------------------------------------------------------------
 * RX processing (thread): echo, lines, binary entry
 */
K_MSGQ_DEFINE(line_q, AT_LINE_MAX, LINE_Q_DEPTH, 4);
K_SEM_DEFINE(bin_done, 0, 1);

/* Queued instead of a line that did not fit (a full AT_LINE_MAX message is copied). */
static const char line_overflow[AT_LINE_MAX] = "\x01";

static char rx_line[AT_LINE_MAX];
static size_t rx_len;
static bool rx_overflow;
static bool echo_on = true;

static struct k_spinlock bin_lock;
static uint8_t *bin_buf;
static size_t bin_need;
static size_t bin_have;

/* Returns true if the byte went to a pending binary entry. */
static bool bin_byte(uint8_t c)
{
	k_spinlock_key_t key = k_spin_lock(&bin_lock);
	bool taken = bin_have < bin_need;

	if (taken) {
		bin_buf[bin_have++] = c;
		if (bin_have == bin_need) {
			bin_need = 0;
			k_sem_give(&bin_done);
		}
	}
	k_spin_unlock(&bin_lock, key);
	return taken;
}

static void line_byte(uint8_t c)
{
	switch (c) {
	case '\r':
		if (rx_overflow) {
			(void)k_msgq_put(&line_q, line_overflow, K_NO_WAIT);
		} else if (rx_len > 0) {
			rx_line[rx_len] = '\0';
			if (k_msgq_put(&line_q, rx_line, K_NO_WAIT) != 0) {
				LOG_WRN("Command queue full, line dropped");
			}
		}
		rx_len = 0;
		rx_overflow = false;
		break;
	case '\n':
		break;
	case '\b':
	case 0x7F:
		if (rx_len > 0) {
			rx_len--;
		}
		break;
	default:
		if (rx_len < AT_LINE_MAX - 1) {
			rx_line[rx_len++] = c;
		} else {
			rx_overflow = true;
		}
		break;
	}
}

static void rx_process(const uint8_t *data, size_t len)
{
	uint8_t echo[RX_BUF_SIZE];
	size_t n_echo = 0;

	for (size_t i = 0; i < len; i++) {
		if (bin_byte(data[i])) {
			continue; /* binary data is not echoed */
		}
		if (echo_on && n_echo < sizeof(echo)) {
			echo[n_echo++] = data[i];
		}
		line_byte(data[i]);
	}
	if (n_echo > 0) {
		k_mutex_lock(&tx_lock, K_FOREVER);
		tx_write(echo, n_echo);
		tx_flush();
		k_mutex_unlock(&tx_lock);
	}
}

static void rx_thread(void *a, void *b, void *c)
{
	struct rx_event ev;

	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	for (;;) {
		if (k_msgq_get(&rx_events, &ev, RX_RETRY_DELAY) == 0) {
			rx_process(ev.data, ev.len);
			rx_buf_unref(ev.data);
		}
		/* Once everything received is processed (buffers are free again), restart RX. */
		if (atomic_get(&rx_disabled) && k_msgq_num_used_get(&rx_events) == 0 &&
		    rx_enable() == 0) {
			atomic_set(&rx_disabled, 0);
		}
	}
}

K_THREAD_DEFINE(at_rx_tid, RX_STACK_SIZE, rx_thread, NULL, NULL, NULL, RX_PRIORITY, 0,
		SYS_FOREVER_MS);

/* ---------------------------------------------------------------------------
 * API
 */
int at_uart_init(void)
{
	int err;

	if (!device_is_ready(uart)) {
		return -ENODEV;
	}
	err = uart_callback_set(uart, uart_cb, NULL);
	if (err) {
		return err;
	}
	k_thread_start(at_rx_tid);
	return rx_enable();
}

int at_uart_get_line(char *out, k_timeout_t timeout)
{
	return k_msgq_get(&line_q, out, timeout);
}

void at_uart_inject_line(const char *line)
{
	char buf[AT_LINE_MAX];

	strncpy(buf, line, sizeof(buf) - 1);
	buf[sizeof(buf) - 1] = '\0';
	(void)k_msgq_put(&line_q, buf, K_NO_WAIT);
}

void at_uart_set_echo(bool on)
{
	echo_on = on;
}

int at_uart_read_binary(uint8_t *buf, size_t len, k_timeout_t timeout)
{
	k_spinlock_key_t key;

	k_sem_reset(&bin_done);
	key = k_spin_lock(&bin_lock);
	bin_buf = buf;
	bin_have = 0;
	bin_need = len;
	k_spin_unlock(&bin_lock, key);

	k_mutex_lock(&tx_lock, K_FOREVER);
	tx_write("\r\n>", 3);
	tx_flush();
	k_mutex_unlock(&tx_lock);

	if (k_sem_take(&bin_done, timeout) != 0) {
		key = k_spin_lock(&bin_lock);
		bin_need = 0;
		bin_have = 0;
		k_spin_unlock(&bin_lock, key);
		return -ETIMEDOUT;
	}
	return 0;
}

void at_print_parts(const char *head, const uint8_t *body, size_t body_len, const char *tail)
{
	k_mutex_lock(&tx_lock, K_FOREVER);
	tx_write("\r\n", 2);
	if (head) {
		tx_write(head, strlen(head));
	}
	if (body) {
		tx_write(body, body_len);
	}
	if (tail) {
		tx_write(tail, strlen(tail));
	}
	tx_write("\r\n", 2);
	tx_flush();
	k_mutex_unlock(&tx_lock);
}

void at_print(const char *fmt, ...)
{
	char line[AT_OUT_MAX];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	at_print_parts(line, NULL, 0, NULL);
}
