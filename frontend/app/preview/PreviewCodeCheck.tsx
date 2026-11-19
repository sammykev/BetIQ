"use client";

import { AppShell } from "@/components/shell/AppShell";
import { CodeCheck } from "@/components/CodeCheck";

export function PreviewCodeCheck() {
  return (
    <AppShell>
      <div className="space-y-6">
        <CodeCheck initialCode="TEST01" />
      </div>
    </AppShell>
  );
}
