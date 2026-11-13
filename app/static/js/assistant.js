// Owner AI assistant chat. Talks to /api/assistant/* endpoints.
(function () {
  const chat = document.getElementById('asChat');
  const form = document.getElementById('asForm');
  const input = document.getElementById('asInput');
  let busy = false;

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function bubble(m) {
    const mine = m.role === 'owner';
    const actions = (m.actions || []).map((a) => `<span class="action-chip">✓ ${esc(a)}</span>`).join('');
    return `<div class="bubble-row ${mine ? 'right' : 'left'}">
      <div class="bubble ${mine ? 'bubble-human_agent' : 'bubble-assistant'}">
        <div class="bubble-who">${mine ? 'You' : 'AI Assistant'}</div>
        <div class="bubble-text">${esc(m.content)}</div>
        ${actions ? `<div class="action-list">${actions}</div>` : ''}
      </div></div>`;
  }

  function render(messages) {
    chat.innerHTML = messages.length
      ? messages.map(bubble).join('')
      : '<div class="empty">Ask me to manage your business. Try one of the examples on the right.</div>';
    chat.scrollTop = chat.scrollHeight;
  }

  async function load() {
    const data = await api('/api/assistant/messages', { method: 'GET' });
    render(data.messages);
  }

  async function send(text) {
    if (busy || !text.trim()) return;
    busy = true;
    chat.querySelector('.empty')?.remove();
    chat.insertAdjacentHTML('beforeend', bubble({ role: 'owner', content: text }));
    chat.insertAdjacentHTML('beforeend', '<div class="bubble-row left" id="asTyping"><div class="bubble bubble-assistant typing"><span></span><span></span><span></span></div></div>');
    chat.scrollTop = chat.scrollHeight;
    try {
      const data = await api('/api/assistant/message', { method: 'POST', body: JSON.stringify({ message: text }) });
      render(data.messages);
    } catch (err) {
      document.getElementById('asTyping')?.remove();
      chat.insertAdjacentHTML('beforeend', `<div class="bubble-row center"><div class="bubble bubble-system">Error: ${esc(err.message)}</div></div>`);
    } finally {
      busy = false;
      input.focus();
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    input.value = '';
    send(text);
  });
  document.querySelectorAll('.chip').forEach((chip) => chip.addEventListener('click', () => send(chip.dataset.q)));
  document.getElementById('asClear').addEventListener('click', async () => {
    if (!confirm('Clear the assistant chat history?')) return;
    await api('/api/assistant/clear', { method: 'POST', body: '{}' });
    render([]);
  });
  load();
})();
