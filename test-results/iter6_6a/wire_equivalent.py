"""Stage 6A.1: rewrite a Windows capture as a network tap would have seen it.

Windows records packets on the far side of the network card's segmentation: an outgoing large send
(IP total length 0, which Argus drops) or several received segments merged into one, either way a
TCP packet bigger than the 1500-byte MTU. This writes a copy in which each such packet is split
into MTU-sized TCP segments (sequence numbers advanced, PSH/FIN only on the last segment, lengths
and checksums filled in, 1 microsecond apart as they leave back to back; Argus skips a gap of
exactly 0), the packets a tap on the wire would record. Windows also stamps packets delivered
together with one time; a tap records them apart, so every packet is written at least 1 microsecond
after the previous one. An 802.1Q tag (VLAN 0, priority only, on two servers' frames) is
link-layer, like the Ethernet header, and UNSW-NB15 counts neither, so it is removed. Packet
contents are otherwise copied unchanged.

    python test-results/iter6_6a/wire_equivalent.py --pcap <in.pcap> --out <out.pcap>
"""
import argparse
from decimal import Decimal
from pathlib import Path

from scapy.layers.inet import IP, TCP
from scapy.layers.l2 import Dot1Q, Ether
from scapy.packet import Raw
from scapy.utils import PcapReader, PcapWriter

MTU = 1500
# The capture's resolution, in exact decimal seconds: float arithmetic rounded some segments onto
# the same microsecond, which Argus then skips as a zero gap.
SEGMENT_SPACING = Decimal("0.000001")


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
        rebuilt.time = Decimal(packet.time) + index * SEGMENT_SPACING
        out.append(rebuilt)
    return out


def without_vlan_tag(packet):
    """The frame without its 802.1Q tag; ``None`` when it has none."""
    if not packet.haslayer(Dot1Q):
        return None
    tag = packet[Dot1Q]
    untagged = Ether(bytes(Ether(dst=packet[Ether].dst, src=packet[Ether].src, type=tag.type)
                           / tag.payload))
    untagged.time = packet.time
    return untagged

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    split = written = spread = untagged = 0
    last = None
    with PcapReader(str(args.pcap)) as reader, PcapWriter(str(args.out), sync=True) as writer:
        for packet in reader:
            plain = without_vlan_tag(packet)
            if plain is not None:
                packet = plain
                untagged += 1
            ip = packet.getlayer(IP)
            if ip is not None and packet.haslayer(TCP) and len(bytes(ip)) > MTU:
                parts = segments(packet)
                split += 1
            else:
                parts = [packet]
            for part in parts:
                stamp = Decimal(part.time)
                if last is not None and stamp <= last:
                    stamp = last + SEGMENT_SPACING
                    spread += 1
                part.time = stamp
                last = stamp
                writer.write(part)
                written += 1
    print(f"{split} large sends split; {spread} shared timestamps spread; "
          f"{untagged} VLAN tags removed; "
          f"{written} packets -> {args.out}")


if __name__ == "__main__":
    main()
