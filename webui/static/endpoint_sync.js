// Keeps the registered endpoints current on the pages that offer their models.
//
// The Episode and Campaign forms get the endpoints in window.CATALOG when they
// are served, so a model saved on the Models page afterwards would only appear
// on reload. The Models page announces every save and delete on a
// BroadcastChannel, and a page following the endpoints asks /api/endpoints again
// when it hears one -- and when it is shown again, because a page restored from
// the back-forward cache, or frozen in a background tab, missed the message.

const ENDPOINTS_CHANNEL = "BroadcastChannel" in window
  ? new BroadcastChannel("ibn-webui-endpoints")
  : null;

function announceEndpointsChanged() {
  if (ENDPOINTS_CHANNEL) ENDPOINTS_CHANNEL.postMessage("changed");
}

// Hand `render` the endpoints each time they differ from the ones on the page.
function followEndpoints(current, render) {
  let shown = JSON.stringify(current);
  let asked = 0;
  async function reload() {
    const ticket = ++asked;
    let endpoints;
    try {
      const response = await fetch("/api/endpoints", { cache: "no-store" });
      if (!response.ok) return;
      endpoints = (await response.json()).endpoints;
    } catch {
      // The server is restarting: the page keeps the list it has.
      return;
    }
    // A later reload was asked while this one waited; its answer is the one drawn.
    if (ticket !== asked) return;
    const text = JSON.stringify(endpoints);
    if (text === shown) return;
    shown = text;
    render(endpoints);
  }
  if (ENDPOINTS_CHANNEL) ENDPOINTS_CHANNEL.addEventListener("message", reload);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") reload();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) reload();
  });
}
