# Zigbee AT firmware (Telegesis R309 compatible)

## Overview

This firmware turns an **nRF54L15** into a Zigbee module that a host drives over UART with
the AT command set of the Telegesis ETRX3 **R309** firmware. It was validated on the
**nRF54L15 DK**.

Reference documentation (Silicon Labs, which owns the Telegesis products):

* [TG-ETRXn-R309 AT-Command Dictionary 3.09](https://www.silabs.com/documents/public/reference-manuals/TG-ETRXn-Commands.pdf)
  (syntax, prompts, S-registers, error codes)
* [TG-APP-0024 Using R3xx firmware in a Home Automation network](https://www.silabs.com/documents/public/application-notes/TG-APP-0024r3-UsingR3xxFirmwareinaHomeAutomationNetwork.pdf)

> **Note:** this firmware implements a **subset** of the R309 command set, not all of it.
> Commands, prompts and registers not listed in [AT interface](#at-interface) are not
> implemented, and the implemented ones may differ in details, see
> [Differences from R309](#differences-from-r309).

There is **one image for every role**. The host decides what the node is:

* `AT+EN` forms a network: the node becomes coordinator and Trust Centre.
* `AT+JN` joins a network as router or end device, as selected by bits F–E of register `S0A`.

Two kinds of application data are supported:

* **Text** on endpoint 1 (profile `0xC091`, cluster `0x0002`, as in R309): `AT+UCAST`,
  `AT+BCAST`, and the `UCAST:`/`BCAST:` prompts.
* **Raw ZCL/ZDO** on endpoint 2, whose descriptor is set with `S48`–`S4C`: `AT+SENDUCAST[B]`
  to send, `RX:` prompts to receive. The host builds and parses the ZCL frames.

The module can be used by hand from any serial terminal (see
[Using the module from a serial terminal](#using-the-module-from-a-serial-terminal)). The
Python scripts in `host/` automate exactly those manual procedures for three example roles:
a generic coordinator, an On/Off light (router) and an On/Off switch (end device), plus an
automated test of a complete three-node network.

## Requirements

* nRF54L15 DK (`nrf54l15dk/nrf54l15/cpuapp`), one per node.
* [Zigbee R23 add-on](https://github.com/nrfconnect/ncs-zigbee) v1.4.0 with nRF Connect SDK
  v3.4.0 (pulled by the add-on manifest), and `nrfutil` with the `sdk-manager` command.
* Python 3.10 or newer with the packages in `host/requirements.txt` (`pyserial`, `pytest`)
  for the host scripts and tests. Not needed to use the module from a terminal.

## Memory usage on the nRF54L15

Release build of this repository (`scripts/build.ps1`), nRF54L15 application core:

| Region | Used | Available | Usage |
|---|---|---|---|
| RRAM (code + read-only data) | 400 064 B (≈ 391 KB) | 1524 KB | 25.6 % |
| RAM | 91 492 B (≈ 89 KB) | 256 KB | 34.9 % |

Non-volatile data uses the partitions of the Zigbee add-on layout: `storage_partition`
(8 KB, S-registers and network state through Zephyr settings) and `zboss_nvram` (32 KB,
Zigbee stack data).

## AT interface

UART: the DK virtual COM port of the application core, **115200 baud, 8N1**, no flow control.
Commands end with `<CR>`; every response and prompt is sent as `<CR><LF>text<CR><LF>`. Typed
characters are echoed unless bit 4 of `S12` is set. The UART uses the asynchronous (EasyDMA)
driver, with the buffering scheme of the nRF Serial Modem: received data is handled in a thread,
output goes through a ring buffer, and prompts never interleave with command responses.

### Commands

| Command | Description |
|---|---|
| `AT` | Returns `OK`. |
| `ATI` | `Telegesis nRF54L15`, `R309N`, EUI64. |
| `ATZ` | Reset. A node that was in a network rejoins it (`JPAN:` prompt). |
| `AT&F` | Leave the network, restore factory S-registers, reset. |
| `ATSXX?` / `ATSXX=<value>[:<password>]` | Read / write S-register `XX`. |
| `ATSXXb?` / `ATSXXb=<0/1>[:<password>]` | Read / write bit `b` of S-register `XX`. |
| `AT+TOKDUMP` | List all S-registers. |
| `AT+EN` | Form a network as coordinator (channel mask `S00`, PAN `S02`, EPID `S03`). |
| `AT+JN` | Join a network (channel mask `S00`, EPID filter `S03`). |
| `AT+JPAN:<ch>,<PID or EPID>` | Join on one channel, optionally a given EPID. |
| `AT+DASSL` | Leave the network (`LeftPAN`). |
| `AT+N` | `+N=<type>,<channel>,<power>,<PAN>,<EPID>` or `+N=NoPAN`. |
| `AT+UCAST:<addr>=<text>` | Text unicast (endpoints, cluster and profile from `S40`/`S42`/`S44`). |
| `AT+BCAST:<nn>,<text>` | Text broadcast to routers and coordinator (`0xFFFC`). |
| `AT+SENDUCAST:<addr>,<srcEP>,<dstEP>,<profile>,<cluster>,<text>` | Unicast with explicit addressing. |
| `AT+SENDUCASTB:<len>,<addr>,<srcEP>,<dstEP>,<profile>,<cluster>` | Same, binary: after `>` send `<len>` raw bytes. |
| `AT+MATCHREQ:<profile>,<nIn>[,<in>…],<nOut>[,<out>…]` | Find the nodes with an endpoint of this profile and at least one of these clusters (ZDO Match_Desc_req to every node with the receiver on). `OK`, then one `MatchDesc:` prompt per answering node. Up to 8 clusters per list; counts are 2 hex digits. |

`<addr>` is a 4-digit network address (`0000` is always the coordinator) or a 16-digit EUI64.
An unknown EUI64 is looked up with a ZDO NWK_addr_req (3 attempts, about 6 s in total).
Unicasts print `SEQ:XX` then `OK`, and later `ACK:XX` or `NACK:XX`. For a destination that
does not exist, the stack gives up and prints `NACK:XX` after about a minute.

### Prompts

`OK`, `ERROR:XX`, `SEQ:XX`, `ACK:XX`, `NACK:XX`, `JPAN:<ch>,<PAN>,<EPID>`, `LeftPAN`,
`MatchDesc:<NWK>,<status>[,<EP>…]` (answer to `AT+MATCHREQ`: `00` and the matching endpoints),
`NEWNODE:<NWK>,<EUI64>,<parent>` (coordinator), `FFD:`/`ZED:`/`SED:<EUI64>,<NWK>` (device
announce), `NODELEFT:<NWK>,<EUI64>` (coordinator, `S0F` bit B),
`UCAST:[<EUI64>,]XX=<data>[,<RSSI>,<LQI>]`, `BCAST:…`,
`RX:[<EUI64>,]<NWK>,<profile>,<dstEP>,<srcEP>,<cluster>,<len>:<payload>[,<RSSI>,<LQI>]`
(shown when `S0F` bit 8 is set and bit 1 is clear; payload in hex when `S0F` bit C is set).

### S-registers

| Reg | Meaning | Default |
|---|---|---|
| `S00` | Channel mask, bit 0 = channel 11 | `FFFF` |
| `S01` | TX power [dBm], -40 to 8. The nRF54L15 transmits at most +7 dBm in the QFN package (nRF54L15 DK) and +8 dBm in the CSP package | `07` |
| `S02` | PAN ID for formation (0 = random) | `0000` |
| `S03` | Extended PAN ID: formation (0 = this node's EUI64) and join filter (0 = any network) | `0000000000000000` |
| `S04` / `S05` | Local EUI64 / network address (read only) | |
| `S08` | Network key for formation (0 = random; write only, password) | 0 |
| `S09` | Preconfigured Trust Centre link key, used when `S0A` bit 8 is set (write only, password) | `ZigBeeAlliance09` |
| `S0A` | Main function (password), see [Security and join control](#security-and-join-control): bits F–E device type (`00` router, `10` end device, `01` SED, `11` MED), A no TC link key request, 8 use the `S09` link key, 6 RSSI/LQI on `RX:`, 5 TC blocks joining network-wide, 4 send the network key encrypted, 3 no unsecured rejoin, 0 no joining through this node | `0000` |
| `S0B` | User readable name | empty |
| `S0C` | Password (write only) | `password` |
| `S0D` | Device information (read only) | |
| `S0E` / `S0F` | Prompt enable 1 / 2 (see the R309 manual) | `0000` / `0006` |
| `S10` | Extended function: bit B hides `SEQ`/`ACK`/`NACK` of `AT+SENDUCAST[B]` | `0000` |
| `S12` | UART: high byte must be `0C` (115200); bit 4 turns echo off | `0C00` |
| `S40`–`S45` | Endpoints, cluster and profile used by `AT+UCAST`/`AT+BCAST` | `0101`, `0002`, `C091` |
| `S48`–`S4C` | Endpoint 2: profile, device ID, version, input and output clusters (applied after a reset) | `C091`, `0000`, `00`, empty, empty |

### Security and join control

Everything is set with `S09` and `S0A` bits, as in R309, before `AT+EN` / `AT+JN` unless noted.

| Setting | Coordinator (Trust Centre) | Router / end device |
|---|---|---|
| `S09` + `S0A` bit 8: preconfigured TC link key | Encrypts the network key it sends with the `S09` key (instead of `ZigBeeAlliance09`) | Uses the `S09` key to decrypt the network key |
| `S0A` bit 4: send the network key encrypted | Clear (R309 default): the network key is sent without link key encryption, so the link keys do not matter. Set: encrypted, joining nodes need the same link key | — (a joining node accepts both, like R309 nodes) |
| `S0A` bit 3: no unsecured rejoin | Refuses Trust Centre (unsecured) rejoins | — |
| `S0A` bit 5: block joining network-wide | The Trust Centre accepts no new node, whatever its parent (takes effect at once) | — |
| `S0A` bit 0: no joining through this node | Closes joining through the coordinator; routers can still let nodes in (takes effect at once) | Closes joining through this router, also when the network is opened again (takes effect at once) |
| `S0A` bit A: no TC link key request | — | Needed to stay in a pre-Zigbee 3.0 network, such as the one this firmware's coordinator forms |

Writing `S0A` needs the password (`ATS0A<bit>=1:password`, see `S0C`). A new `S09` key is
used from the next `AT+EN` / `AT+JN`.

### Network parameters: where to set the channel, PAN ID and extended PAN ID

| | Coordinator (forms the network) | Router / end device (joins) |
|---|---|---|
| Channel | `S00` mask before `AT+EN` | `S00` mask before `AT+JN` (only speeds up the scan), or `AT+JPAN:<ch>,…` |
| PAN ID | `S02` before `AT+EN` (0 = random) | not used |
| Extended PAN ID | `S03` before `AT+EN` (0 = this node's EUI64) | `S03` before `AT+JN`: join only this network (0 = first open network) |
| Python scripts | `--channel`, `--pan`, `--epid` | `--channel`, `--epid` |

The PAN ID (16 bits) and the extended PAN ID (64 bits) are **independent**. The PAN ID is the
network address used on the radio and can change if two networks collide; the extended PAN
ID is the stable identity of the network, the value joining devices filter on (much like an
SSID). There is no rule that one is derived from the other.

### Differences from R309

* Default baud rate 115200 instead of 19200; only the echo bit of `S12` can be changed.
* `ATI` reports `nRF54L15` / `R309N`.
* Sleepy and mobile end devices (`S0A` = `01`/`11`) join as non-sleepy end devices.
* `AT+DASSL` and `AT&F` reset the module after leaving.
* The coordinator is a pre-Zigbee 3.0 Trust Centre, like R309: it ignores the TC link key
  requests of Zigbee 3.0 nodes, so joining nodes set `S0A` bit A.
* The sender EUI64 in `UCAST:`/`BCAST:`/`RX:` is shown when the node knows it.
* `AT+BCAST` validates the hop count but uses the default radius.
* `NEWNODE:` shows the parent as `FFFF`.
* `AT+JN` filters on the EPID only; `AT+JPAN` with a PAN ID restricts the channel only.
* Not implemented: `AT+PANSCAN`, `AT+ESCAN`, the ZDO requests other than `AT+MATCHREQ`,
  binding, multicast, sink, data mode, I/O, ADC, timers, remote S-register access,
  `S06`/`S07`. `S0A` bit 2 (network key encryption on unsecured rejoin) is stored only:
  the stack always encrypts it.

## Using the module from a serial terminal

Open the DK's VCOM port with any terminal (PuTTY, Tera Term, minicom, the nRF Connect Serial
Terminal…): **115200 baud, 8N1, no flow control**, Enter sends CR, **local echo off** (the
module echoes what you type). The scripts in `host/` send exactly these commands.

**Coordinator** (forms a network and reports who joins):

```
AT&F                                      OK
ATS00=0200                                OK      only channel 20 (bit 9 = 11 + 9)
ATS02=7A31                                OK      PAN ID
ATS03=00000000000A1B2C                    OK      extended PAN ID
ATS0F=1904                                OK      show NODELEFT and RX: frames (hex payload)
AT+EN                                     JPAN:20,7A31,00000000000A1B2C
                                          OK
... when devices join:                    NEWNODE:6CBF,F4CE36000000B001,FFFF
                                          FFD:F4CE36000000B001,6CBF
```

**Router** (the bulb's role):

```
AT&F                                      OK
ATS00=0200                                OK      only channel 20 (optional, faster scan)
ATS03=00000000000A1B2C                    OK      join only this network
ATS0AA=1:password                         OK      no TC link key request (legacy TC)
AT+JN                                     JPAN:20,7A31,00000000000A1B2C
                                          OK
AT+N                                      +N=FFD,20,07,7A31,00000000000A1B2C
                                          OK
```

**End device** (the switch's role): as the router, plus `ATS0AF=1:password` before `AT+JN`;
`AT+N` then reports `ZED`.

**Finding a device by function** (from any node; here a switch looks for On/Off lights,
i.e. endpoints with profile `0104` and cluster `0006` as input, which the bulb declares
with `ATS48=0104`, `ATS4B=0000,0003,0006` and a reset):

```
AT+MATCHREQ:0104,01,0006,00               OK
                                          MatchDesc:6CBF,00,02    node 6CBF, endpoint 02
```

The answer gives the node's network address and endpoint, ready for
`AT+SENDUCASTB:<len>,6CBF,02,02,0104,0006`.

**Messages** (from any node):

```
AT+UCAST:0000=hello                       SEQ:00             unicast to the coordinator
                                          OK
                                          ACK:00             the coordinator received it
AT+UCAST:F4CE36000000B001=hello           ...                unicast by EUI64
AT+BCAST:00,hello                         OK                 broadcast to routers + coordinator
```

The receiver prints `UCAST:<sender EUI64>,05=hello` (or `BCAST:…`). End devices do not
receive broadcasts to `0xFFFC`: this is Zigbee behaviour, not a limitation of the
firmware. They receive unicasts normally.

## Building and running

1. Install the toolchain and the add-on workspace (once):

   ```powershell
   nrfutil sdk-manager toolchain install --ncs-version v3.4.0
   nrfutil sdk-manager toolchain launch --ncs-version v3.4.0 -- west init -m https://github.com/nrfconnect/ncs-zigbee --mr v1.4.0 C:\ncs\ncs-zigbee
   cd C:\ncs\ncs-zigbee
   nrfutil sdk-manager toolchain launch --ncs-version v3.4.0 -- west update
   ```

2. Build (from this repository):

   ```powershell
   .\scripts\build.ps1            # -Pristine for a clean build, -Workspace <path> if needed
   ```

   The equivalent `west` command, run from the workspace:

   ```powershell
   nrfutil sdk-manager toolchain launch --ncs-version v3.4.0 -- west build -b nrf54l15dk/nrf54l15/cpuapp <repo>/app --build-dir <repo>/build
   ```

3. Program every DK with the same image (`nrfutil device list` shows the serial numbers):

   ```powershell
   .\scripts\flash.ps1 -SerialNumber <sn1>,<sn2>,<sn3> -Erase
   ```

   `-Erase` also clears the S-registers and the Zigbee network data.

4. Install the host requirements (only for the scripts and tests):

   ```powershell
   cd host
   python -m pip install -r requirements.txt
   ```

## Host scripts

Each script automates the manual procedure above for one role. Its full documentation (AT
commands sent, prompts printed, interactive commands and the AT command behind each one,
examples, troubleshooting) is at the top of the file and in `python <script> --help`.

| Script | Role | Interactive commands |
|---|---|---|
| `coordinator.py` | Generic coordinator: forms the network, prints joins, announces, leaves and received messages | `bcast <text>`, `ucast <addr> <text>`, `info` |
| `bulb.py` | Router, HA On/Off Light on endpoint 2: follows ZCL On/Off/Toggle, answers with a Default Response | `say <addr> <text>`, `state` |
| `switch.py` | End device, HA On/Off Switch on endpoint 2: finds a bulb with `AT+MATCHREQ` (or `--bulb <EUI64>` for manual binding) and sends it ZCL On/Off/Toggle | `on`, `off`, `t`, `find`, `say <addr> <text>` |
| `e2e_test.py` | Runs the three roles on three DKs and checks the whole network (PASS/FAIL per step) | — |

Common options: `--port`, `--channel`, `--pan` (coordinator), `--epid`, `--legacy` (joiners,
on by default), `--reset`, `-v` (show the AT traffic). Example with three terminals in `host/`:

```powershell
python coordinator.py --port COM31 --channel 20 --reset
python bulb.py --port COM36 --channel 20 --reset
python switch.py --port COM7 --channel 20 --reset          # finds the bulb with AT+MATCHREQ
```

Typing `on`, `off` or `t` in the switch prints `status 00`, and the bulb prints
`bulb: light is ON` / `OFF`. `say 0000 hello` on the bulb or the switch prints
`ucast from <EUI64>: hello` on the coordinator.

## Testing

### Unit tests (no hardware)

```powershell
cd host
python -m pytest -q
```

Hardware tests are skipped unless their serial ports are given.

### Firmware tests on hardware

```powershell
cd host
python -m pytest tests/hil/test_basic.py tests/hil/test_sreg.py --coord-port COM31      # one DK
python -m pytest tests/hil --coord-port COM31 --bulb-port COM36 --switch-port COM7        # three DKs
```

The ports can also be given with the `ZB_AT_COORD`, `ZB_AT_BULB` and `ZB_AT_SWITCH`
environment variables.

### Automated three-DK test

```powershell
cd host
python e2e_test.py --coord COM31 --bulb COM36 --switch COM7
```

The test factory-resets the three DKs, forms a network, joins the bulb (router) and the switch
(end device), checks that the switch finds the bulb with `AT+MATCHREQ`, and checks ZCL On/Off control, acknowledged text unicasts in all directions, a
broadcast (routers only), rejoin after a reset, and that no node leaves during a soak period
(`--soak`, 60 s by default). Use `--channel`, `--pan` and `--epid` to test a fixed network.

### Joining an existing pre-Zigbee 3.0 network

Use the network's channel and extended PAN ID and skip the TC link key request:

```powershell
python bulb.py --port COM36 --channel <ch> --epid <EPID> --legacy --reset
```

## Host library

`host/etrx` provides:

* `Etrx`: one module on a serial port. `cmd()` sends a command and returns the lines printed
  before `OK` (raises `EtrxError` on `ERROR:XX`). Prompts are parsed into objects (`Text`,
  `Rx`, `Jpan`, `NewNode`, `Ack`, …), passed to listeners and kept for `wait_for()`. Helpers:
  `sreg_get`/`sreg_set`, `form`, `join`, `leave`, `network`, `ucast`, `bcast`, `senducast[b]`.
* `zcl`: On/Off, Default Response and Read Attributes frames.
* `net`: `NetworkOptions` and the common set-up steps (`ensure_coordinator`, `ensure_joined`,
  `configure_endpoint2`, `wait_network`).

Each of `coordinator.py`, `bulb.py` and `switch.py` holds its role in one class (`Coordinator`,
`Bulb`, `Switch`) with a small command-line front end. New host applications and tests import
these classes instead of repeating their logic, as `e2e_test.py` does.

## Limitations

See [Differences from R309](#differences-from-r309). Planned: interoperability with stock Zigbee 3.0 devices (coordinator in
Zigbee 3.0 Trust Centre mode, joining Zigbee 3.0 networks with TC link key exchange), sleepy
end devices and binding.
