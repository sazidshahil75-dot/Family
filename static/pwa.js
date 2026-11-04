(function () {
  if ("serviceWorker" in navigator) {
    addEventListener("load", function () { navigator.serviceWorker.register("/sw.js").catch(function () {}); });
  }
  var standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (standalone) return;
  var deferred = null;
  function $(id) { return document.getElementById(id); }
  function show(text, withButton) {
    var box = $("installBox"); if (!box) return;
    $("installText").textContent = text;
    $("installBtn").hidden = !withButton;
    box.hidden = false;
  }
  addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault(); deferred = e;
    show("Install it to open full screen from your home screen, like any other app.", true);
  });
  addEventListener("appinstalled", function () { var b = $("installBox"); if (b) b.hidden = true; });
  document.addEventListener("DOMContentLoaded", function () {
    var btn = $("installBtn");
    if (btn) btn.onclick = function () { if (deferred) { deferred.prompt(); deferred = null; $("installBox").hidden = true; } };
    var ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (ios) show("On iPhone: tap the Share button in Safari, then Add to Home Screen. It then opens full screen like an app.", false);
  });
})();
