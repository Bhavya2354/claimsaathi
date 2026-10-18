import type { Metadata } from "next";
import { Suspense } from "react";

import { Workspace, WorkspaceSkeleton } from "@/components/workspace/Workspace";

export const metadata: Metadata = {
  title: "Your case · ClaimSaathi",
  robots: { index: false, follow: false },
};

// The case ID is only known at request time, so the client workspace renders inside Suspense.
export default function CasePage() {
  return (
    <Suspense fallback={<WorkspaceSkeleton />}>
      <Workspace />
    </Suspense>
  );
}
