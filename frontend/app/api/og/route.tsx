import { ImageResponse } from "next/og";
import { ShareCard, brandFonts } from "@/lib/brand";

export const revalidate = 86400; // rebuilt daily (the font is fetched when it renders)

export async function GET() {
  return new ImageResponse(<ShareCard />, { width: 1200, height: 630, fonts: await brandFonts() });
}
