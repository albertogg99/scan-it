export type WireEvent = { id: number; event: string; data: Record<string, unknown> };
export class SseParser {
  private decoder = new TextDecoder();
  private pending = '';
  private lines: string[] = [];
  push(chunk: Uint8Array): WireEvent[] {
    this.pending += this.decoder.decode(chunk, { stream: true });
    const events: WireEvent[] = [];
    let match: RegExpExecArray | null;
    const newline = /\r\n|\r|\n/g;
    while ((match = newline.exec(this.pending))) {
      if (match[0] === '\r' && newline.lastIndex === this.pending.length) break;
      const line = this.pending.slice(0, match.index);
      this.pending = this.pending.slice(newline.lastIndex);
      newline.lastIndex = 0;
      if (line === '') {
        const id = this.lines.find(value => value.startsWith('id:'))?.slice(3).trim();
        const event = this.lines.find(value => value.startsWith('event:'))?.slice(6).trim();
        const data = this.lines.filter(value => value.startsWith('data:')).map(value => value.slice(5).trimStart()).join('\n');
        if (id && event && data) {
          try {
            const parsed = JSON.parse(data) as Record<string, unknown>;
            if (Number.isSafeInteger(Number(id)) && Number(id) >= 0 && parsed.seq === Number(id) && parsed.type === event) events.push({ id: Number(id), event, data: parsed });
          } catch { this.lines = []; }
        }
        this.lines = [];
      } else if (!line.startsWith(':')) this.lines.push(line);
    }
    return events;
  }
}