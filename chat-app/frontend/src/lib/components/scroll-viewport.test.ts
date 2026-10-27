// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { scrollViewport } from './scroll-viewport';

describe('chat viewport scrolling', () => {
  let resize: () => void;
  const disconnect = vi.fn();
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('requestAnimationFrame', (callback: () => void) => setTimeout(callback, 16));
    vi.stubGlobal('cancelAnimationFrame', clearTimeout);
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { resize = callback; }
      observe() {}
      disconnect = disconnect;
    });
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

  function viewport() {
    const node = document.createElement('div');
    node.append(document.createElement('div'));
    Object.defineProperties(node, {
      scrollHeight: { value: 1200, configurable: true },
      clientHeight: { value: 400 }
    });
    return node;
  }

  it('shows the scrollbar while scrolling and hides it after inactivity', () => {
    const node = viewport();
    const action = scrollViewport(node);
    expect(node.classList.contains('is-scrolling')).toBe(false);
    node.dispatchEvent(new Event('scroll'));
    expect(node.classList.contains('is-scrolling')).toBe(true);
    vi.advanceTimersByTime(800);
    node.dispatchEvent(new Event('scroll'));
    vi.advanceTimersByTime(800);
    expect(node.classList.contains('is-scrolling')).toBe(true);
    vi.advanceTimersByTime(200);
    expect(node.classList.contains('is-scrolling')).toBe(false);
    action.destroy();
  });

  it('follows content growth, respects scrolling up, and resets on a new user message', () => {
    const node = viewport();
    const action = scrollViewport(node, { follow: true, resetKey: 'first' });
    vi.advanceTimersByTime(16);
    expect(node.scrollTop).toBe(1200);
    Object.defineProperty(node, 'scrollHeight', { value: 1500, configurable: true });
    resize();
    vi.advanceTimersByTime(16);
    expect(node.scrollTop).toBe(1500);
    node.scrollTop = 100;
    node.dispatchEvent(new Event('scroll'));
    resize();
    vi.advanceTimersByTime(16);
    expect(node.scrollTop).toBe(100);
    action.update({ follow: true, resetKey: 'first' });
    vi.advanceTimersByTime(16);
    expect(node.scrollTop).toBe(100);
    action.update({ follow: true, resetKey: 'second' });
    vi.advanceTimersByTime(16);
    expect(node.scrollTop).toBe(1500);
    action.destroy();
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it('lets a swipe interrupt a queued scroll and stays unpinned until the bottom', () => {
    const node = viewport();
    const action = scrollViewport(node, { follow: true });
    vi.advanceTimersByTime(16);
    node.scrollTop = 800;
    resize();
    node.dispatchEvent(new Event('touchmove'));
    node.scrollTop = 790;
    node.dispatchEvent(new Event('scroll'));
    resize();
    vi.advanceTimersByTime(16);
    expect(node.scrollTop).toBe(790);
    node.scrollTop = 800;
    node.dispatchEvent(new Event('scroll'));
    Object.defineProperty(node, 'scrollHeight', { value: 1500, configurable: true });
    resize();
    vi.advanceTimersByTime(16);
    expect(node.scrollTop).toBe(1500);
    action.destroy();
  });

  it('cleans up pending scrolls, timers and listeners on navigation', () => {
    const node = viewport();
    const action = scrollViewport(node, { follow: true });
    node.dispatchEvent(new Event('scroll'));
    action.destroy();
    vi.runAllTimers();
    expect(node.scrollTop).toBe(0);
    node.dispatchEvent(new Event('scroll'));
    expect(node.classList.contains('is-scrolling')).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});