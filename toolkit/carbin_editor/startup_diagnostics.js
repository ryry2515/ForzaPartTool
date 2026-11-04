// Surface a failed renderer/module startup instead of leaving the status at "ready".
window.editorErrors = [];
function reportStartupError(error) {
  const message = error?.stack || error?.message || String(error);
  window.editorErrors.push(message);
  const status = document.querySelector('#status');
  if (status) { status.textContent = message; status.className = 'err'; }
}
addEventListener('error', event => reportStartupError(event.error || event.message));
addEventListener('unhandledrejection', event => reportStartupError(event.reason));
