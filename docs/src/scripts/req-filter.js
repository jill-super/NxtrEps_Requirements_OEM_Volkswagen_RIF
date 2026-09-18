// Per-page requirement filter behaviour (progressive enhancement).
// Loaded globally via `head` in astro.config.mjs; the matching
// `.req-filter` markup is emitted by tools/rif_to_mdx.py on every page.
(function () {
  function init() {
    var q = document.getElementById('req-q');
    if (!q || q.dataset.reqFilterInit) return;
    q.dataset.reqFilterInit = '1';
    var t = document.getElementById('req-t');
    var c = document.getElementById('req-count');
    var cards = Array.prototype.slice.call(document.querySelectorAll('.req-card'));
    function apply() {
      var needle = (q.value || '').toLowerCase();
      var ty = t ? t.value : '';
      var vis = 0;
      cards.forEach(function (el) {
        var hay = (el.getAttribute('data-search') || '').toLowerCase();
        var okT = !ty || el.getAttribute('data-type') === ty;
        var okQ = !needle || hay.indexOf(needle) >= 0;
        var show = okT && okQ;
        el.style.display = show ? '' : 'none';
        if (show) vis++;
      });
      if (c) c.textContent = vis + ' / ' + cards.length;
    }
    q.addEventListener('input', apply);
    if (t) t.addEventListener('change', apply);
    apply();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  // Re-init after Starlight client-side navigation.
  document.addEventListener('astro:page-load', init);
})();
