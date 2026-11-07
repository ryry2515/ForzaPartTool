// Surface a failed renderer/module startup instead of leaving the status at "ready".
window.editorErrors = [];
function reportStartupError(error) {
  const message = error?.stack || error?.message || String(error);
  window.editorErrors.push(message);
  const status = document.querySelector('#status');
  if (status) { status.textContent = message; status.className = 'err'; }
}
addEventListener('error', event => {
  if (/ResizeObserver loop/.test(event.message || '')) return;     // a harmless browser notice when a panel is resized, not a failure
  reportStartupError(event.error || event.message);
});
addEventListener('unhandledrejection', event => reportStartupError(event.reason));
