"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import type { Brand } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { BrandForm, emptyBrandForm, formToPayload, validateBrandForm } from "@/components/brand-form";

export default function OnboardingPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { brands, setBrandId } = useBrand();
  const [values, setValues] = useState(emptyBrandForm());
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.post<Brand>("/api/v1/brands", formToPayload(values, "create")),
    onSuccess: async (brand) => {
      await queryClient.invalidateQueries({ queryKey: ["brands"] });
      setBrandId(brand.id);
      toast.success(`${brand.name} created. Next: add a source to research from.`);
      router.push("/sources");
    },
    onError: (e) => setError(errorMessage(e)),
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={brands.length ? "Add a brand" : "Set up your first brand"}
        description="Tell ContentPilot who you are, who you write for and what you want content to do. You can change all of this later."
      />
      <Card>
        <CardContent className="pt-6">
          <BrandForm
            values={values}
            onChange={setValues}
            onSubmit={() => {
              const problem = validateBrandForm(values);
              setError(problem);
              if (!problem) create.mutate();
            }}
            submitLabel="Create brand"
            submitting={create.isPending}
            error={error}
            showKit={false}
          />
        </CardContent>
      </Card>
    </div>
  );
}
