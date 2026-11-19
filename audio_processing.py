"""Conservative export-time cleanup; never gate speech or boost quiet passages."""
from __future__ import annotations


def cleanup_filter(source: str = "system") -> str:
    # Preserve music/bass on loopback; microphone tracks need more rumble removal.
    if source == "microphone":
        highpass, reduction, floor = 65, 10, -48
    else:
        highpass, reduction, floor = 30, 6, -55
    # The bundled afftdn adds 25 ms of signal latency without shifting timestamps.
    # Feed a tail through it, then trim that latency so clicks/speech stay in sync.
    return (f"highpass=f={highpass},apad=pad_dur=0.025,"
            f"afftdn=nr={reduction}:nf={floor}:tn=1:gs=8,"
            "atrim=start=0.025,asetpts=PTS-STARTPTS,"
            "alimiter=limit=0.98:level=0:latency=1")
