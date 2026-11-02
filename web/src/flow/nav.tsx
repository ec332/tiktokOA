import { createContext, useContext, type ReactNode } from "react";

/** Cross-links on the run page: a link names a step and an anchor; the run page opens that
 *  step's details, scrolls to the anchor and flashes it. */
export type Target = { step: string; anchor: string };
const Ctx = createContext<(t: Target) => void>(() => {});
export const NavProvider = Ctx.Provider;

export const STEP = {
  understand: "Understand the feature",
  clarify: "Clarify",
  design: "Design and approve tests",
  build: "Build and check",
  report: "Report",
} as const;

/** TC-O11-1.a (a boundary point) -> TC-O11-1 (the test as approved). */
export const unitOf = (caseId: string) => caseId.split(".")[0];

function Go({ to, title, children }: { to: Target; title: string; children: ReactNode }) {
  const go = useContext(Ctx);
  return (
    <a href={`#${to.anchor}`} title={title} onClick={(e) => { e.preventDefault(); go(to); }}>
      {children}
    </a>
  );
}

export function ReqLink({ id, children }: { id: string; children?: ReactNode }) {
  return <Go to={{ step: STEP.understand, anchor: `req-${id}` }} title={`Requirement ${id}`}>{children ?? id}</Go>;
}

/** The test as designed and approved. */
export function TestLink({ caseId, children }: { caseId: string; children?: ReactNode }) {
  return <Go to={{ step: STEP.design, anchor: `test-${unitOf(caseId)}` }} title={`Test ${unitOf(caseId)}`}>{children ?? caseId}</Go>;
}

/** The test's gate and run result. */
export function ResultLink({ caseId, children }: { caseId: string; children?: ReactNode }) {
  return <Go to={{ step: STEP.build, anchor: `run-${caseId}` }} title={`Results for ${caseId}`}>{children ?? caseId}</Go>;
}
