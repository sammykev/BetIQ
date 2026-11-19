import { notFound } from "next/navigation";
import { PreviewCodeCheck } from "./PreviewCodeCheck";

// The code checker with sample data, for the browser tests' screenshots
// (e2e/pages.spec.ts). Only built in when NEXT_PUBLIC_PREVIEW is "1": the
// live site answers 404 here.
export default function PreviewPage() {
  if (process.env.NEXT_PUBLIC_PREVIEW !== "1") notFound();
  return <PreviewCodeCheck />;
}
