import { NextResponse, type NextRequest } from "next/server";

// Optimistic gate only: it checks that a session cookie exists. The API enforces auth.
const PUBLIC = new Set(["/login", "/register"]);

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const signedIn = request.cookies.has("cp_access") || request.cookies.has("cp_refresh");

  // The landing page is public; signed-in users skip it.
  if (pathname === "/") {
    return signedIn ? NextResponse.redirect(new URL("/dashboard", request.url)) : NextResponse.next();
  }
  if (PUBLIC.has(pathname)) {
    return signedIn ? NextResponse.redirect(new URL("/dashboard", request.url)) : NextResponse.next();
  }
  if (!signedIn) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  // Everything except the API proxy, media, Next internals and static files.
  matcher: ["/((?!api|media|_next|favicon.ico|.*\\..*).*)"],
};
