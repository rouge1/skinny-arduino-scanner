# ESP32 Board Specs

Source board identified from `esp32.png`: a **ESP32 DEVKIT V1** (30-pin, micro-USB, ESP-WROOM-32 module).
Amazon search reference: `esp-wroom-32` (appears to be the DOIT ESP32 DevKit V1 / ESP-32S family).

## Confirmed Hardware (queried live via esptool)

Verified on 2026-09-24 with esptool (first on the Mac mini, then on this Linux PC).

| Field | Reading | Matches docs? |
|-------|---------|---------------|
| Port | `/dev/ttyUSB0` | — |
| USB-UART bridge | Silicon Labs **CP2102** (VID `0x10C4`, PID `0xEA60`) | ✅ docs list CP2102 |
| Chip type | **ESP32-D0WD-V3** (revision v3.1) | ✅ ESP32-D0WD family |
| Features | Wi-Fi, BT, Dual Core + LP Core, 240 MHz | ✅ |
| Crystal | 40 MHz | ✅ |
| Flash | **4 MB** (GigaDevice `c4`, device `6016` = GD25Q32) | ✅ docs list 4 MB |
| MAC | `04:b2:47:06:0a:c0` | — |

Result: **the connected board matches the documentation** (classic ESP-WROOM-32 / ESP32-D0WD family, dual-core 240 MHz Wi-Fi+BT, 4 MB flash, CP2102 USB bridge).

## Board Identification (from image)

- **Board name (silkscreen):** ESP32 DEVKIT V1
- **Module:** ESP-WROOM-32
- **Form factor:** 30-pin DevKit, dual-row headers, 4 corner mounting holes
- **USB:** Micro-USB (bottom edge, next to EN and BOOT buttons)
- **Buttons:** EN (reset) + BOOT (download)
- **Regulatory markings on module:** Wi-Fi (FCC), CE, model ESP-WROOM-32, FCC ID `2AC7Z-ESPWROOM32`
- **Silkscreen pin labels (front):**
  - Left header: `3V3 GND D15 D2 D4 RX2 TX2 D5 D18 D19 D21 RX0 TX0 D22 D23`
  - Right header: `VN VP EN D34 D35 D32 D33 D25 D26 D27 D14 D12 D13 GND D23`
  - Bottom labels: `3V3 GND D15 D2 ...` and `GND`

## Core SoC (ESP32-D0WD)

| Spec | Value |
|------|-------|
| Chip | Espressif ESP32-D0WD (in ESP-WROOM-32 module) |
| Architecture | Xtensa LX6 |
| Cores | Dual-core (2 cores, up to 240 MHz) |
| SRAM | 520 KB |
| ROM | 448 KB |
| Flash | 4 MB (typical for this module) |
| GPIO | 30-pin breakout exposes ~25 usable GPIOs |
| ADC | 2x 12-bit SAR ADC (18 channels, some unusable on Wi-Fi variants) |
| DAC | 2x 8-bit |
| Touch | 10 capacitive touch GPIOs |
| PWM | 16 channels |
| UART | 3x |
| SPI | 4x (HSPI, VSPI) |
| I2C | 2x |
| I2S | 2x |
| Timers | 4x 64-bit |

## Wireless

| Spec | Value |
|------|-------|
| Wi-Fi | 2.4 GHz 802.11 b/g/n |
| Bluetooth | Bluetooth Classic + BLE (BT 4.2 on later revisions) |
| Antenna | Integrated PCB antenna (some variants have U.FL/IPEX option) |

## Power & Electrical

| Spec | Value |
|------|-------|
| Input voltage | 5 V (micro-USB) |
| Operating voltage | 3.3 V logic |
| On-board regulator | 3.3 V LDO (AMS1117-class) |
| USB-to-UART | CP2102 or CH340/CH9102 (varies by clone) |
| Current draw | ~40-240 mA typical (Wi-Fi TX peaks higher) |

## Variants seen in Amazon `esp-wroom-32` search

- Classic **ESP-WROOM-32 / ESP-32S DevKit** — dual-core, 2.4 GHz Wi-Fi + BT.
  - **Micro-USB** (e.g. HiLetgo, DOIT) vs **USB-C/Type-C** (ELEGOO, Hosyond, AITRIP).
  - **30-pin** vs **38-pin** (38-pin exposes extra GND/IO pins on the sides).
  - USB-UART bridge: **CP2102** (common), **CH340C**, or unmarked clones.
- **ESP32-S3** variant (sponsored listings): ESP32-S3-WROOM-1 **N16R8** = 16 MB flash / 8 MB PSRAM, 44-pin, Type-C.
- Accessory boards: ESP32 breakout/GPIO expanders (30/38-pin), ESP32-CAM-MB (OV2640 + CH340G).

> Note: The Amazon search page did not list flash/PSRAM per product; values above for the classic `ESP-WROOM-32` module are the standard Espressif datasheet figures.

## Programming notes

- Default boot mode; hold **BOOT** + press **EN** to enter download mode if auto-reset fails.
- Arduino board target used here: `esp32:esp32:esp32` with `PartitionScheme=huge_app`
  (set in `tools/esp32-ai/sketch.yaml`; WiFi + BLE doesn't fit the default 1.2 MB app partition).
  `esp32:esp32:esp32doit-devkit-v1` also matches this board.
- Requires `arduino-cli core install esp32:esp32` (3.3.12 installed).