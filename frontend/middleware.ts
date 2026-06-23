import { clerkMiddleware } from "@clerk/nextjs/server";

export default clerkMiddleware();

export const config = {
  // Only run Clerk on page routes and specific API routes — never on /api/og
  matcher: [
    "/((?!_next|api/og|opengraph-image|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|webmanifest)).*)",
    "/api/subscribe(.*)",
    "/api/chat(.*)",
  ],
};
