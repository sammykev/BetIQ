import { clerkMiddleware } from "@clerk/nextjs/server";

export default clerkMiddleware();

export const config = {
  matcher: [
    // Exclude static files, OG image, and Next internals from Clerk middleware
    "/((?!_next|opengraph-image|twitter-image|favicon\\.svg|logo\\.svg|manifest\\.json|sw\\.js|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
