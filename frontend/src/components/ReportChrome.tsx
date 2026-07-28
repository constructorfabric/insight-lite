// Report-view chrome: the floating metrics-assistant chat (its own component) plus
// the drill-down modal + click-to-sort + hover-tooltip behaviours (installed as document-delegated
// effects). Mounted by the report-chrome entry on report_chrome pages, replacing
// the old shell.CHAT_WIDGET_JS + DRILL_JS + SORT_JS injected <script> blocks.
import { useEffect } from "react";
import ChatWidget from "./ChatWidget";
import { installDrill, installMoreRows, installPersonNav, installSort, installTips } from "./reportChromeEffects";

export default function ReportChrome() {
  useEffect(() => {
    const drill = installDrill();
    const sort = installSort();
    const personNav = installPersonNav();
    const moreRows = installMoreRows();
    const tips = installTips();
    return () => {
      drill();
      sort();
      personNav();
      moreRows();
      tips();
    };
  }, []);
  return <ChatWidget />;
}
