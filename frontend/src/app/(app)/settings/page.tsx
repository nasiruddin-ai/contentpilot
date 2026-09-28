"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import { useBrand } from "@/lib/brand";
import { formatDate } from "@/lib/format";
import { useLogout, useMe } from "@/lib/hooks";
import { label, PUBLISHABLE_PLATFORMS, type SocialAccount } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const NOTES: Record<string, string> = {
  facebook: "Publishes to a Facebook Page you manage. You will be asked to pick the Page and grant posting and insights permissions.",
  linkedin: "Publishes to your LinkedIn profile. Requires an approved LinkedIn app.",
  x: "Publishes to an X account. X charges for its posting API, so this is usually off on a free setup.",
};

function Connections() {
  const { brandId, brand } = useBrand();
  const queryClient = useQueryClient();
  const { data: accounts = [] } = useQuery({
    queryKey: ["social", brandId],
    queryFn: () => api.get<SocialAccount[]>("/api/v1/social/accounts", { brand_id: brandId }),
    enabled: !!brandId,
    refetchOnWindowFocus: true,
  });
  const connect = useMutation({
    mutationFn: (platform: string) => api.post<{ authorization_url: string }>(`/api/v1/social/${platform}/connect`, { brand_id: brandId }),
    onSuccess: ({ authorization_url }) => {
      window.open(authorization_url, "_blank", "noopener");
      toast.info("Finish the connection in the new tab, then come back here.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const disconnect = useMutation({
    mutationFn: (platform: string) => api.post<void>(`/api/v1/social/${platform}/disconnect`, { brand_id: brandId }),
    onSuccess: () => {
      toast.success("Disconnected.");
      queryClient.invalidateQueries({ queryKey: ["social", brandId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Connected accounts</CardTitle>
        <CardDescription>Where {brand?.name ?? "this brand"} publishes. Tokens are stored encrypted and never shown.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col divide-y">
        {PUBLISHABLE_PLATFORMS.map((platform) => {
          const account = accounts.find((a) => a.platform === platform);
          return (
            <div key={platform} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{label(platform)}</span>
                  {account ? <StatusBadge status={account.status} /> : null}
                </div>
                <p className="text-sm text-muted-foreground">
                  {account
                    ? `Connected as ${account.account_name}${account.token_expires_at ? ` · token expires ${formatDate(account.token_expires_at)}` : ""}`
                    : NOTES[platform]}
                </p>
              </div>
              {account ? (
                <>
                  {account.status === "reconnect_required" ? (
                    <Button size="sm" onClick={() => connect.mutate(platform)}>
                      Reconnect
                    </Button>
                  ) : null}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      if (window.confirm(`Disconnect ${label(platform)}? Scheduled posts for it will fail until reconnected.`)) disconnect.mutate(platform);
                    }}
                  >
                    Disconnect
                  </Button>
                </>
              ) : (
                <Button size="sm" onClick={() => connect.mutate(platform)} disabled={connect.isPending}>
                  Connect
                </Button>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const { data: me } = useMe();
  const logout = useLogout();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Settings" description="Your account and the social accounts this brand publishes to." />
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Account</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-4 text-sm">
          <div className="flex-1">
            <p className="font-medium">{me?.name}</p>
            <p className="text-muted-foreground">{me?.email}</p>
            {me ? <p className="text-xs text-muted-foreground">Member since {formatDate(me.created_at)}</p> : null}
          </div>
          <Button variant="outline" onClick={() => logout.mutate()}>
            Sign out
          </Button>
        </CardContent>
      </Card>
      <Connections />
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Billing</CardTitle>
          <CardDescription>Plans and payments are not set up yet. Everything runs on the free tier for now.</CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}
