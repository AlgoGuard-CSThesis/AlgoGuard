"""Stage 6A.1: rewrite a Windows capture as a network tap would have seen it.

Windows records packets on the far side of the network card's segmentation: an outgoing large send
(IP total length 0, which Argus drops) or several received segments merged into one, either way a
TCP packet bigger than the 1500-byte MTU. This writes a copy in which each such packet is split
into MTU-sized TCP segments (sequence numbers advanced, PSH/FIN only on the last segment, lengths
and checksums filled in, 1 microsecond apart as they leave back to back; Argus skips a gap of
exactly 0), the packets a tap on the wire would record. Every other packet is copied unchanged.

    python test-results/iter6_6a/wire_equivalent.py --pcap <in.pcap> --out <out.pcap>
"""
import argparse
from pathlib import Path

from scapy.layers.inet import IP, TCP
from scapy.layers.l2 import Ether  # noqa: F401  (decoder for the capture's link layer)
from scapy.packet import Raw
from scapy.utils import PcapReader, PcapWriter

MTU = 1500
SEGMENT_SPACING = 0.000001  # seconds; the capture's resolution


def segments(packet):
    ip, tcp = packet[IP], packet[TCP]
    headers = ip.ihl * 4 + tcp.dataofs * 4
    payload = bytes(tcp.payload)
    mss = MTU - headers
    out = []
    for index, offset in enumerate(range(0, len(payload), mss)):
        last = offset + mss >= len(payload)
        segment = packet.copy()
        segment[IP].len = None
        segment[IP].chksum = None
        segment[IP].id = (ip.id + index) & 0xFFFF
        segment[TCP].seq = (tcp.seq + offset) & 0xFFFFFFFF
        segment[TCP].chksum = None
        if not last:
            segment[TCP].flags = int(tcp.flags) & ~0x09  # clear FIN and PSH
        segment[TCP].remove_payload()
        segment[TCP].add_payload(Raw(payload[offset:offset + mss]))
        rebuilt = segment.__class__(bytes(segment))
        rebuilt.time = packet.time + index * SEGMENT_SPACING
        out.append(rebuilt)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    split = written = 0
    with PcapReader(str(args.pcap)) as reader, PcapWriter(str(args.out), sync=True) as writer:
        for packet in reader:
            ip = packet.getlayer(IP)
            if ip is not None and packet.haslayer(TCP) and len(bytes(ip)) > MTU:
                parts = segments(packet)
                split += 1
                written += len(parts)
                for part in parts:
                    writer.write(part)
            else:
                written += 1
                writer.write(packet)
    print(f"{split} large sends split; {written} packets -> {args.out}")


if __name__ == "__main__":
    main()
