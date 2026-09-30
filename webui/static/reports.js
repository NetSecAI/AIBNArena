// The files the judge and the campaigns write under reports/, which the server
// serves at /reports/. A path written elsewhere (a result directory outside the
// repository) stays text: there is nothing here to link it to.
function reportLink(path, text) {
  const label = text || path;
  if (path && path.startsWith("reports/")) {
    return Object.assign(document.createElement("a"), { href: `/${path}`, textContent: label });
  }
  return Object.assign(document.createElement("code"), { textContent: label });
}
