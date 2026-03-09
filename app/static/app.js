/* === Eidos Mail — Client JS === */

// Service worker registration (with cache busting)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js').catch(() => {});
}

// ---------------------------------------------------------------------------
// Toast notification system
// ---------------------------------------------------------------------------

function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('show'));
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

// ---------------------------------------------------------------------------
// Avatar color from string
// ---------------------------------------------------------------------------

function avatarColor(s) {
  const colors = ['#ef4444','#f59e0b','#22c55e','#3b82f6','#8b5cf6','#ec4899','#14b8a6','#f97316'];
  let h = 0;
  for (let i = 0; i < s.length; i++) h = s.charCodeAt(i) + ((h << 5) - h);
  return colors[Math.abs(h) % colors.length];
}

function initials(from) {
  if (!from) return '?';
  const m = from.match(/^([^<]+)</);
  const name = m ? m[1].trim() : from.split('@')[0];
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length-1][0]).toUpperCase();
  return name.substring(0, 2).toUpperCase();
}

function initAvatars() {
  document.querySelectorAll('.avatar[data-from]').forEach(el => {
    const from = el.dataset.from;
    el.style.backgroundColor = avatarColor(from);
    if (!el.textContent.trim()) el.textContent = initials(from);
  });
}

// ---------------------------------------------------------------------------
// Swipe handling for email items (C1: with error handling)
// ---------------------------------------------------------------------------

function initSwipe() {
  document.querySelectorAll('.email-item[data-swipe]').forEach(el => {
    let startX = 0, currentX = 0, swiping = false;
    const content = el.querySelector('.email-inner');
    if (!content) return;

    el.addEventListener('touchstart', e => {
      startX = e.touches[0].clientX;
      swiping = true;
      content.style.transition = 'none';
    }, { passive: true });

    el.addEventListener('touchmove', e => {
      if (!swiping) return;
      currentX = e.touches[0].clientX - startX;
      currentX = Math.max(-120, Math.min(120, currentX));
      content.style.transform = `translateX(${currentX}px)`;
      const leftBg = el.querySelector('.swipe-bg.left');
      const rightBg = el.querySelector('.swipe-bg.right');
      if (leftBg) leftBg.style.opacity = currentX > 40 ? '1' : '0';
      if (rightBg) rightBg.style.opacity = currentX < -40 ? '1' : '0';
    }, { passive: true });

    el.addEventListener('touchend', () => {
      swiping = false;
      content.style.transition = 'transform 0.2s ease';
      const id = el.dataset.id;
      const isTrash = el.classList.contains('trashed');

      if (currentX > 80) {
        content.style.transform = 'translateX(100%)';
        if (isTrash) {
          // Swipe right in trash = restore
          setTimeout(() => {
            fetch(`/undelete/${id}`, { method: 'POST' })
              .then(r => {
                if (!r.ok) throw new Error('Failed');
                _slideRemove(el);
                showToast('Email restored', 'success');
              })
              .catch(() => {
                content.style.transform = 'translateX(0)';
                showToast('Restore failed', 'error');
              });
          }, 150);
        } else {
          // Swipe right = mark read
          setTimeout(() => {
            fetch('/mark-read', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ids: [parseInt(id)], read: true })
            })
              .then(r => {
                if (!r.ok) throw new Error('Failed');
                el.classList.remove('unread');
                content.style.transform = 'translateX(0)';
              })
              .catch(() => {
                content.style.transform = 'translateX(0)';
                showToast('Mark read failed', 'error');
              });
          }, 150);
        }
      } else if (currentX < -80 && !isTrash) {
        // Swipe left = delete
        content.style.transform = 'translateX(-100%)';
        setTimeout(() => {
          fetch(`/delete/${id}`, { method: 'POST' })
            .then(r => {
              if (!r.ok) throw new Error('Failed');
              _slideRemove(el);
              showToast('Moved to trash', 'success');
            })
            .catch(() => {
              content.style.transform = 'translateX(0)';
              showToast('Delete failed', 'error');
            });
        }, 150);
      } else {
        content.style.transform = 'translateX(0)';
      }
      // Reset backgrounds
      el.querySelectorAll('.swipe-bg').forEach(bg => bg.style.opacity = '0');
      currentX = 0;
    });
  });
}

// Slide and remove an element from the list
function _slideRemove(el) {
  el.style.maxHeight = el.offsetHeight + 'px';
  el.style.overflow = 'hidden';
  el.style.transition = 'max-height 0.3s ease, padding 0.3s ease, opacity 0.3s';
  el.style.opacity = '0';
  el.style.maxHeight = '0';
  el.style.padding = '0';
  setTimeout(() => el.remove(), 300);
}

// ---------------------------------------------------------------------------
// Quick action buttons (C1: with error handling)
// ---------------------------------------------------------------------------

function quickAction(action, id, btn) {
  const item = btn.closest('.email-item');

  if (action === 'read') {
    fetch('/mark-read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [id], read: true })
    })
      .then(r => { if (r.ok) item?.classList.remove('unread'); else throw new Error(); })
      .catch(() => showToast('Mark read failed', 'error'));
  } else if (action === 'delete') {
    fetch(`/delete/${id}`, { method: 'POST' })
      .then(r => {
        if (!r.ok) throw new Error();
        if (item) _slideRemove(item);
        showToast('Moved to trash', 'success');
      })
      .catch(() => showToast('Delete failed', 'error'));
  } else if (action === 'undelete') {
    fetch(`/undelete/${id}`, { method: 'POST' })
      .then(r => {
        if (!r.ok) throw new Error();
        if (item) _slideRemove(item);
        showToast('Email restored', 'success');
      })
      .catch(() => showToast('Restore failed', 'error'));
  }
}

// ---------------------------------------------------------------------------
// Swipe hint (H8: discoverability)
// ---------------------------------------------------------------------------

function initSwipeHint() {
  if (localStorage.getItem('eidos-swipe-hint')) return;
  const firstItem = document.querySelector('.email-item[data-swipe]');
  if (!firstItem) return;

  const hint = document.createElement('div');
  hint.className = 'swipe-hint';
  hint.innerHTML = '<span>&larr; swipe to delete</span><span>swipe to read &rarr;</span>';
  firstItem.parentNode.insertBefore(hint, firstItem);
  localStorage.setItem('eidos-swipe-hint', '1');
  setTimeout(() => {
    hint.style.opacity = '0';
    setTimeout(() => hint.remove(), 500);
  }, 4000);
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts (L1)
// ---------------------------------------------------------------------------

function initKeyboard() {
  document.addEventListener('keydown', e => {
    // Don't trigger in inputs/textareas
    if (e.target.matches('input, textarea, select, [contenteditable]')) return;

    switch(e.key) {
      case 'c':
        e.preventDefault();
        htmx.ajax('GET', '/compose', {target: '#content', swap: 'innerHTML'});
        break;
      case '/':
        e.preventDefault();
        htmx.ajax('GET', '/search', {target: '#content', swap: 'innerHTML'});
        // Focus search input after swap
        document.addEventListener('htmx:afterSwap', function focusSearch() {
          const input = document.querySelector('.search-input');
          if (input) input.focus();
          document.removeEventListener('htmx:afterSwap', focusSearch);
        });
        break;
      case 'i':
        e.preventDefault();
        htmx.ajax('GET', '/inbox', {target: '#content', swap: 'innerHTML'});
        break;
      case 'Escape':
        // Back to inbox from detail view
        if (document.querySelector('.email-detail')) {
          htmx.ajax('GET', '/inbox', {target: '#content', swap: 'innerHTML'});
        }
        break;
    }
  });
}

// ---------------------------------------------------------------------------
// Auto-dismiss flash messages (M2/M3)
// ---------------------------------------------------------------------------

function initFlashDismiss() {
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => {
      el.style.transition = 'opacity 0.5s ease';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 500);
    }, 4000);
  });
}

// Auto-dismiss sync status (M2)
function initSyncDismiss() {
  const status = document.getElementById('sync-status');
  if (status && status.textContent.trim()) {
    setTimeout(() => {
      status.style.transition = 'opacity 0.5s ease';
      status.style.opacity = '0';
      setTimeout(() => { status.innerHTML = ''; status.style.opacity = '1'; }, 500);
    }, 4000);
  }
}

// ---------------------------------------------------------------------------
// Global HTMX loading indicator (H3)
// ---------------------------------------------------------------------------

function initGlobalLoading() {
  document.body.addEventListener('htmx:beforeRequest', () => {
    document.getElementById('global-loading')?.classList.add('active');
  });
  document.body.addEventListener('htmx:afterRequest', () => {
    document.getElementById('global-loading')?.classList.remove('active');
  });
}

// ---------------------------------------------------------------------------
// Initialize
// ---------------------------------------------------------------------------

function initAll() {
  initAvatars();
  initSwipe();
  initSwipeHint();
  initFlashDismiss();
  initSyncDismiss();
}

// On HTMX swap
document.addEventListener('htmx:afterSwap', () => {
  initAll();
});

// On load
document.addEventListener('DOMContentLoaded', () => {
  initAll();
  initKeyboard();
  initGlobalLoading();
});
