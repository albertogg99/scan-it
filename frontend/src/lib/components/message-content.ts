import type { Part } from '$lib/api/types';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(marked.parse(text, { async: false, gfm: true }), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['img', 'form', 'input', 'button', 'video', 'audio', 'iframe'],
    FORBID_ATTR: ['style', 'id', 'name']
  });
}

export function groupParts(parts: Part[]): Part[] {
  const grouped: Part[] = [];
  for (const part of parts) {
    const previous = grouped.at(-1);
    if (part.type !== 'tool_call' && previous?.type === part.type) {
      previous.text += part.text;
    } else {
      grouped.push({ ...part });
    }
  }
  return grouped;
}