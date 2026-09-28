"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import {
  BarChart3,
  Bell,
  BookOpen,
  CalendarDays,
  Inbox,
  ChevronsUpDown,
  FileText,
  LayoutDashboard,
  Lightbulb,
  LogOut,
  Menu,
  Palette,
  Plane,
  Rss,
  Settings,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useBrand } from "@/lib/brand";
import { useLogout, useMe, useUnreadCount } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/research", label: "Research", icon: BookOpen },
  { href: "/opportunities", label: "Opportunities", icon: Lightbulb },
  { href: "/content", label: "Content", icon: FileText },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/inbox", label: "Inbox", icon: Inbox },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/autopilot", label: "Autopilot", icon: Plane },
  { href: "/sources", label: "Sources", icon: Rss },
  { href: "/brand", label: "Brand kit", icon: Palette },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

function BrandSwitcher() {
  const { brands, brand, setBrandId } = useBrand();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="outline" className="w-full justify-between">
            <span className="truncate">{brand?.name ?? (brands.length ? "Choose a brand" : "No brand yet")}</span>
            <ChevronsUpDown className="size-4 text-muted-foreground" />
          </Button>
        }
      />
      <DropdownMenuContent className="w-56" align="start">
        <DropdownMenuLabel>Brands</DropdownMenuLabel>
        {brands.map((b) => (
          <DropdownMenuItem key={b.id} onClick={() => setBrandId(b.id)}>
            {b.name}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem render={<Link href="/onboarding" />}>Add a brand</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-1">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="size-4" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

function UserMenu() {
  const { data: me } = useMe();
  const logout = useLogout();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" className="w-full justify-start gap-2 px-2">
            <span className="flex size-7 items-center justify-center rounded-full bg-muted text-xs font-semibold uppercase">
              {me?.name?.slice(0, 1) ?? "?"}
            </span>
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate text-sm">{me?.name ?? "…"}</span>
              <span className="block truncate text-xs text-muted-foreground">{me?.email ?? ""}</span>
            </span>
          </Button>
        }
      />
      <DropdownMenuContent className="w-56" align="start">
        <DropdownMenuItem render={<Link href="/settings" />}>Settings</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => logout.mutate()} variant="destructive">
          <LogOut className="size-4" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function NotificationsButton() {
  const { data } = useUnreadCount();
  const unread = data?.unread ?? 0;
  return (
    <Button variant="ghost" size="icon" nativeButton={false} render={<Link href="/notifications" aria-label="Notifications" />}>
      <span className="relative">
        <Bell className="size-4" />
        {unread > 0 ? (
          <span className="absolute -top-1.5 -right-2 min-w-4 rounded-full bg-primary px-1 text-[10px] font-semibold leading-4 text-primary-foreground">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </span>
    </Button>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  const sidebar = (
    <div className="flex h-full flex-col gap-4 p-4">
      <Link href="/dashboard" className="flex items-center gap-2 px-1 text-base font-semibold" onClick={() => setOpen(false)}>
        <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">CP</span>
        ContentPilot
      </Link>
      <BrandSwitcher />
      <SidebarNav onNavigate={() => setOpen(false)} />
      <div className="mt-auto border-t pt-3">
        <UserMenu />
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 border-r bg-card lg:block">{sidebar}</aside>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 bg-card shadow-xl">{sidebar}</aside>
        </div>
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-2 border-b bg-card px-4">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setOpen((v) => !v)} aria-label="Menu">
            {open ? <X className="size-4" /> : <Menu className="size-4" />}
          </Button>
          <div className="flex-1" />
          <NotificationsButton />
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
