document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const target = document.getElementById(button.dataset.copy);
    const status = button.closest('section').querySelector('.copy-status');
    try {
      await navigator.clipboard.writeText(target.textContent);
      status.textContent = 'Cover-letter text copied. Review before using it.';
    } catch (_) {
      status.textContent = 'Clipboard unavailable. Select the cover-letter text to copy it.';
    }
  });
});
document.querySelectorAll('[data-busy-form]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!form.checkValidity()) return;
    // Preserve the clicked action value; disabling its button would omit it.
    if (form.dataset.pending === 'true') { event.preventDefault(); return; }
    form.dataset.pending = 'true';
    form.setAttribute('aria-busy', 'true');
  });
});

document.querySelectorAll('[data-discovery-watch]').forEach(container => {
  const initialState = container.dataset.initialState;
  let active = true;
  const poll = async () => {
    if (!active) return;
    if (document.hidden) { window.setTimeout(poll, 5000); return; }
    try {
      const response = await fetch(container.dataset.discoveryWatch, {
        credentials: 'same-origin', headers: {Accept: 'application/json'}, cache: 'no-store'
      });
      if (!response.ok || response.redirected) throw new Error('Status unavailable');
      const data = await response.json();
      if (data.status !== initialState) { active = false; window.location.reload(); return; }
      container.querySelectorAll('[data-run-count]').forEach(element => {
        element.textContent = data.summary[element.dataset.runCount] ?? 0;
      });
      const body = container.querySelector('[data-source-counts]');
      if (body && data.sources.length) {
        const fragment = document.createDocumentFragment();
        data.sources.forEach(source => {
          const row = document.createElement('tr');
          [source.name, source.state, ...['fetched','roles','uae','recent','eligible','review','rejected']
            .map(key => source.counts[key] ?? 0)].forEach(value => {
              const cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell);
            });
          fragment.appendChild(row);
        });
        body.replaceChildren(fragment);
      }
      window.setTimeout(poll, 5000);
    } catch (_) {
      active = false;
      const notice = container.querySelector('[data-poll-status]');
      if (notice) notice.textContent = 'Progress is unavailable. Refresh to check the recorded state.';
    }
  };
  window.setTimeout(poll, 5000);
});
