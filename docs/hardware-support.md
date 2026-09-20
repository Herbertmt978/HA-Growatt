# Hardware and control evidence

Reading telemetry, reading a setting and successfully changing that setting are
separate capabilities. A register that returns zero may still ignore writes.
HA Growatt checks replies and reads settings back before reporting success.

The table below records evidence checked on 20 September 2026. Community results
describe the reported equipment and connection. They are not a claim that those
owners tested HA Growatt.

| Hardware or behaviour | Evidence | Treatment in HA Growatt |
| --- | --- | --- |
| Existing two-inverter installation | The earlier production migration verified both feeds, all 64 rendered sensor strings and retained registry/history/statistics identities. | Existing telemetry remains supported. This does not qualify battery controls. |
| SPH/SPA schedule blocks | The manufacturer documents three charging periods at 1100–1108 and three discharge periods at 1080–1088. | Explicit SPH/SPA control profile and experimental-controls option required. Read all three values, write the whole period once, then verify it. |
| SPA3000TL BL through Shine datalogger | Fez's issue reports successful setting reads, incomplete or inconsistent writes, and later confirmation timeouts. Its latest correction still awaited the reporter's confirmation when checked. | Do not label this combination hardware-verified. Repeat confirmation reads only; a failed write is never silently retried. |
| MID 25KTL3-XH, protocol 2.02, through a Modbus gateway | A community report confirms charge stop at register 3048 and discharge reserve at 3067 through measured battery behaviour. It also identifies ineffective SPH-register writes. | Separate MOD/MID TL3-XH control profile. Never expose the SPH battery block on MOD/MIN. Additional controls remain opt-in because the datalogger path is not established by that report. |
| MIN TL-XH | Manufacturer register table describes charge rate 3047, charge limit 3048 and grid-charge enable 3049. | Explicit MIN TL-XH profile; successful reads required. No blanket claim for all MIN models, and no SPH schedules. |
| SPH with newer encrypted Shine traffic | An issue reports packets arriving without decodable telemetry in both Grott and Fez's integration. | Show a connection/profile warning when packets arrive without readings. Session-key encryption is not added by this release. |
| Older 5500MTL-S register access | Grott has an unresolved report of register read/write failures. | A telemetry connection alone does not enable a control; the requested setting must respond correctly. |

Sources:

- [Growatt Modbus V1.24](https://www.amosplanet.org/wp-content/uploads/2023/06/Growatt-Inverter-Modbus-RTU-Protocol_II-V1_24-English.pdf), holding-register tables.
- [SPA3000TL BL control reports](https://github.com/FezVrasta/growatt-datalogger/issues/2) and [confirmation-read correction](https://github.com/FezVrasta/growatt-datalogger/pull/6).
- [MID 25KTL3-XH field testing](https://github.com/0xAHA/Growatt_ModbusTCP/issues/362).
- [SPH decoding report](https://github.com/FezVrasta/growatt-datalogger/issues/3).
- [Older 5500MTL-S register report](https://github.com/johanmeijer/grott/issues/721).

## Additional battery controls

Enable experimental controls only after selecting the exact control family in
the app. Keep the standard profile for an ordinary grid-tied MOD/MIN without the
matching battery interface.

| Control profile | Setting | Register | Accepted range |
| --- | --- | --- | --- |
| MIN TL-XH; MOD/MID TL3-XH | Battery charge power | 3047 | 1–100% |
| MIN TL-XH; MOD/MID TL3-XH | Battery charge limit | 3048 | 0–100% |
| MIN TL-XH; MOD/MID TL3-XH | Allow grid charging | 3049 | Off/on |
| MOD/MID TL3-XH | Battery discharge reserve | 3067 | 10–100% |

The discharge floor uses the conservative minimum supported by the cited field
report. Charge limit and discharge reserve can apply during ordinary
self-consumption, not just a forced charging/discharging period. Grid-charge
enable permits charging; it does not create a charging schedule by itself.

Register 1044 has conflicting write claims and is read-only in the manufacturer
document used here. It remains excluded. Arbitrary register writes, grid codes
and protection settings are not added to the app.

## Reporting a combination

Include the inverter model and firmware, datalogger model and firmware, app
version, selected profiles and downloaded diagnostics. For a control, say
whether its initial read succeeded, whether the read-back matched, and whether
the inverter's actual behaviour changed. Do not post passwords, serial numbers
or raw captures. A support file deliberately contains no packet contents.
