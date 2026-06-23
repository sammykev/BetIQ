import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Routes that must remain public (no Clerk session required)
const isPublic = createRouteMatcher([
  "/api/og",
  "/api/og(.*)",
]);

export default clerkMiddleware(async (auth, req) => {
  // Do nothing for public routes — let them pass through unauthenticated
  if (isPublic(req)) return;
});

export const config = {
  matcher: [
    "/((?!_next|opengraph-image|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
