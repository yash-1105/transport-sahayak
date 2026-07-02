// pcm16-worklet.js — AudioWorkletProcessor that converts the microphone's raw
// Float32 samples (at whatever sample rate the browser/device provides) into
// mono PCM16LE at a fixed target rate (16kHz, matching what the backend's
// Speech-to-Text V2 streaming config expects), and posts each chunk back to
// the main thread as a transferable ArrayBuffer.
//
// Downsampling uses simple nearest-neighbor decimation — not audiophile
// quality, but more than sufficient for speech recognition input, and it
// keeps this dependency-free (no resampler library needed).
//
// Loaded via `audioContext.audioWorklet.addModule("/audio/pcm16-worklet.js")`
// from src/hooks/useVoiceInput.ts.

class PCM16Processor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetSampleRate = opts.targetSampleRate || 16000;
    // `sampleRate` is a global provided by the AudioWorkletGlobalScope —
    // the actual input sample rate of the AudioContext this processor runs in.
    this.ratio = sampleRate / this.targetSampleRate;
    this.buffer = [];
    // ~100ms worth of input samples per emitted chunk — small enough for low
    // latency, large enough to avoid excessive message-passing overhead.
    this.inputSamplesPerChunk = Math.floor(this.ratio * this.targetSampleRate * 0.1);
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channelData = input[0]; // mono — first channel only
    if (!channelData || channelData.length === 0) return true;

    for (let i = 0; i < channelData.length; i++) {
      this.buffer.push(channelData[i]);
    }

    while (this.buffer.length >= this.inputSamplesPerChunk) {
      const inputChunk = this.buffer.splice(0, this.inputSamplesPerChunk);
      const outLength = Math.max(1, Math.floor(inputChunk.length / this.ratio));
      const pcm16 = new Int16Array(outLength);
      for (let i = 0; i < outLength; i++) {
        const srcIndex = Math.min(inputChunk.length - 1, Math.floor(i * this.ratio));
        let sample = inputChunk[srcIndex];
        sample = Math.max(-1, Math.min(1, sample));
        pcm16[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      }
      this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm16-processor", PCM16Processor);
