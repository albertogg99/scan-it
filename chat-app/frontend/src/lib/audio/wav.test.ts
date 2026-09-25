import { describe, expect, it } from 'vitest';
import { encodeWav } from './wav';

describe('encodeWav', () => {
  it('writes a 16-bit mono PCM header and clamped samples', async () => {
    const blob = encodeWav(new Float32Array([0, 1, -1, 2]), 16000);
    const view = new DataView(await blob.arrayBuffer());
    const ascii = (offset: number) => String.fromCharCode(...new Uint8Array(view.buffer, offset, 4));
    expect(blob.type).toBe('audio/wav');
    expect([ascii(0), ascii(8), ascii(12), ascii(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data']);
    expect([view.getUint16(22, true), view.getUint32(24, true), view.getUint16(34, true), view.getUint32(40, true)]).toEqual([1, 16000, 16, 8]);
    expect([0, 1, 2, 3].map(index => view.getInt16(44 + index * 2, true))).toEqual([0, 32767, -32768, 32767]);
  });
});
