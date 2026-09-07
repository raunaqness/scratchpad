import type { ReactNode } from "react";

import { MyRuntimeProvider } from "@/app/MyRuntimeProvider";

export const dynamic = "force-dynamic";

export default function AppLayout({ children }: { children: ReactNode }) {
  return <MyRuntimeProvider>{children}</MyRuntimeProvider>;
}
