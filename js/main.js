/* ==========================================================================
   BIRD CRIME — shared chrome (header/footer) + small utilities
   ========================================================================== */

const NAV_LINKS = [
  { href: "index.html", label: "Home" },
  { href: "crime-report.html", label: "Crime Report" },
  { href: "standings.html", label: "Standings" },
  { href: "managers.html", label: "Managers" },
  { href: "records.html", label: "Records" },
  { href: "transactions.html", label: "Transactions" },
  { href: "rules.html", label: "Rules" },
];

function renderHeader(activeHref) {
  const tabs = NAV_LINKS.map(link => {
    const active = link.href === activeHref ? " active" : "";
    return `<a class="tab${active}" href="${link.href}">${link.label}</a>`;
  }).join("");

  const html = `
    <div class="header-inner">
      <a class="brand" href="index.html">
        <span class="brand-mark">BC</span>
        <span class="brand-text">BIRD CRIME<small>Fantasy Football League</small></span>
      </a>
      <nav class="tabs" aria-label="Site sections">${tabs}</nav>
    </div>
  `;
  document.getElementById("site-header").innerHTML = html;
}

function renderFooter() {
  document.getElementById("site-footer").innerHTML = `
    <div class="wrap">
      <p>BIRD CRIME &middot; FANTASY FOOTBALL LEAGUE &middot; UPDATED LIVE</p>
    </div>
  `;
}

function initChrome(activeHref) {
  renderHeader(activeHref);
  renderFooter();
}

function showLoading(el, msg = "Loading league data…") {
  el.innerHTML = `<p class="loading-note">${msg}</p>`;
}

function showError(el, msg = "Couldn't reach the Sleeper API. Try refreshing, or check your connection.") {
  el.innerHTML = `<p class="error-note">${msg}</p>`;
}

function avatarUrl(avatarId) {
  return avatarId ? `https://sleepercdn.com/avatars/thumbs/${avatarId}` : null;
}
