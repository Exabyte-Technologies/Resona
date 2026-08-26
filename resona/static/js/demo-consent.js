(() => {
  const identity = document.querySelector('input[name="identity"]');
  const panel = document.querySelector('#demo-legal-consent');
  if (!identity || !panel) return;
  const checks = [...panel.querySelectorAll('input[type="checkbox"]')];
  const synchronize = () => {
    const demo = identity.value.trim().toLowerCase() === 'demo';
    panel.hidden = !demo;
    checks.forEach((input) => {
      input.disabled = !demo;
      input.required = demo;
      if (!demo) input.checked = false;
    });
  };
  identity.addEventListener('input', synchronize);
  identity.form?.addEventListener('submit', synchronize);
  synchronize();
})();
