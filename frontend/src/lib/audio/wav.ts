export const WAV_RATE = 16000;

export function encodeWav(samples: Float32Array, rate = WAV_RATE): Blob {
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const ascii = (offset: number, value: string) => { for (let index = 0; index < value.length; index++) view.setUint8(offset + index, value.charCodeAt(index)); };
  ascii(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); ascii(8, 'WAVE');
  ascii(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  ascii(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => { const value = Math.max(-1, Math.min(1, sample)); view.setInt16(44 + index * 2, value < 0 ? value * 0x8000 : value * 0x7fff, true); });
  return new Blob([view.buffer], { type: 'audio/wav' });
}

// Browsers record webm/opus or mp4/aac; the provider accepts WAV, so resample to 16 kHz mono PCM.
export async function toWav(recording: Blob): Promise<File> {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await recording.arrayBuffer());
    const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * WAV_RATE)), WAV_RATE);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const rendered = await offline.startRendering();
    return new File([encodeWav(rendered.getChannelData(0))], 'voice.wav', { type: 'audio/wav' });
  } finally {
    void context.close();
  }
}
