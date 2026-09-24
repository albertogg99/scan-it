// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { groupParts, renderMarkdown } from './message-content';

describe('assistant Markdown', () => {
  it('renders grouped streamed text, lists, code, links and tables', () => {
    const parts = groupParts([{ type: 'text', text: '**Hel' }, { type: 'text', text: 'lo**\n\n- One\n- Two\n\n`code` [link](https://example.com)\n\n| Name |\n| --- |\n| Clock |' }]);
    const html = renderMarkdown(parts[0].type === 'text' ? parts[0].text : '');
    expect(html).toContain('<strong>Hello</strong>');
    expect(html).toContain('<li>One</li>');
    expect(html).toContain('<code>code</code>');
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('<table>');
  });
  it('removes executable HTML, tracking images and unsafe links', () => {
    const html = renderMarkdown('<script>alert(1)</script><img src="https://example.com/track" onerror="alert(1)"><a href="javascript:alert(1)" onclick="alert(1)">bad</a><form><input></form><p style="position:fixed" id="login">Safe</p>');
    expect(html).not.toMatch(/<script|<img|onerror|onclick|javascript:|<form|<input|style=|id=/);
    expect(html).toContain('Safe');
  });
});