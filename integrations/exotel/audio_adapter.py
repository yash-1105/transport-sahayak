"""Audio format adapter between Exotel and the existing Hindi pipeline.

The pipeline is fixed (do NOT change it): Sarvam Saaras expects PCM16 mono @
16 kHz IN; Sarvam Bulbul emits PCM16 mono @ 24 kHz OUT. Exotel streams PCM16
mono @ EXOTEL_SAMPLE_RATE (default 16 kHz; telephony is often 8 kHz — set the env
to whatever the applet actually sends). This module resamples between them using
stdlib `audioop.ratecv`, carrying converter state across chunks per direction so
a continuous stream stays smooth.

  Exotel in  →  resample(EXOTEL_SAMPLE_RATE → 16000)  →  Saaras
  Bulbul out (24000)  →  resample(24000 → EXOTEL_SAMPLE_RATE)  →  Exotel

Only PCM16 mono is handled (the one format both sides use). If a deployment's
Exotel applet is configured for a non-PCM codec (e.g. µ-law), add a decode step
here — never in the pipeline.

NOTE: `audioop` is stdlib through Python 3.12 and is removed in 3.13. The service
runs on 3.11/3.12; if upgrading to 3.13+, vendor the `audioop-lts` shim or a tiny
linear resampler — this is the only place that would need it.
"""
from __future__ import annotations

import audioop

_SAARAS_RATE_IN = 16000   # severity_engine.sarvam_speech._SAMPLE_RATE_IN
_BULBUL_RATE_OUT = 24000  # severity_engine.sarvam_speech.TTS_SAMPLE_RATE
_WIDTH = 2                 # PCM16 = 2 bytes/sample
_CHANNELS = 1              # mono


class AudioAdapter:
    """Per-call resampler. One instance per Exotel session (state is per stream)."""

    def __init__(self, exotel_rate: int = 8000):
        self.exotel_rate = int(exotel_rate) or 8000
        # ratecv converter state, carried across chunks for each direction.
        self._in_state = None    # Exotel rate -> 16k
        self._out_state = None   # 24k -> Exotel rate

    def exotel_to_pipeline(self, pcm: bytes) -> bytes:
        """Exotel inbound PCM16 (exotel_rate) -> PCM16 @ 16 kHz for Saaras."""
        if not pcm:
            return b""
        if self.exotel_rate == _SAARAS_RATE_IN:
            return pcm
        converted, self._in_state = audioop.ratecv(
            pcm, _WIDTH, _CHANNELS, self.exotel_rate, _SAARAS_RATE_IN, self._in_state
        )
        return converted

    def pipeline_to_exotel(self, pcm24k: bytes) -> bytes:
        """Bulbul PCM16 @ 24 kHz -> PCM16 @ exotel_rate for Exotel outbound."""
        if not pcm24k:
            return b""
        if self.exotel_rate == _BULBUL_RATE_OUT:
            return pcm24k
        converted, self._out_state = audioop.ratecv(
            pcm24k, _WIDTH, _CHANNELS, _BULBUL_RATE_OUT, self.exotel_rate, self._out_state
        )
        return converted
