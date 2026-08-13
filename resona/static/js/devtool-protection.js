(() => {
  if (typeof window.DisableDevtool !== 'function') return;

  window.DisableDevtool({
    disableMenu: true,
    disableSelect: false,
    disableInputSelect: false,
    disableCopy: false,
    disableCut: false,
    disablePaste: false,
    clearLog: false,
    disableIframeParents: true,
  });
})();
