// Shared helpers for the dashboard.

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : '';
}

// fetch() wrapper for the JSON API: sends the CSRF header and throws readable errors.
async function api(url, options = {}) {
  const res = await fetch(url, {
    credentials: 'same-origin',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken(),
      ...(options.headers || {}),
    },
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    if (data && typeof data.detail === 'string') msg = data.detail;
    else if (data && Array.isArray(data.detail) && data.detail[0]) msg = data.detail[0].msg;
    throw new Error(msg);
  }
  return data;
}

function copyText(elementId, button) {
  const text = document.getElementById(elementId).innerText;
  navigator.clipboard.writeText(text).then(() => {
    const old = button.textContent;
    button.textContent = 'Copied!';
    setTimeout(() => (button.textContent = old), 1500);
  });
}

// Auto-hide success alerts.
document.querySelectorAll('.alert-success').forEach((el) => {
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 400); }, 4000);
});
