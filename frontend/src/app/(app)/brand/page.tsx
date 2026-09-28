"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import type { Brand } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { BrandForm, brandToForm, formToPayload, validateBrandForm, type BrandFormValues } from "@/components/brand-form";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export default function BrandPage() {
  const { brand } = useBrand();
  if (!brand) return null;
  // Remount the editor when the selected brand changes so the form starts from that brand's data.
  return <BrandEditor key={brand.id} brand={brand} />;
}

function BrandEditor({ brand }: { brand: Brand }) {
  const { brands, setBrandId } = useBrand();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [values, setValues] = useState<BrandFormValues>(() => brandToForm(brand));
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = useMutation({
    mutationFn: () => api.patch<Brand>(`/api/v1/brands/${brand.id}`, formToPayload(values, "update")),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["brands"] });
      toast.success("Brand kit saved.");
    },
    onError: (e) => setError(errorMessage(e)),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/brands/${brand.id}`),
    onSuccess: async () => {
      setConfirmDelete(false);
      await queryClient.invalidateQueries({ queryKey: ["brands"] });
      const next = brands.find((b) => b.id !== brand.id);
      if (next) setBrandId(next.id);
      toast.success("Brand deleted.");
      router.push(next ? "/dashboard" : "/onboarding");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Brand kit" description="Everything the AI knows about this brand: voice, audience, goals, pillars and visual identity." />
      <Card>
        <CardContent className="pt-6">
          <BrandForm
            values={values}
            onChange={setValues}
            onSubmit={() => {
              const problem = validateBrandForm(values);
              setError(problem);
              if (!problem) save.mutate();
            }}
            submitLabel="Save changes"
            submitting={save.isPending}
            error={error}
          />
        </CardContent>
      </Card>
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle>Delete this brand</CardTitle>
          <CardDescription>Removes the brand with all its sources, research, opportunities, posts and connections. This cannot be undone.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="destructive" onClick={() => setConfirmDelete(true)}>
            Delete {brand.name}
          </Button>
        </CardContent>
      </Card>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {brand.name}?</DialogTitle>
            <DialogDescription>All content for this brand will be permanently removed. Published posts stay on the social networks.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => remove.mutate()} disabled={remove.isPending}>
              {remove.isPending ? "Deleting…" : "Delete brand"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
