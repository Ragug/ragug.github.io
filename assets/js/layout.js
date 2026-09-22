/**
 * layout.js
 * Single source of truth for the header nav and footer used on every page.
 * Add/remove a page ONLY here — never edit header/footer markup in the HTML files again.
 *
 * Usage in each HTML file:
 *   1. Replace the whole <header id="header">...</header> block with:
 *        <div id="site-header"></div>
 *   2. Replace <footer>...</footer> + #preloader + .back-to-top with:
 *        <div id="site-footer"></div>
 *   3. Include this script BEFORE assets/js/main.js:
 *        <script src="assets/js/layout.js"></script>
 *        <script src="assets/js/main.js"></script>
 *      (main.js binds the mobile-nav-toggle click handler on DOMContentLoaded,
 *      so layout.js must run first and inject the real header before it does.)
 */
(function () {
  "use strict";

  // ---- Add / remove / rename pages here. This is the only place. ----
  var PAGES = [
    { href: "index.html", label: "Home" },
    { href: "about.html", label: "About" },
    { href: "blog.html", label: "Blogs" },
    { href: "projects.html", label: "Projects" },
    { href: "products.html", label: "Products" },
    { href: "services.html", label: "Services" },
    { href: "tools.html", label: "Tools" },
    { href: "contact.html", label: "Contact" }
  ];

  var SOCIAL = [
    { href: "mailto:ragu19062002@gmail.com", icon: "bi-envelope-at", label: "Email" },
    { href: "http://api.whatsapp.com/send?phone=+917598226670&text=Hi Ragu", icon: "bi-whatsapp", label: "WhatsApp", newTab: true },
    { href: "tg://resolve?domain=ragu2k", icon: "bi-telegram", label: "Telegram" },
    { href: "https://www.instagram.com/_ragug_/", icon: "bi-instagram", label: "Instagram", newTab: true },
    { href: "https://www.linkedin.com/in/ragug/", icon: "bi-linkedin", label: "LinkedIn", newTab: true },
    { href: "https://github.com/Ragug/", icon: "bi-github", label: "GitHub", newTab: true }
  ];

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  /** Works out the current file name so the matching nav link gets .active — no more hardcoding it per page. */
  function currentPage() {
    var path = location.pathname.split("/").pop();
    return path && path !== "" ? path : "index.html";
  }

  function buildHeader() {
    var current = currentPage();

    var navItems = PAGES.map(function (p) {
      var isActive = p.href === current;
      var cls = isActive ? ' class="active"' : "";
      var aria = isActive ? ' aria-current="page"' : "";
      return '<li><a' + cls + aria + ' href="' + p.href + '">' + escapeHtml(p.label) + "</a></li>";
    }).join("");

    var socialLinks = SOCIAL.map(function (s) {
      var target = s.newTab ? ' target="_blank" rel="noopener"' : "";
      return '<a href="' + s.href + '"' + target + ' aria-label="' + escapeHtml(s.label) + '"><i class="bi ' + s.icon + '"></i></a>';
    }).join("");

    return (
      '<header id="header" class="fixed-top">' +
        '<div class="container-fluid d-flex justify-content-between align-items-center">' +
          '<p class="logo me-auto me-lg-0"><a href="index.html">RAGU</a></p>' +
          '<nav id="navbar" class="navbar order-last order-lg-0">' +
            "<ul>" + navItems + "</ul>" +
            '<i class="bi bi-list mobile-nav-toggle"></i>' +
          "</nav>" +
          '<div class="header-social-links">' + socialLinks + "</div>" +
        "</div>" +
      "</header>"
    );
  }

  function buildFooter() {
    var year = new Date().getFullYear();
    return (
      '<footer id="footer">' +
        '<div class="container">' +
          '<div class="copyright">&copy; Copyright ' + year + ' <strong><span>Ragu</span></strong>. All Rights Reserved</div>' +
        "</div>" +
      "</footer>" +
      '<div id="preloader"></div>' +
      '<a href="#" class="back-to-top d-flex align-items-center justify-content-center"><i class="bi bi-arrow-up-short"></i></a>'
    );
  }

  function mount() {
    var headerMount = document.getElementById("site-header");
    var footerMount = document.getElementById("site-footer");
    if (headerMount) headerMount.outerHTML = buildHeader();
    if (footerMount) footerMount.outerHTML = buildFooter();
  }

  // Run immediately, not on DOMContentLoaded. The #site-header / #site-footer
  // placeholder divs are already in the DOM by the time this script tag is
  // reached (it's placed after them in the HTML), and main.js — loaded right
  // after this file — looks for #preloader / .mobile-nav-toggle synchronously
  // the moment IT runs. If we wait for DOMContentLoaded, main.js runs first,
  // finds nothing, and silently never wires up the preloader removal or the
  // mobile nav toggle — which is exactly what caused the infinite "loading" bug.
  mount();
})();
