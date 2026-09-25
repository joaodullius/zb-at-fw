#
# Telegesis R309-compatible AT firmware for the nRF54L15 DK.
#

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})

project(zb_at_fw)

target_sources(app PRIVATE
  src/main.c
  src/at_uart.c
  src/at_fmt.c
  src/at_cmd.c
  src/sreg.c
  src/zb_core.c
  src/zb_legacy_tc.c
  src/zb_msg.c
  src/zboss_mem_globals.c
)

# zb_legacy_tc.c changes the ZBOSS Trust Center policy, which only the internal
# ZBOSS headers expose (the add-on builds lib/zboss/src with them too).
set_source_files_properties(src/zb_legacy_tc.c PROPERTIES
  INCLUDE_DIRECTORIES ${ZEPHYR_NCS_ZIGBEE_MODULE_DIR}/lib/zboss/src/include
)

# Keep the ZBOSS memory globals out of LTO (see the NCS Zigbee coordinator sample).
if(CONFIG_LTO)
  set_source_files_properties(
    src/zboss_mem_globals.c
    PROPERTIES COMPILE_OPTIONS -fno-lto
  )
endif()
