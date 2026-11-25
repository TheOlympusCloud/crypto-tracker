import tracker from "../../../public/locales/en/tracker.json";
import portfolio from "../../../public/locales/en/tracker-portfolio.json";
import graph from "../../../public/locales/en/tracker-graph.json";
import lookup from "../../../public/locales/en/tracker-lookup.json";
import manage from "../../../public/locales/en/tracker-manage.json";

/** The wallet tracker's namespaces, each a file per language in public/locales/<lang>/. English is bundled like common. */
export const TRACKER_EN = {
  tracker,
  "tracker-portfolio": portfolio,
  "tracker-graph": graph,
  "tracker-lookup": lookup,
  "tracker-manage": manage,
};
export const TRACKER_NS = Object.keys(TRACKER_EN);
