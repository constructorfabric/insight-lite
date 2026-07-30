import type { ReactNode } from "react";

// One collapsible section of a report view, stating the question it answers and who
// asks it.
//
// Flow was the first page to need this: an undivided scroll of everything known about
// one subject, which read
// as "for everybody", i.e. for nobody: two readers after different things got the
// same wall. Naming the question is most of the fix; collapsing everything except
// what answers "what do I do now" is the rest.
//
// Deliberately NOT nav: a section is part of a page, and the sidebar's pane holds
// routes. Mixing "go to another page" and "reveal part of this one" into one
// identically-styled list is the sort of bespoke pattern this codebase avoids.
//
// `open` is the INITIAL state only. React writes the attribute on mount and, because
// the value never changes between renders, leaves it alone afterwards — so a reader's
// own expand/collapse survives a period or scope change.
export default function Section({ q, who, open, children }: {
  q: string; who: string; open?: boolean; children: ReactNode;
}) {
  return (
    <details className="flow-sec" open={open}>
      <summary>
        <span className="fs-q">{q}</span>
        <span className="fs-who">{who}</span>
      </summary>
      <div className="fs-body">{children}</div>
    </details>
  );
}
