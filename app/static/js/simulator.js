// WhatsApp Simulator (demo mode). Talks to /api/simulator/* endpoints.
(function () {
  const chat = document.getElementById('simChat');
  const form = document.getElementById('simForm');
  const input = document.getElementById('simMessage');
  const phoneInput = document.getElementById('simPhone');
  const nameInput = document.getElementById('simName');
  const resetBtn = document.getElementById('simReset');
  const statusLine = document.getElementById('agentStatus');
  const agentLabel = statusLine.textContent;
  let lastIds = '';
  let busy = false;

  const phone = () => phoneInput.value.replace(/\D/g, '');

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function time(iso) {
    const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function render(messages, status) {
    const ids = messages.map((m) => m.id).join(',') + status;
    if (ids === lastIds) return; // nothing new
    lastIds = ids;
    if (!messages.length) {
      chat.innerHTML = '<div class="sim-empty">Say hi to start. Try: <i>"What do you sell?"</i></div>';
    } else {
      chat.innerHTML = messages.map((m) => {
        const side = m.role === 'customer' ? 'out' : m.role === 'system' ? 'sys' : 'in';
        const who = m.role === 'human_agent' ? '<div class="sim-who">Team member</div>' : '';
        return `<div class="sim-msg ${side}">${who}${escapeHtml(m.content).replace(/\n/g, '<br>')}<span class="sim-time">${time(m.created_at)}</span></div>`;
      }).join('');
    }
    statusLine.textContent = status === 'human' ? 'A team member will reply' : agentLabel;
    chat.scrollTop = chat.scrollHeight;
  }

  function typing(on) {
    let el = document.getElementById('simTyping');
    if (on && !el) {
      el = document.createElement('div');
      el.id = 'simTyping';
      el.className = 'sim-msg in typing';
      el.innerHTML = '<span></span><span></span><span></span>';
      chat.appendChild(el);
      chat.scrollTop = chat.scrollHeight;
    } else if (!on && el) {
      el.remove();
    }
  }

  async function loadHistory() {
    if (busy || phone().length < 6) return;
    try {
      const data = await api(`/api/simulator/messages?phone=${encodeURIComponent(phone())}`, { method: 'GET' });
      render(data.messages, data.conversation_status);
    } catch (e) { /* ignore polling errors */ }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    busy = true;
    input.value = '';
    // Show the customer's message straight away.
    chat.querySelector('.sim-empty')?.remove();
    chat.insertAdjacentHTML('beforeend', `<div class="sim-msg out">${escapeHtml(text)}<span class="sim-time">now</span></div>`);
    typing(true);
    try {
      const data = await api('/api/simulator/message', {
        method: 'POST',
        body: JSON.stringify({ phone: phone(), name: nameInput.value.trim() || null, message: text }),
      });
      lastIds = '';
      render(data.messages, data.conversation_status);
    } catch (err) {
      typing(false);
      chat.insertAdjacentHTML('beforeend', `<div class="sim-msg sys">Error: ${escapeHtml(err.message)}</div>`);
    } finally {
      busy = false;
      input.focus();
    }
  });

  resetBtn.addEventListener('click', async () => {
    if (!confirm('Start a new chat for this phone number? (Orders are kept.)')) return;
    await api('/api/simulator/reset', { method: 'POST', body: JSON.stringify({ phone: phone() }) });
    lastIds = '';
    loadHistory();
  });

  phoneInput.addEventListener('change', () => { lastIds = ''; loadHistory(); });
  loadHistory();
  setInterval(loadHistory, 4000); // pick up replies sent by a human from the dashboard
})();
