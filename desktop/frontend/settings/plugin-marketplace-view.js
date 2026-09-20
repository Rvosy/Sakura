export const marketplaceMarkup = `
<section id="market-surface" class="market-page" hidden>
      <div class="market-tools">
        <label class="market-search"><span class="sakura-icon icon-search" aria-hidden="true"></span><input id="search" type="search" placeholder="搜索名称、作者或功能" aria-label="搜索插件" autocomplete="off"><kbd>/</kbd></label>
        <label class="compatibility"><input id="compatible" type="checkbox" checked><span>仅看兼容</span></label>
        <button id="market-sources" class="secondary-button">下载源</button>
        <button id="refresh" class="icon-button" aria-label="刷新市场"><span class="sakura-icon icon-refresh-cw" aria-hidden="true"></span></button>
      </div>
      <div id="categories" class="categories" aria-label="插件领域"></div>
      <div id="notice" class="market-notice" role="status" hidden></div>
      <div class="results-line"><span id="result-count" aria-live="polite"></span><label>排序 <select id="sort" aria-label="插件排序"><option value="default">默认顺序</option><option value="updated">最近更新</option><option value="name">名称</option></select></label></div>
      <section id="content" class="market-content" role="tabpanel" aria-labelledby="market-tab" tabindex="0">
        <div id="catalog" class="catalog"></div>
      </section>
      <div class="market-foot"><span id="catalog-state"></span><span>社区目录</span></div>
</section>
  <dialog id="detail-dialog" class="market-drawer" aria-labelledby="detail-title"><div id="detail"></div></dialog>
`;
