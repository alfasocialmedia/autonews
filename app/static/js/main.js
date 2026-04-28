/* AutoNews — JS helpers */

// ── Toast notifications ──────────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const colors = {
    success: '#198754', danger: '#dc3545', info: '#0dcaf0',
    warning: '#ffc107', secondary: '#6c757d', primary: '#0d6efd',
  };

  const id = 'toast-' + Date.now();
  const el = document.createElement('div');
  el.id = id;
  el.className = 'toast align-items-center text-white border-0 show';
  el.style.cssText = `background:${colors[type] || colors.info};border-radius:10px;min-width:280px`;
  el.setAttribute('role', 'alert');
  el.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${message}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
    </div>`;
  container.appendChild(el);

  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 300);
  }, duration);
}

// ── Confirm + POST delete ────────────────────────────────────────────────────
let _pendingDeleteUrl = null;

function confirmDelete(url, itemName = 'este elemento') {
  _pendingDeleteUrl = url;
  const body = document.getElementById('confirmModalBody');
  if (body) body.textContent = `¿Eliminar ${itemName}? Esta acción no se puede deshacer.`;
  const modal = new bootstrap.Modal(document.getElementById('confirmModal'));
  modal.show();
}

document.addEventListener('DOMContentLoaded', function () {
  // Confirm modal OK button
  document.getElementById('confirmModalOk')?.addEventListener('click', async function () {
    if (!_pendingDeleteUrl) return;
    const modal = bootstrap.Modal.getInstance(document.getElementById('confirmModal'));
    modal?.hide();

    const resp = await fetch(_pendingDeleteUrl, { method: 'POST' });

    // Handle redirect (form-based delete) or JSON
    if (resp.redirected || resp.headers.get('content-type')?.includes('text/html')) {
      location.reload();
      return;
    }

    try {
      const data = await resp.json();
      if (data.success) {
        showToast('Eliminado correctamente', 'success');
        setTimeout(() => location.reload(), 1200);
      } else {
        showToast(data.message || 'Error al eliminar', 'danger');
      }
    } catch {
      location.reload();
    }
    _pendingDeleteUrl = null;
  });

  // Mobile sidebar toggle
  document.getElementById('sidebarToggle')?.addEventListener('click', function () {
    document.querySelector('.sidebar')?.classList.toggle('open');
  });

  // Auto-dismiss alerts after 5s
  document.querySelectorAll('.alert-dismissible').forEach(alert => {
    setTimeout(() => {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      bsAlert?.close();
    }, 5000);
  });
});
