# Protocol evidence

The framing implementation uses the eight-byte Growatt TCP header. Protocol 2
uses an unmasked payload. Protocols 5 and 6 apply the repeating ASCII Growatt XOR
mask and carry a big-endian CRC-16/MODBUS checksum over the transmitted header
and masked payload. Stream handling treats TCP as a byte stream, with declared
lengths determining record boundaries.

Sources for wire facts:

- [Growatt WiFi Module Protocol](https://www.vromans.org/software/sw_growatt_wifi_protocol.html)
  describes the older protocol, exchanges and frame lengths.
- [Growatt Inverter Communications Protocol](https://www.ietfng.org/nwf/misc/growatt-protocol.html)
  describes the masked protocol, CRC and register-report envelopes.
- [Growatt Modbus RTU Protocol V3.05](https://soulraven.github.io/growatt-monitor/assets/Growatt_PV_Inverter_Modbus_RS485_RTU_Protocol_V3.05.pdf)
  and [V1.24](https://www.photovoltaikforum.com/core/file-download/463106/)
  are manufacturer documents describing register meaning and scaling.

The structured protocol-6 parser reads two padded 30-byte identities, a six-byte
device timestamp and the declared register ranges. It rejects overlapping or
truncated ranges and unrecognised trailing bytes. Device timestamps without a
valid calendar value are reported as absent, rather than fabricated.

The scalar compatibility decoder is separate from the structured register
parser. Its byte dependencies were established by changing synthetic inputs and
observing output changes. This preserves existing numerical interpretation even
where packet layout variants differ from the general register documentation.

The XOR mask is obfuscation, not transport security. MQTT TLS is available through
configuration; datalogger traffic should remain on the local network.

Additional diagnostic evidence:

- [Growatt Modbus V1.24, TL-X/TL-XH input table](https://www.amosplanet.org/wp-content/uploads/2023/06/Growatt-Inverter-Modbus-RTU-Protocol_II-V1_24-English.pdf)
  defines the low-byte web state at input 3000, main fault at 3105 and main
  warning at 3106. Battery-converter faults have a separate meaning.
- [Growatt MIC 600–3300TL-X manual, June 2023, section 11.2](https://de.growatt.com/upload/file/MIC_600-3000TL-X_User_manual_EN_202306.pdf)
  supplies the limited MIC main-fault descriptions. Other model families are
  not assigned these descriptions without matching evidence.

Clock differences are observations of packet wall time, not a new protocol write.
Cloud-write attribution correlates function 6/16 acknowledgements with a subsequent
function 5 read in the same TCP session, after any transaction translation. The
implementation is new code; external implementations were not copied.

The [read-only identification tools](register-tools.md) use the documented
firmware and optional model fields, plus VPP DTC and version codes. A DTC is a
family hint only. Reads use TCP function 5 with inclusive holding addresses;
the tool accepts at most 32 words and preserves normal command pacing.
