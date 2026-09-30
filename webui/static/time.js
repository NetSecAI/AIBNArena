// Every date this interface shows, in the lab's time zone. What is stored stays
// UTC with its offset -- the run records, the seed register, the run ids -- and
// only what a person reads is converted, so a record and the page never
// disagree about an instant. The zone is named rather than taken from the
// browser, so a page opened from a machine set to UTC still reads in lab time.
const DISPLAY_TIME_ZONE = "Europe/Paris";

const DISPLAY_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: DISPLAY_TIME_ZONE,
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  timeZoneName: "short",
});

// "2026-09-23T07:10:37+00:00" -> "2026-09-23 09:10:37 CEST". Anything that does
// not parse is shown as it came rather than as an invalid date.
function displayTime(iso) {
  const instant = new Date(iso);
  if (!iso || Number.isNaN(instant.getTime())) return iso || "";
  const part = Object.fromEntries(
    DISPLAY_TIME.formatToParts(instant).map((item) => [item.type, item.value]));
  return `${part.year}-${part.month}-${part.day} `
    + `${part.hour}:${part.minute}:${part.second} ${part.timeZoneName}`;
}

// The dates a template writes as <time datetime="...">, the UTC value on hover.
for (const element of document.querySelectorAll("time[datetime]")) {
  element.title = element.getAttribute("datetime");
  element.textContent = displayTime(element.getAttribute("datetime"));
}
