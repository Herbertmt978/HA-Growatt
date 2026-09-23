# Hardware identification and register tools

These tools are available from 0.5.0. Update both the app and companion
before using the Home Assistant actions. They send holding-register reads over
the existing datalogger connection; they do not enable controls or write registers.

## Identify an inverter

In the app web UI, open **Identify hardware and inspect registers** below an
inverter, then select **Identify hardware**. Keep the datalogger connected and
allow up to 45 seconds. Unsupported or unrecognised fields stay unconfirmed.

Compare reported model text with the physical label. Some firmware returns
abbreviated text, and a family code can cover several models. If the details are
correct, select **Copy reported details into the form**, review them and use
**Save inverter details**. Copying alone does not save anything. The wizard never
selects a decoder, enables a battery control or qualifies a model for writes.

| Read | Interpretation | Evidence |
| --- | --- | --- |
| Holding 9–14 | Two ASCII inverter firmware identifiers | Growatt Modbus RTU V1.24 |
| Holding 125–132 | Model text, where implemented; reserved on some hardware | Growatt Modbus RTU V1.24 |
| Holding 30000 | Device Type Code (DTC); a family hint, not an exact model | Growatt VPP V2.03 table published by Growatt_ModbusTCP |
| Holding 30099 | Raw VPP protocol version code, where implemented | Same VPP table |

The family lookup covers DTC 5100, 5200 and 5400. **5200 can mean MIC or MIN**,
and 5400 covers MOD and MID variants. Other codes remain visible without an
inferred family. Zero-filled and all-ones codes are not identification evidence.
The VPP code does not prove datalogger command support; inverter firmware is
not logger firmware.

Sources: [manufacturer register table](https://www.amosplanet.org/wp-content/uploads/2023/06/Growatt-Inverter-Modbus-RTU-Protocol_II-V1_24-English.pdf)
and [Growatt_ModbusTCP's VPP documentation](https://0xaha.github.io/Growatt_ModbusTCP/developer/protocol-vpp/).
These supply register facts; the implementation is written for HA Growatt.
Some MIN devices have holding ranges that exclude 125–132, so unavailable
model text is expected on some otherwise working feeds.

## Read and compare a register block

Use the register table for the exact inverter model. Enter a decimal, zero-based
holding address and a count from 1 to 32. Select **Read registers**, then wait a
few seconds before **Read again and compare**. Changed addresses show previous
and current values. Values are raw unsigned 16-bit words, without assumed
units, signedness or scaling. Input registers and arbitrary writes are not exposed.

The comparison uses the preceding successful read for this inverter, including
reads through the companion. It expires after ten minutes, an app restart,
a connection change, a profile or hardware-details change, or a read of a
different range. Start a new first reading if a comparison is rejected.

**Download private register report** saves the current result and any comparison.
Registers can contain serial numbers and other private values; do not post the
file publicly. These results are excluded from ordinary redacted diagnostics,
retained MQTT state and the reading recovery cache. The app keeps only the
latest comparison block per discovered inverter in memory. Expired blocks are
discarded when another diagnostic request arrives.

## Home Assistant actions

Administrators can use **Developer tools → Actions**:

- **HA Growatt: Identify hardware** takes an inverter device and returns its report.
- **HA Growatt: Read holding registers** takes an inverter device, first address and count. Supply the preceding result's snapshot reference as **Previous snapshot** to compare the same range.

Select the inverter's MQTT device, not its datalogger. System automations without
a user context may also call these actions. The result is returned to the caller;
automations can use a response variable. Keep responses and automation traces private.

```yaml
action: ha_growatt.read_registers
data:
  device_id: YOUR_INVERTER_DEVICE_ID
  start: 9
  count: 6
response_variable: register_report
```

The companion exchanges non-retained requests and replies with the app over
MQTT. Clients allowed to publish to the diagnostic request topic can request
these same bounded reads. Use appropriate broker access controls. Requests
expire, duplicates are ignored and replies must match the request and inverter.

## If a read fails

**Busy:** wait a few seconds and retry. At most four diagnostic requests are
queued, with a five-second minimum between diagnostic starts for an inverter.
Normal transport command pacing also applies.

**Connection changed:** the logger reconnected or the inverter's details changed.
Start a new reading rather than comparing different sessions.

**Unavailable field:** the register may not be implemented or the inverter did
not answer. Keep the working reading profile and use the physical label. Solar-only
inverters may not answer overnight.

**No diagnostic reply:** check that the app is running, Home Assistant features
are enabled, both components include these tools and both use the same broker.
Write controls can remain disabled during read-only diagnostics.

The definitions and request handling have protocol and synthetic integration
tests. New model detection has not been physically qualified across these
families. Existing telemetry verification does not extend that claim.

## Scan direct Modbus input and holding registers

Some inverters or gateways offer a separate **direct Modbus TCP** connection.
This is not the Shine upload connection used by the HA Growatt app. If your
hardware exposes direct Modbus TCP, run the scanner on a computer that can reach
that gateway. Give its address explicitly; the scanner does not search your
network. It sends only function 04 (input read) and function 03 (holding read).
It never sends a write, and a successful direct read does not establish that
the same register can be read through the Shine upload connection.

~~~sh
uv run --locked python -m ha_growatt.modbus_scan \
  --host YOUR_MODBUS_GATEWAY --port 502 --unit 1 \
  --range 0:125 --range 3000:125 \
  --output private-register-scan.json
~~~

Each range is a decimal `START:COUNT`, with no more than 512 addresses across
the request. The scanner reads both register types in blocks of at most 32,
waits at least half a second between requests, and stops at 128 requests by
default. If the gateway reports an illegal register address, the scanner
divides that block into smaller blocks to show which addresses answer. A
connection failure or timeout does not trigger extra reads. An unanswered
block is recorded as unavailable rather than as zero. Use `--kind input` or
`--kind holding` to limit the type, and
`--max-requests` to set a limit from 1 to 256. A slow or unsupported gateway
can make broad scans take several minutes; start with one documented range.

The JSON report contains raw values. Some registers encode serial numbers,
so keep it private. For a public hardware report, describe the tested model,
firmware, gateway, software, read ranges and which blocks responded; do not
attach this unredacted file. The separate serial-redacted packet replay covers
Shine traffic but does not turn unsupported packets into decoded measurements.

The owner's MIC 2000TL-X and MIN 2500TL-XH already have verified HA Growatt
Shine telemetry. Their ShineWiFi-X dataloggers have not yet been confirmed as
offering a separate direct Modbus TCP endpoint. The app's existing holding
register inspector remains the read-only check for those live Shine sessions.
