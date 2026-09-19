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
