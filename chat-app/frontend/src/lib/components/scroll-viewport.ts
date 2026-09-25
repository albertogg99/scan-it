type ScrollOptions = { follow?: boolean; resetKey?: string };

export function scrollViewport(node: HTMLElement, options: ScrollOptions = {}) {
  let pinned = true;
  let frame: number | undefined;
  let idleTimer: ReturnType<typeof setTimeout> | undefined;
  node.classList.add('quiet-scroll');

  function scrollToBottom() {
    if (frame !== undefined) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = undefined;
      if (options.follow && pinned) node.scrollTop = node.scrollHeight;
    });
  }

  function onScroll() {
    pinned = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
    node.classList.add('is-scrolling');
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => node.classList.remove('is-scrolling'), 1000);
  }

  node.addEventListener('scroll', onScroll, { passive: true });
  const observer = options.follow ? new ResizeObserver(() => {
    if (pinned) scrollToBottom();
  }) : null;
  observer?.observe(node);
  if (node.firstElementChild) observer?.observe(node.firstElementChild);
  if (options.follow) scrollToBottom();

  return {
    update(next: ScrollOptions) {
      if (next.resetKey !== options.resetKey) {
        pinned = true;
        scrollToBottom();
      }
      options = next;
    },
    destroy() {
      observer?.disconnect();
      node.removeEventListener('scroll', onScroll);
      clearTimeout(idleTimer);
      if (frame !== undefined) cancelAnimationFrame(frame);
      node.classList.remove('quiet-scroll', 'is-scrolling');
    }
  };
}