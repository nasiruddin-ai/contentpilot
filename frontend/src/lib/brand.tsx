"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Brand } from "@/lib/types";

type BrandContextValue = {
  brands: Brand[];
  brand: Brand | null;
  brandId: string | null;
  setBrandId: (id: string) => void;
  isLoading: boolean;
};

const BrandContext = createContext<BrandContextValue | null>(null);
const STORAGE_KEY = "contentpilot.brandId";

function readStored(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function BrandProvider({ children }: { children: ReactNode }) {
  const { data: brands = [], isLoading } = useQuery({ queryKey: ["brands"], queryFn: () => api.get<Brand[]>("/api/v1/brands") });
  // Server renders with null; the client starts from the remembered choice. Nothing brand-specific is
  // rendered until the brands query resolves on the client, so the two initial renders still match.
  const [chosen, setChosen] = useState<string | null>(readStored);

  // The remembered brand if it still exists, otherwise the first brand.
  const brandId = brands.some((b) => b.id === chosen) ? chosen : (brands[0]?.id ?? null);

  const value = useMemo<BrandContextValue>(
    () => ({
      brands,
      brandId,
      brand: brands.find((b) => b.id === brandId) ?? null,
      isLoading,
      setBrandId: (id: string) => {
        setChosen(id);
        try {
          window.localStorage.setItem(STORAGE_KEY, id);
        } catch {
          // Storage may be unavailable; the selection still works for this page load.
        }
      },
    }),
    [brands, brandId, isLoading],
  );

  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

export function useBrand(): BrandContextValue {
  const context = useContext(BrandContext);
  if (!context) throw new Error("useBrand must be used inside BrandProvider");
  return context;
}
