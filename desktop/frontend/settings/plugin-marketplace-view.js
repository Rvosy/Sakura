export const marketplaceMarkup = `
<section id="market-surface" class="market-page" hidden>
      <div class="market-tools">
        <label class="market-search"><span class="sakura-icon icon-search" aria-hidden="true"></span><input id="search" type="search" placeholder="搜索名称、作者或功能" aria-label="搜索插件" autocomplete="off"><kbd>/</kbd></label>
        <button id="market-filter" class="market-filter-trigger secondary-button" type="button" aria-expanded="false" aria-controls="market-filter-panel" popovertarget="market-filter-panel">筛选</button>
        <button id="market-submit" class="market-submit-button primary-button" type="button" aria-haspopup="dialog" aria-controls="market-submit-dialog"><span class="sakura-icon icon-circle-arrow-up" aria-hidden="true"></span>上传插件</button>
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
  <dialog id="market-submit-dialog" class="market-drawer market-submit-dialog" aria-labelledby="market-submit-title" aria-describedby="market-submit-description">
    <div class="drawer-top"><h2 id="market-submit-title">上传插件</h2><button class="icon-button" type="button" data-close-submit aria-label="关闭"><span class="sakura-icon icon-x" aria-hidden="true"></span></button></div>
    <div class="market-submit-content"><p id="market-submit-description">请先将插件源码上传到你自己的公开 GitHub 仓库，再打开 Issue 页面，填写插件 ID、仓库地址和版本信息，提交收录申请。</p></div>
    <div class="drawer-bottom"><div class="detail-buttons"><button class="primary-button" type="button" id="market-submit-issue">打开 Issue 页面<span class="sakura-icon icon-external-link" aria-hidden="true"></span></button></div></div>
  </dialog>
`;
