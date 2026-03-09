/* === Eidos Mail — Client JS === */

// Service worker registration
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js').catch(() => {});
}

// Avatar color from string
function avatarColor(s) {
  const colors = ['#ef4444','#f59e0b','#22c55e','#3b82f6','#8b5cf6','#ec4899','#14b8a6','#f97316'];
  let h = 0;
  for (let i = 0; i < s.length; i++) h = s.charCodeAt(i) + ((h << 5) - h);
  return colors[Math.abs(h) % colors.length];
}

// Extract initials from email/name
function initials(from) {
  if (!from) return '?';
  // "Name <email>" format
  const m = from.match(/^([^<]+)</);
  const name = m ? m[1].trim() : from.split('@')[0];
  const parts = name.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length-1][0]).toUpperCase();
  return name.substring(0, 2).toUpperCase();
}

// Set avatar colors on load
function initAvatars() {
  document.querySelectorAll('.avatar[data-from]').forEach(el => {
    const from = el.dataset.from;
    el.style.backgroundColor = avatarColor(from);
    if (!el.textContent.trim()) el.textContent = initials(from);
  });
}

// Swipe handling for email items
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
      // Clamp
      currentX = Math.max(-120, Math.min(120, currentX));
      content.style.transform = `translateX(${currentX}px)`;
      // Show swipe backgrounds
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
            fetch(`/undelete/${id}`, { method: 'POST' }).then(() => {
              el.style.maxHeight = el.offsetHeight + 'px';
              el.style.overflow = 'hidden';
              el.style.transition = 'max-height 0.3s ease, padding 0.3s ease';
              el.style.maxHeight = '0';
              el.style.padding = '0';
              setTimeout(() => el.remove(), 300);
            });
          }, 150);
        } else {
          // Swipe right = mark read
          setTimeout(() => {
            fetch('/mark-read', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ids: [parseInt(id)], read: true })
            }).then(() => {
              el.classList.remove('unread');
              content.style.transform = 'translateX(0)';
            });
          }, 150);
        }
      } else if (currentX < -80 && !isTrash) {
        // Swipe left = delete (not in trash)
        content.style.transform = 'translateX(-100%)';
        el.style.maxHeight = el.offsetHeight + 'px';
        setTimeout(() => {
          fetch(`/delete/${id}`, { method: 'POST' }).then(() => {
            el.style.maxHeight = '0';
            el.style.overflow = 'hidden';
            el.style.padding = '0';
            el.style.borderBottom = 'none';
            el.style.transition = 'max-height 0.3s ease, padding 0.3s ease';
            setTimeout(() => el.remove(), 300);
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

// Quick action buttons (mark read, delete) without swipe
function quickAction(action, id, btn) {
  const item = btn.closest('.email-item');
  if (action === 'read') {
    fetch('/mark-read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [id], read: true })
    }).then(() => item?.classList.remove('unread'));
  } else if (action === 'delete') {
    fetch(`/delete/${id}`, { method: 'POST' }).then(() => {
      if (item) {
        item.style.maxHeight = item.offsetHeight + 'px';
        item.style.overflow = 'hidden';
        item.style.transition = 'max-height 0.3s ease, padding 0.3s ease, opacity 0.3s';
        item.style.opacity = '0';
        item.style.maxHeight = '0';
        item.style.padding = '0';
        setTimeout(() => item.remove(), 300);
      }
    });
  } else if (action === 'undelete') {
    fetch(`/undelete/${id}`, { method: 'POST' }).then(() => {
      if (item) {
        item.style.maxHeight = item.offsetHeight + 'px';
        item.style.overflow = 'hidden';
        item.style.transition = 'max-height 0.3s ease, padding 0.3s ease, opacity 0.3s';
        item.style.opacity = '0';
        item.style.maxHeight = '0';
        item.style.padding = '0';
        setTimeout(() => item.remove(), 300);
      }
    });
  }
}

// Initialize on HTMX swap
document.addEventListener('htmx:afterSwap', () => {
  initAvatars();
  initSwipe();
});

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
  initAvatars();
  initSwipe();
});
