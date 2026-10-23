(() => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  fetch('/analytics/confirm', {
    method: 'POST',
    credentials: 'same-origin',
    keepalive: true,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      screen: `${window.screen.width}x${window.screen.height}`,
      language: navigator.language || '',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    }),
  }).catch(() => {});

  const progress = document.querySelector('[data-reading-progress]');
  const article = document.querySelector('[data-article-content]');
  if (progress && article) {
    let start = 0;
    let distance = 0;
    let frame = 0;
    const measureProgress = () => {
      start = article.offsetTop;
      distance = article.offsetHeight - window.innerHeight;
    };
    const updateProgress = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        const value = distance > 0
          ? Math.min(1, Math.max(0, (window.scrollY - start + 120) / distance))
          : 0;
        progress.style.transform = `scaleX(${value})`;
        frame = 0;
      });
    };
    measureProgress();
    updateProgress();
    window.addEventListener('scroll', updateProgress, { passive: true });
    window.addEventListener('resize', () => {
      measureProgress();
      updateProgress();
    }, { passive: true });
  }

  const copyButton = document.querySelector('[data-copy-link]');
  if (copyButton) {
    copyButton.setAttribute('aria-live', 'polite');
    copyButton.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(window.location.href);
        copyButton.textContent = '链接已复制 ✓';
        copyButton.dataset.copyState = 'done';
      } catch {
        copyButton.textContent = '请复制浏览器地址';
      }
      window.setTimeout(() => {
        copyButton.textContent = '复制文章链接';
        delete copyButton.dataset.copyState;
      }, 1800);
    });
  }

  const revealGroups = [
    '.section-heading',
    '.featured-card',
    '.post-card',
    '.project-preview-card',
    '.project-full-card',
    '.start-card',
    '.topic-grid > a',
    '.activity-list > a',
    '.garden-lanes > a',
    '.garden-more > a',
    '.uses-grid > article',
    '.system-map li',
    '.colophon-principles > article',
    '.garden-category-list > article',
    '.note-grid article',
    '.notes-wall article',
    '.about-dashboard > article',
    '.principles article',
    '.related-grid > a',
  ];
  const revealItems = [...document.querySelectorAll(revealGroups.join(','))];
  if (revealItems.length && !reducedMotion && 'IntersectionObserver' in window) {
    document.documentElement.classList.add('motion-ready');
    revealItems.forEach((item, index) => {
      item.dataset.reveal = '';
      item.style.setProperty('--reveal-delay', `${(index % 4) * 30}ms`);
    });
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    revealItems.forEach((item) => observer.observe(item));
  }
})();
