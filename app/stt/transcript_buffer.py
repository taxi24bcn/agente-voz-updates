"""Buffer acumulativo thread-safe de segmentos transcritos."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Segment:
    speaker: str  # "cliente" | "operador"
    text: str
    timestamp: float = field(default_factory=time.time)


class TranscriptBuffer:
    def __init__(self) -> None:
        self._segments: list[Segment] = []
        self._lock = threading.Lock()
        self._start_time = time.time()

    def append(self, speaker: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._segments.append(Segment(speaker, text))

    def clear(self) -> None:
        with self._lock:
            self._segments.clear()
            self._start_time = time.time()

    def snapshot(self) -> list[Segment]:
        with self._lock:
            return list(self._segments)

    def full_text(self) -> str:
        """All segments as conversation lines: '[C] texto\\n[O] texto\\n...'."""
        with self._lock:
            lines = []
            for seg in self._segments:
                tag = "C" if seg.speaker == "cliente" else "O"
                lines.append(f"[{tag}] {seg.text}")
            return "\n".join(lines)

    def word_count(self) -> int:
        with self._lock:
            return sum(len(seg.text.split()) for seg in self._segments)
