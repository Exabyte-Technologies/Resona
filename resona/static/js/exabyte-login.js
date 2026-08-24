document.querySelectorAll('.exabyte-sign-in[data-loading-text]').forEach(link => {
  link.addEventListener('click', () => {
    link.classList.add('is-loading');
    link.setAttribute('aria-disabled', 'true');
    const label = link.querySelector('span');
    if (label) label.textContent = link.dataset.loadingText;
  }, { once:true });
});
