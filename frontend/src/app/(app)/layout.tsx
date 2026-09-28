"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { AppShell } from "@/components/app-shell";
import { BrandProvider, useBrand } from "@/lib/brand";

function RequireBrand({ children }: { children: ReactNode }) {
  const { brands, isLoading } = useBrand();
  const router = useRouter();
  const pathname = usePathname();
  const needsBrand = !isLoading && brands.length === 0 && pathname !== "/onboarding";

  useEffect(() => {
    if (needsBrand) router.replace("/onboarding");
  }, [needsBrand, router]);

  if (needsBrand) return null;
  return <>{children}</>;
}

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <BrandProvider>
      <AppShell>
        <RequireBrand>{children}</RequireBrand>
      </AppShell>
    </BrandProvider>
  );
}
