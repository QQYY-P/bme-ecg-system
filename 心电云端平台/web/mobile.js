'use strict';
// Mobile navigation is presentation only; account and record access stay on the server.
var MobileUI = (() => {
  const narrow = window.matchMedia('(max-width: 720px)');
  const shell = $('workspace-shell');
  let current = 'records';
  const scrollPositions = {records: 0, results: 0, account: 0};
  const buttons = ['records', 'results', 'account'];

  function paint() {
    shell.dataset.mobileView = current;
    for (const name of buttons) {
      const button = $('nav-' + name);
      if (name === current) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    $('nav-results').disabled = !state.selected;
  }
  function show(name, {load = true, focus = true} = {}) {
    if (!buttons.includes(name) || (name === 'results' && !state.selected)) return false;
    if (name !== 'results' && document.body?.classList.contains('wave-focus')) $('wave-focus').click();
    if (current !== name) {
      scrollPositions[current] = window.scrollY;
      current = name;
      paint();
      if (narrow.matches) {
        window.scrollTo({top: scrollPositions[name], behavior: 'instant'});
        if (focus) $(name === 'account' ? 'account-heading' : name + '-heading').focus({preventScroll: true});
      }
    }
    if (name === 'results' && load && state.ready) {
      if (!state.wave) detailFeedback('正在读取测量结果…');
      loadWave(); loadAnalysis();
    }
    requestAnimationFrame(draw);
    return true;
  }
  function selectionChanged() {
    scrollPositions.results = 0;
    if (!state.selected && current === 'results') show('records', {load: false, focus: false});
    paint();
  }
  function filtersChanged() {
    const active = Object.entries(state.filters).filter(([key, value]) => value && !(key === 'trashed' && value === 'false'));
    text('filter-summary', active.length ? '已设 ' + active.length + ' 项条件' : '全部记录');
    if (narrow.matches) $('filter-panel').open = false;
  }
  function reset() {
    current = 'records';
    for (const key of buttons) scrollPositions[key] = 0;
    paint(); filtersChanged();
  }
  for (const name of buttons) $('nav-' + name).onclick = () => show(name);
  $('back-records').onclick = () => show('records');
  $('account-install').onclick = () => $('pwa-install').click();
  const installed = window.matchMedia('(display-mode: standalone)');
  const syncInstall = () => {$('account-install').hidden = installed.matches || window.navigator.standalone === true;};
  installed.addEventListener('change', syncInstall);
  window.addEventListener('appinstalled', () => {$('account-install').hidden = true;});
  narrow.addEventListener('change', () => {
    $('filter-panel').open = !narrow.matches;
    paint();
    if (state.ready && (!narrow.matches || current === 'results')) {loadWave(); loadAnalysis();}
    requestAnimationFrame(draw);
  });
  $('filter-panel').open = !narrow.matches;
  syncInstall(); paint();
  return {
    reset, selectionChanged, filtersChanged,
    needsDetail: () => !narrow.matches || current === 'results',
    openResult: () => narrow.matches && show('results'),
  };
})();
