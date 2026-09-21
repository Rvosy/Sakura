export const marketplaceMarkup = `
<section id="market-surface" class="market-page" hidden>
      <div class="market-tools">
        <label class="market-search"><span class="sakura-icon icon-search" aria-hidden="true"></span><input id="search" type="search" placeholder="搜索名称、作者或功能" aria-label="搜索插件" autocomplete="off"><kbd>/</kbd></label>
        <button id="market-filter" class="market-filter-trigger secondary-button" type="button" aria-expanded="false" aria-controls="market-filter-panel" popovertarget="market-filter-panel">筛选</button>
      </div>
      <div class="market-filters">
        <div id="categories" class="categories" aria-label="插件领域"></div>

      </div>
      <div id="notice" class="market-notice" role="status" hidden></div>
      <section id="content" class="market-content" role="tabpanel" aria-labelledby="market-tab" tabindex="0">
        <div id="catalog" class="catalog"></div>
      </section>
      <div class="market-foot"><div class="market-summary"><span id="result-count" aria-live="polite"></span><span id="catalog-state"></span></div><span>社区目录</span></div>
</section>
  <div id="market-filter-panel" class="market-filter-panel" popover="auto" aria-label="浏览筛选"><label class="compatibility"><input id="compatible" type="checkbox" checked><span>仅看兼容</span></label><label class="compatibility"><input id="hide-installed" type="checkbox"><span>隐藏已安装（有更新的除外）</span></label><label class="market-sort"><span>排序</span><select id="sort" aria-label="插件排序"><option value="default">默认顺序</option><option value="updated">最近更新</option><option value="name">名称</option></select></label></div>
  <button id="market-sources" class="market-menu-action" type="button" role="menuitem">下载源…</button>
  <button id="refresh" class="market-menu-action" type="button" role="menuitem">刷新目录</button>
  <dialog id="detail-dialog" class="market-drawer" aria-labelledby="detail-title"><div id="detail"></div></dialog>
`;
